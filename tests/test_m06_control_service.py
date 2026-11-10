import tempfile
import unittest
from pathlib import Path

from vibecoder.control_service import (
    AuthorizationError, ControlService, LocalAuthorizer, PeerIdentity,
    UnknownOperation,
)
from vibecoder.sqlite import SQLiteRepository


class ControlServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite"
        self.db = SQLiteRepository(self.path)
        self.service = ControlService(self.db, LocalAuthorizer(2222))
        self.admin = PeerIdentity(1, 1000, 1000, frozenset({2222}))
        self.other = PeerIdentity(2, 1001, 1001, frozenset())

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_standby_offline_is_normal_without_configuration(self):
        status = self.service.dispatch("status", {}, self.other)
        self.assertEqual(status["operational_state"], "STANDBY/OFFLINE")
        self.assertEqual(status["connectivity"], "UNCONFIGURED")
        self.assertEqual(status["execution_capacity"], "NO_WORKER")
        self.assertEqual(status["project_count"], 0)
        self.service.status(); self.service.status()
        self.assertEqual(self.db.query_one("SELECT count(*) AS n FROM audit_events")["n"], 0)

    def test_authorization_uses_peer_not_json(self):
        with self.assertRaises(AuthorizationError):
            self.service.dispatch("pause", {}, self.other)
        with self.assertRaises(ValueError):
            self.service.dispatch("pause", {"uid": 0, "gid": 2222}, self.admin)
        denied = self.db.query_one("SELECT * FROM audit_events WHERE result='DENIED'")
        self.assertEqual(denied["actor"], self.other.actor)

    def test_root_and_supplementary_group_are_admin(self):
        self.assertTrue(self.service.authorizer.is_administrator(PeerIdentity(1, 0, 999)))
        self.assertTrue(self.service.authorizer.is_administrator(self.admin))

    def test_pause_resume_persist_idempotently_and_keep_barrier(self):
        first = self.service.dispatch("pause", {}, self.admin)
        second = self.service.dispatch("pause", {}, self.admin)
        self.assertTrue(first["changed"]); self.assertFalse(second["changed"])
        self.db.execute("INSERT INTO recovery_barriers VALUES(?,?,?,?,?,?)",
                        ("barrier", "restore", "ACTIVE", "now", None, None))
        resumed = self.service.dispatch("resume", {}, self.admin)
        again = self.service.dispatch("resume", {}, self.admin)
        self.assertTrue(resumed["changed"]); self.assertFalse(again["changed"])
        status = self.service.status()
        self.assertFalse(status["administratively_paused"])
        self.assertTrue(status["recovery_barrier"])
        events = self.db.query_all("SELECT operation FROM audit_events WHERE operation IN ('pause','resume')")
        self.assertEqual([x["operation"] for x in events], ["pause", "resume"])

    def test_pause_survives_repository_restart(self):
        self.service.pause(self.admin.actor)
        self.db.close()
        self.db = SQLiteRepository(self.path)
        self.service = ControlService(self.db, LocalAuthorizer(2222))
        self.assertTrue(self.service.status()["administratively_paused"])

    def test_doctor_and_unknown_operation(self):
        doctor = self.service.dispatch("doctor", {}, self.other)
        self.assertEqual(doctor["schema_version"], 1)
        self.assertEqual(doctor["database_integrity"], ["ok"])
        with self.assertRaises(UnknownOperation):
            self.service.dispatch("shell", {}, self.admin)

