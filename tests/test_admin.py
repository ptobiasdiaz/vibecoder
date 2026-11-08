from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vibecoder.admin import (
    AdministrationService, AdministrativeAuthorizationError, FileSecretStore, main,
)


class AdministrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspaces"; self.workspace.mkdir()
        self.account_file = self.root / "accounts.json"; self.account_file.write_text('{"accounts": {}}\n')
        self.config = self.root / "supervisor.json"
        self.config.write_text(json.dumps({
            "database": str(self.root / "state.db"), "accounts": str(self.account_file),
            "task_workspace_root": str(self.workspace), "projects": {}, "authorized_users": [],
            "operational_state": "NOT_CONFIGURED", "inference_enabled": False,
            "publication_enabled": False, "remote_reads_enabled": False,
        }))
        self.service = AdministrationService(self.config, secrets_dir=self.root / "secrets", uid_getter=lambda: 0)

    def tearDown(self): self.temp.cleanup()

    def test_mutations_require_root(self):
        service = AdministrationService(self.config, uid_getter=lambda: 1000)
        with self.assertRaises(AdministrativeAuthorizationError):
            service.config_set("log_level", "DEBUG")

    def test_account_and_project_are_persistent_and_idempotent(self):
        home = self.root / "codex"; cwd = self.root / "repo"; cwd.mkdir()
        account = self.service.add_account("primary", "CHATGPT", home, "/bin/true", cwd)
        project = self.service.add_project("demo", account["id"], "owner/demo")
        self.assertEqual(self.service.add_project("demo", account["id"], "owner/demo")["id"], project["id"])
        configured = self.service.configure_project(project["id"], repository="owner/demo", workspace=cwd,
                                                    authorized_users=["developer"])
        self.assertFalse(configured["runtime"]["enabled"])
        self.assertTrue(self.service.set_project_enabled(project["id"], True)["runtime"]["enabled"])
        reopened = AdministrationService(self.config, secrets_dir=self.root / "secrets", uid_getter=lambda: 0)
        self.assertEqual(reopened.account(account["id"])["runtime"]["codex_home"], str(home))
        self.assertEqual(reopened.project(project["id"])["github_repository"], "owner/demo")

    def test_codex_home_cannot_be_shared(self):
        home = self.root / "codex"; cwd = self.root / "repo"; cwd.mkdir()
        self.service.add_account("one", "CHATGPT", home, "/bin/true", cwd)
        with self.assertRaises(Exception):
            self.service.add_account("two", "CHATGPT", home, "/bin/true", cwd)

    def test_secret_storage_is_restrictive_and_never_returned(self):
        result = self.service.add_credential("github", b"top-secret", "github-main")
        self.assertNotIn("top-secret", json.dumps(result))
        path = self.root / "secrets" / "github-main.secret"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_bytes(), b"top-secret\n")
        self.assertNotIn("secret_path", result)
        self.assertTrue(self.service.check_credential("github-main")["ok"])
        self.service.replace_credential("github-main", b"replacement")
        self.assertEqual(path.read_bytes(), b"replacement\n")
        self.assertEqual(self.service.revoke_credential("github-main")["status"], "REVOKED")
        self.assertFalse(path.exists())
        self.assertFalse(self.service.check_credential("github-main")["ok"])

    def test_secret_ids_and_symlinks_are_rejected(self):
        store = FileSecretStore(self.root / "secrets")
        with self.assertRaises(Exception): store.write("../escape", b"x")
        store.root.mkdir(exist_ok=True); outside = self.root / "outside"; outside.write_text("safe")
        (store.root / "github-link.secret").symlink_to(outside)
        with self.assertRaises(Exception): store.write("github-link", b"x")
        self.assertEqual(outside.read_text(), "safe")

    def test_configuration_write_is_validated_and_degrades_ready(self):
        self.service.config_file.update(lambda raw: {**raw, "operational_state": "READY"})
        self.service._refresh()
        result = self.service.config_set("limits.global_active", "2")
        self.assertEqual(result["limits"]["global_active"], 2)
        self.assertEqual(result["operational_state"], "CONFIGURED")
        with self.assertRaises(Exception): self.service.config_set("limits.global_active", "0")

    def test_lifecycle_requires_enabled_project_and_valid_account(self):
        self.service.setup_configure()
        with self.assertRaises(Exception): self.service.setup_activate()
        cwd = self.root / "repo"; cwd.mkdir()
        account = self.service.add_account("primary", "CHATGPT", self.root / "codex", "/bin/true", cwd)
        project = self.service.add_project("demo", account["id"])
        self.service.configure_project(project["id"], repository=None, workspace=cwd, authorized_users=[])
        self.service.set_project_enabled(project["id"], True)
        self.assertEqual(self.service.setup_activate()["operational_state"], "READY")
        self.assertEqual(self.service.setup_deactivate()["operational_state"], "CONFIGURED")

    def test_doctor_is_read_only_when_database_is_absent(self):
        self.assertFalse((self.root / "state.db").exists())
        report = self.service.doctor()
        self.assertFalse((self.root / "state.db").exists())
        self.assertEqual(report["summary"]["NOT_CONFIGURED"], 1)

    def test_cli_json_and_authorization_exit_code(self):
        output = io.StringIO()
        with patch("vibecoder.admin.os.geteuid", return_value=0), redirect_stdout(output):
            code = main(["--config", str(self.config), "--json", "doctor"])
        self.assertEqual(code, 0); self.assertIn("operational_state", json.loads(output.getvalue()))

    def test_initial_identity_is_stable_idempotent_and_audited(self):
        first = self.service.create_initial_identity("admin", "Local Admin", source="cli")
        second = self.service.create_initial_identity("admin", "Ignored", source="cli")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["display_name"], "Local Admin")
        with self.assertRaises(Exception):
            self.service.create_initial_identity("other", source="debconf")
        with self.service._repo() as repo:
            events = repo.list("admin_audit_events", operation="CREATE_INITIAL_IDENTITY")
        self.assertEqual(len(events), 1)
        self.assertNotIn("Ignored", json.dumps(events))

    def test_setup_init_is_resumable_and_does_not_activate_by_default(self):
        first = self.service.setup_init(admin_identifier="admin")
        second = self.service.setup_init(admin_identifier="admin")
        self.assertEqual(first["initial_identity"]["id"], second["initial_identity"]["id"])
        self.assertEqual(second["operational_state"], "CONFIGURED")
        self.assertIn("codex_account", second["missing"])
        self.assertNotEqual(second["operational_state"], "READY")

    def test_noninteractive_cli_initializes_without_tty_or_activation(self):
        output = io.StringIO()
        with patch("vibecoder.admin.os.geteuid", return_value=0), redirect_stdout(output):
            code = main(["--config", str(self.config), "--json", "setup", "init",
                         "--non-interactive", "--admin-id", "automation"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["initial_identity"]["identifier"], "automation")
        self.assertEqual(payload["operational_state"], "CONFIGURED")

    def test_bootstrap_admin_with_empty_identifier_only_migrates(self):
        output = io.StringIO()
        with patch("vibecoder.admin.os.geteuid", return_value=0), redirect_stdout(output):
            code = main(["--config", str(self.config), "--json", "setup", "bootstrap-admin",
                         "--source", "debconf"])
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(output.getvalue())["identity"])
        self.assertEqual(self.service.identities(), [])

    def test_interactive_wizard_defaults_to_no_activation(self):
        answers = iter(["operator", "Local Operator", "n", "n", "n", "n"])
        output = io.StringIO()
        with patch("vibecoder.admin.os.geteuid", return_value=0), \
             patch("vibecoder.admin.sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _prompt="": next(answers)), \
             redirect_stdout(output):
            code = main(["--config", str(self.config), "--json", "setup", "init"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["initial_identity"]["identifier"], "operator")
        self.assertEqual(payload["operational_state"], "CONFIGURED")
        self.assertNotEqual(payload["operational_state"], "READY")


if __name__ == "__main__": unittest.main()
