from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from vibecoder.approvals import RuntimeApprovalService
from vibecoder.codex_plugin_runtime import CodexApprovalBridge
from vibecoder.errors import ConflictError, ValidationError
from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository

NOW = "2026-11-10T10:00:00+00:00"


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db_path = Path(self.temp.name) / "db"; self.db = SQLiteRepository(self.db_path)
        PluginRegistry(self.db)
        self.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("w", "fake-live", "fake", "1.0.0", "READY", "{}", "/tmp/w", 1, 1, NOW, None, NOW, NOW,))
        self.db.execute("INSERT INTO worker_runtime_identities VALUES(?,?,?,?)", ("w", "vibecoder-w-aaaaaaaaaaaa", "TEST", NOW))
        self.db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)", ("r", 1, None, "o", "p", "o/p", "main", NOW, NOW))
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)", ("p", "p", "r", "main", "a" * 40, "DISABLED", NOW, NOW))
        self.db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)", ("a", "p", "main", "main", "ACTIVE", 1, None, NOW, NOW))
        self.db.execute("INSERT INTO provider_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("s", "p", "a", "w", None, "test", "ctx", "provider", "ACTIVE", "{}", NOW, NOW))
        self.db.execute("INSERT INTO provider_executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("e", "s", "w", "key", "RUNNING", None, None, None, "{}", "{}", NOW, NOW))
        self.service = RuntimeApprovalService(self.db)

    def tearDown(self): self.db.close(); self.temp.cleanup()

    def request(self, ref="1", timeout=60):
        return {"session_id": "s", "execution_id": "e", "provider_request_ref": ref,
            "kind": "COMMAND", "summary": "Comando: printf test", "details": {"command": "printf test"},
            "allowed_decisions": ["APPROVE", "DENY"], "timeout_seconds": timeout}

    def test_register_duplicate_decide_claim_confirm_and_conflict(self):
        first = self.service.register("w", self.request()); duplicate = self.service.register("w", self.request())
        self.assertEqual(first["id"], duplicate["id"]); self.assertEqual(first["status"], "PENDING")
        decided = self.service.decide(first["id"], "APPROVE", "unix:0")
        self.assertEqual(decided["status"], "DECISION_RECORDED")
        self.assertEqual(self.service.claim("w", first["id"])["status"], "DELIVERING")
        self.assertEqual(self.service.confirm("w", first["id"], True)["status"], "APPROVED")
        with self.assertRaises(ConflictError): self.service.decide(first["id"], "DENY", "unix:0")

    def test_correlation_validation_and_no_implicit_decision(self):
        bad = self.request(); bad["execution_id"] = "other"
        with self.assertRaises(ValidationError): self.service.register("w", bad)
        pending = self.service.register("w", self.request("2"))
        self.assertIsNone(pending["decision"])

    def test_recovery_marks_unconfirmed_requests_unknown(self):
        pending = self.service.register("w", self.request("p"))
        delivery = self.service.register("w", self.request("d")); self.service.decide(delivery["id"], "DENY", "root"); self.service.claim("w", delivery["id"])
        self.service.recover()
        self.assertEqual(self.service.get(pending["id"])["status"], "UNKNOWN")
        self.assertEqual(self.service.get(delivery["id"])["status"], "UNKNOWN")

    def test_codex_translation_waits_for_human_and_is_per_request(self):
        class Client:
            def __init__(inner): inner.approval = None
            def request(inner, operation, arguments):
                repository = SQLiteRepository(self.db_path); service = RuntimeApprovalService(repository)
                try:
                    if operation == "approval.register":
                        inner.approval = service.register("w", arguments); return inner.approval
                    if operation == "approval.claim": return service.claim("w", arguments["approval_id"])
                    return service.confirm("w", arguments["approval_id"], arguments["delivered"])
                finally: repository.close()
        client = Client(); bridge = CodexApprovalBridge("s", "e", timeout=5, client=client)
        result = {}
        def wait(): result["reply"] = bridge("item/commandExecution/requestApproval",
            {"threadId": "t", "turnId": "u", "itemId": "i", "command": "printf ok", "cwd": "/workspace"}, "7")
        thread = threading.Thread(target=wait); thread.start()
        while client.approval is None: time.sleep(.01)
        self.assertTrue(thread.is_alive()); self.assertEqual(client.approval["status"], "PENDING")
        self.service.decide(client.approval["id"], "DENY", "root")
        thread.join(2); self.assertFalse(thread.is_alive())
        self.assertEqual(result["reply"].response, {"decision": "decline"})
        bridge.confirm(result["reply"].approval_id)
        self.assertEqual(self.service.get(client.approval["id"])["status"], "DENIED")

    def test_file_and_permission_responses_are_single_turn_only(self):
        class ImmediateClient:
            def __init__(inner, decision): inner.decision, inner.n = decision, 0
            def request(inner, operation, arguments):
                if operation == "approval.register":
                    inner.n += 1; return {"id": str(inner.n), "status": "DECISION_RECORDED", "decision": inner.decision}
                if operation == "approval.claim": return {"id": arguments["approval_id"], "status": "DELIVERING", "decision": inner.decision}
                return {}
        base = {"threadId": "t", "turnId": "u", "itemId": "i"}
        file_reply = CodexApprovalBridge("s", "e", client=ImmediateClient("APPROVE"))(
            "item/fileChange/requestApproval", base, "f")
        self.assertEqual(file_reply.response, {"decision": "accept"})
        permission = {**base, "cwd": "/w", "permissions": {"network": {"enabled": True}}}
        denied = CodexApprovalBridge("s", "e", client=ImmediateClient("DENY"))(
            "item/permissions/requestApproval", permission, "p")
        self.assertEqual(denied.response["scope"], "turn")
        self.assertIsNone(denied.response["permissions"]["network"])


if __name__ == "__main__": unittest.main()
