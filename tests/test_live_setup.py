from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests.test_github import FakeGitHubClient, repository_payload
from vibecoder import ConflictError, GitHubRegistryService, OrchestratorCore, SQLiteRepository
from vibecoder.errors import ValidationError
from vibecoder.live_setup import initialize_live_setup


class LiveSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "live.sqlite3"
        self.accounts_json = self.root / "accounts.json"
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.executable = self.root / "codex"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.remote = FakeGitHubClient()
        self.remote.repository = repository_payload(
            owner="ptobiasdiaz", name="vibecoder_test"
        )
        self.github = GitHubRegistryService(self.core, self.remote)

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def initialize(self, **overrides: object):
        values: dict[str, object] = {
            "account_name": "codex-live",
            "project_name": "vibecoder-test",
            "repository": "ptobiasdiaz/vibecoder_test",
            "codex_home": self.codex_home,
            "codex_executable": self.executable,
            "working_directory": self.cwd,
            "accounts_json": self.accounts_json,
        }
        values.update(overrides)
        return initialize_live_setup(self.core, self.github, **values)

    def test_initializes_account_project_repository_and_accounts_json(self) -> None:
        result = self.initialize()
        self.assertTrue(result.account_id)
        self.assertTrue(result.project_id)
        self.assertEqual(result.repository_full_name, "ptobiasdiaz/vibecoder_test")
        self.assertTrue(result.accounts_json_created)
        payload = json.loads(self.accounts_json.read_text(encoding="utf-8"))
        config = payload["accounts"][result.account_id]
        self.assertEqual(config["codex_home"], os.fspath(self.codex_home))
        self.assertEqual(config["codex_executable"], os.fspath(self.executable))
        self.assertEqual(config["cwd"], os.fspath(self.cwd))
        self.assertEqual(len(self.repository.list("codex_accounts")), 1)
        self.assertEqual(len(self.repository.list("projects")), 1)
        self.assertEqual(len(self.repository.list("github_repositories")), 1)
        self.assertEqual(self.remote.issue_writes, 0)
        self.assertEqual(self.remote.comment_writes, 0)

    def test_repeated_initialization_reuses_all_records(self) -> None:
        first = self.initialize()
        second = self.initialize()
        self.assertEqual(first.account_id, second.account_id)
        self.assertEqual(first.project_id, second.project_id)
        self.assertEqual(first.repository_id, second.repository_id)
        self.assertFalse(second.accounts_json_created)
        self.assertEqual(len(self.repository.list("codex_accounts")), 1)
        self.assertEqual(len(self.repository.list("projects")), 1)
        self.assertEqual(len(self.repository.list("github_repositories")), 1)

    def test_existing_project_is_associated_with_reused_account(self) -> None:
        other = self.core.register_account("other", "CHATGPT", "codex-home://other")
        project = self.core.register_project("vibecoder-test", other.id)
        result = self.initialize()
        self.assertEqual(result.project_id, project.id)
        self.assertEqual(self.core.get_project(project.id).codex_account_id, result.account_id)

    def test_missing_codex_home_or_executable_is_rejected_before_remote_write(self) -> None:
        with self.assertRaises(ValidationError):
            self.initialize(codex_home=self.root / "missing")
        with self.assertRaises(ValidationError):
            self.initialize(codex_executable=self.root / "missing-codex")
        self.assertEqual(self.remote.issue_writes, 0)

    def test_invalid_repository_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.initialize(repository="not-a-repository")

    def test_existing_incompatible_accounts_json_is_not_overwritten(self) -> None:
        first = self.initialize()
        payload = json.loads(self.accounts_json.read_text(encoding="utf-8"))
        payload["accounts"][first.account_id]["cwd"] = os.fspath(self.root)
        self.accounts_json.write_text(json.dumps(payload), encoding="utf-8")
        before = self.accounts_json.read_text(encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.initialize()
        self.assertEqual(self.accounts_json.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
