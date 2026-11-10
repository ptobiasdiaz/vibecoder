import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from vibecoder.errors import ConflictError
from vibecoder.sqlite import (
    DatabaseKind, IncompatibleDatabaseError, SQLiteRepository, classify_database,
)


NOW = "2026-01-01T00:00:00Z"


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite"
    def tearDown(self):
        self.tmp.cleanup()

    def test_initializes_schema_one_and_integrity(self):
        db = SQLiteRepository(self.path)
        self.assertEqual(db.schema_version, 1)
        self.assertEqual(db.integrity_check(), ["ok"])
        db.close()
        self.assertEqual(classify_database(self.path), DatabaseKind.CURRENT)

    def seed_project(self, db):
        db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)",
                   ("r", 10, None, "owner", "repo", "owner/repo", "main", NOW, NOW))
        db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
                   ("p", "repo", "r", "main", "abc", "ENABLED", NOW, NOW))

    def test_foreign_keys_uniqueness_last_user_and_transaction(self):
        db = SQLiteRepository(self.path)
        self.seed_project(db)
        db.execute("INSERT INTO project_authorized_users VALUES(?,?,?,?,?)", ("p", 1, "alice", NOW, None))
        with self.assertRaises(ConflictError):
            db.remove_project_authorization("p", 1)
        with self.assertRaises(ConflictError):
            db.execute("INSERT INTO project_authorized_users VALUES(?,?,?,?,?)", ("p", 1, "other", NOW, None))
        with self.assertRaises(RuntimeError):
            with db.transaction():
                db.execute("INSERT INTO installation VALUES(?,?,?)", ("i", NOW, NOW))
                raise RuntimeError("rollback")
        self.assertIsNone(db.query_one("SELECT * FROM installation"))
        db.close()

    def test_agent_scope_and_immutable_revisions(self):
        db = SQLiteRepository(self.path)
        self.seed_project(db)
        db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)", ("r2", 11, None, "owner", "two", "owner/two", "main", NOW, NOW))
        # Project display names may collide; canonical repository names disambiguate them.
        db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)", ("p2", "repo", "r2", "main", "abc", "ENABLED", NOW, NOW))
        for aid, pid in (("a1", "p"), ("a2", "p2")):
            db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)", (aid, pid, "main", "role", "ACTIVE", 1, None, NOW, NOW))
            db.execute("INSERT INTO agent_revisions VALUES(?,?,?,?,?,?,?,?,?)", (aid+"r", aid, 1, "[]", "[]", "[]", "text", "hash", NOW))
        with self.assertRaises(ConflictError):
            db.execute("UPDATE agent_revisions SET instructions=? WHERE id=?", ("changed", "a1r"))
        # A provider session cannot cross the agent's project boundary.
        with self.assertRaises(ConflictError):
            db.execute("INSERT INTO provider_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("s", "p2", "a1", "missing", None, "purpose", "hash", None,
                        "ACTIVE", "{}", NOW, NOW))
        db.close()

    def test_approval_is_bound_to_exact_plan_hash(self):
        db = SQLiteRepository(self.path)
        self.seed_project(db)
        db.execute("INSERT INTO issue_workflows VALUES(?,?,?,?,?,?,?)",
                   ("i", "p", 10, 1, "PLANNING", NOW, NOW))
        db.execute("INSERT INTO plan_revisions VALUES(?,?,?,?,?,?,?)",
                   ("plan", "i", 1, "PROPOSED", "expected", "{}", NOW))
        with self.assertRaises(ConflictError):
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)",
                       ("approval", "plan", "wrong", 1, "event", NOW))
        db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)",
                   ("approval", "plan", "expected", 1, "event", NOW))
        db.close()

    def assert_rejected_unchanged(self, setup, expected):
        setup()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        with self.assertRaises(IncompatibleDatabaseError) as caught:
            SQLiteRepository(self.path)
        self.assertEqual(caught.exception.kind, expected)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def create_foreign_schema(self, table):
        connection=sqlite3.connect(self.path)
        try:
            connection.execute(f"CREATE TABLE {table}(id TEXT)");connection.commit()
        finally:connection.close()

    def test_rejects_legacy_unknown_and_corrupt_without_modification(self):
        self.assert_rejected_unchanged(lambda: self.create_foreign_schema("codex_accounts"), DatabaseKind.LEGACY_0_4)
        self.path.unlink()
        self.assert_rejected_unchanged(lambda: self.create_foreign_schema("alien"), DatabaseKind.UNKNOWN)
        self.path.unlink()
        self.assert_rejected_unchanged(lambda: self.path.write_bytes(b"not sqlite"), DatabaseKind.CORRUPT)

    def test_discarded_events_have_no_body_column(self):
        db = SQLiteRepository(self.path)
        columns = {row["name"] for row in db.query_all("PRAGMA table_info(discarded_events)")}
        self.assertNotIn("body", columns)
        db.close()

