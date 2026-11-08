from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from vibecoder.admin import AdministrationService
from vibecoder.errors import ConflictError, ValidationError
from vibecoder.maintenance import MaintenanceService
from vibecoder.sqlite import SQLiteRepository
from vibecoder.supervisor import SupervisorStore


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.workspaces = self.root / "workspaces"; self.workspaces.mkdir()
        (self.workspaces / "demo").mkdir(); (self.workspaces / "demo/file.txt").write_text("original")
        self.accounts_dir = self.root / "accounts"; self.accounts_dir.mkdir()
        (self.accounts_dir / "session.json").write_text("session")
        self.accounts_json = self.root / "accounts.json"; self.accounts_json.write_text('{"accounts": {}}\n')
        self.config = self.root / "supervisor.json"
        self.config.write_text(json.dumps({
            "database": str(self.root / "vibecoder.db"), "accounts": str(self.accounts_json),
            "task_workspace_root": str(self.workspaces), "secrets_dir": str(self.root / "secrets"),
            "projects": {}, "operational_state": "CONFIGURED", "inference_enabled": False,
            "publication_enabled": False, "remote_reads_enabled": False,
        }))
        self.admin = AdministrationService(self.config, uid_getter=lambda: 0)
        self.admin.initialize_storage(); self.admin.secrets.write("github-main", b"secret")
        with self.admin._repo() as repo:
            SupervisorStore(repo).set_pause("GLOBAL", "global", all_work=True,
                inference=True, publication=True, actor="test", reason="maintenance")
        self.service = MaintenanceService(self.admin)

    def tearDown(self): self.temp.cleanup()

    def test_backup_create_manifest_verify_and_permissions(self):
        manifest = self.service.create_backup(offline_confirmed=True, backup_id="backup-test")
        self.assertEqual(manifest["format_version"], 1)
        self.assertIn("database/vibecoder.db", manifest["components"])
        result = self.service.verify("backup-test")
        self.assertTrue(result["valid"]); self.assertTrue(result["includes_secrets"])
        self.assertEqual((self.root / "backups").stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(self.service.list_backups()), 1)

    def test_backup_requires_pause_and_offline_confirmation(self):
        with self.assertRaises(ConflictError): self.service.create_backup(offline_confirmed=False)
        with self.admin._repo() as repo:
            SupervisorStore(repo).set_pause("GLOBAL", "global", all_work=False,
                inference=False, publication=False, actor="test")
        with self.assertRaises(ConflictError): self.service.create_backup(offline_confirmed=True)

    def test_running_job_blocks_backup_and_unknown_is_preserved(self):
        now = "2026-11-08T00:00:00+00:00"
        values = ("job-running", "ISSUE_POLL", "REMOTE_READ", "ISSUE", "1",
                  "RUNNING", 1, now, 1, "op-running", "{}", now, now)
        connection = sqlite3.connect(self.root / "vibecoder.db")
        connection.execute(
            "INSERT INTO supervisor_jobs(id,job_type,category,source_type,source_id,state,"
            "priority,eligible_at,max_attempts,operation_key,payload_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        connection.commit(); connection.close()
        with self.assertRaises(ConflictError):
            self.service.create_backup(offline_confirmed=True)
        connection = sqlite3.connect(self.root / "vibecoder.db")
        connection.execute("UPDATE supervisor_jobs SET state='UNKNOWN' WHERE id='job-running'")
        connection.commit(); connection.close()
        manifest = self.service.create_backup(offline_confirmed=True, backup_id="backup-unknown")
        self.assertEqual(manifest["quiescence"]["unknown"], 1)
        connection = sqlite3.connect(self.root / "vibecoder.db")
        state = connection.execute(
            "SELECT state FROM supervisor_jobs WHERE id='job-running'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(state, "UNKNOWN")

    def test_corrupt_and_incomplete_backups_are_rejected(self):
        self.service.create_backup(offline_confirmed=True, backup_id="backup-corrupt")
        target = self.root / "backups/backup-corrupt/payload/data/workspaces/demo/file.txt"
        target.write_text("tampered")
        with self.assertRaises(ValidationError): self.service.verify("backup-corrupt")
        incomplete = self.root / "backups/backup-incomplete"; incomplete.mkdir()
        (incomplete / "manifest.json").write_text("{}")
        with self.assertRaises(ValidationError): self.service.verify("backup-incomplete")

    def test_symlink_in_source_is_rejected_without_publishing_backup(self):
        (self.workspaces / "demo/link").symlink_to(self.root / "outside")
        with self.assertRaises(ValidationError):
            self.service.create_backup(offline_confirmed=True, backup_id="backup-link")
        self.assertFalse((self.root / "backups/backup-link").exists())

    def test_restore_uses_staging_and_sets_recovery_barrier(self):
        self.service.create_backup(offline_confirmed=True, backup_id="backup-good")
        (self.workspaces / "demo/file.txt").write_text("changed")
        result = self.service.restore("backup-good", offline_confirmed=True, confirmed=True)
        self.assertEqual((self.workspaces / "demo/file.txt").read_text(), "original")
        self.assertTrue(result["barrier"])
        refreshed = AdministrationService(self.config, uid_getter=lambda: 0)
        self.assertEqual(refreshed.config.operational_state, "CONFIGURED")
        with refreshed._repo() as repo:
            pause = repo.get("supervisor_controls", "GLOBAL:global")
        self.assertEqual(pause["pause_all"], 1)

    def test_interrupted_restore_is_detected_and_can_roll_back(self):
        self.service.create_backup(offline_confirmed=True, backup_id="backup-before")
        (self.workspaces / "demo/file.txt").write_text("later")
        original_apply = self.service._apply
        def fail_apply(*_args, **_kwargs): raise OSError("simulated")
        self.service._apply = fail_apply  # type: ignore[method-assign]
        with self.assertRaises(OSError):
            self.service.restore("backup-before", offline_confirmed=True, confirmed=True)
        self.assertTrue(self.service.maintenance_status()["barrier"])
        self.service._apply = original_apply  # type: ignore[method-assign]
        result = self.service.reconcile_restore(rollback=True, offline_confirmed=True)
        self.assertEqual(result["phase"], "ROLLED_BACK")
        self.assertEqual((self.workspaces / "demo/file.txt").read_text(), "later")

    def test_newer_schema_is_rejected_by_old_runtime(self):
        database = self.root / "newer.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_migrations VALUES(999, 'now')")
        connection.commit(); connection.close()
        with self.assertRaises(ConflictError): SQLiteRepository(database)


if __name__ == "__main__": unittest.main()
