from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vibecoder.cli import build_parser, _worker_request
from vibecoder.codex_app_server import CodexAppServer
from vibecoder.codex_plugin_runtime import CodexApprovalBridge
from vibecoder.errors import ConflictError
from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.workers import ProvisionedIdentity, WorkerService


NOW = "2026-11-15T10:00:00+00:00"


class Identity:
    def prepare(self, worker_id, root, admin_gid):
        path = root / worker_id
        path.mkdir(parents=True, mode=0o700)
        (path / "tmp").mkdir(mode=0o700)
        return ProvisionedIdentity("worker-" + worker_id[:8], path, "TEST", False)


class CapturingServer(CodexAppServer):
    def __init__(self, policy):
        super().__init__(Path("/bin/false"), Path("/tmp/home"), Path("/tmp/work"),
                         approval_policy=policy, timeout=.01)
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/start": return {"thread": {"id": "thread"}}
        if method == "thread/resume": return {"thread": {"id": params["threadId"]}}
        if method == "turn/start": return {"turn": {"id": "turn"}}
        raise AssertionError(method)

    def _read(self, deadline):
        raise TimeoutError("fixture complete")


class WorkerApprovalPolicyTests(unittest.TestCase):
    def test_existing_workers_migrate_to_manual_without_schema_version_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db"
            db = SQLiteRepository(path); PluginRegistry(db)
            db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("w", "existing", "fake", "1.0.0", "READY", "{}", "/tmp/w", 1, 0, NOW, None, NOW, NOW))
            db.execute("DROP TABLE worker_approval_policies"); db.close()
            reopened = SQLiteRepository(path)
            self.assertEqual(reopened.schema_version, 1)
            self.assertEqual(reopened.query_one("SELECT policy FROM worker_approval_policies WHERE worker_id='w'")["policy"], "manual")
            reopened.close()

    def test_policy_is_persisted_changed_audited_and_blocks_active_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db = SQLiteRepository(root / "db"); plugins = PluginRegistry(db)
            service = WorkerService(db, plugins, root / "workers", __import__("vibecoder.worker_runtime", fromlist=["DirectProcessLauncher"]).DirectProcessLauncher(), Identity())
            worker = service.add("fake", None, "actor")
            self.assertEqual(worker["approval_policy"], "manual")
            with self.assertRaises(ConflictError):
                service.set_approval_policy(worker["id"], "autonomous", "actor")
            self.assertEqual(plugins.resolve("codex").default_approval_policy, "autonomous")
            self.assertEqual(plugins.resolve("codex").approval_policies, frozenset({"autonomous", "manual"}))
            db.execute("UPDATE workers SET active_executions=1 WHERE id=?", (worker["id"],))
            with self.assertRaises(ConflictError):
                service.set_approval_policy(worker["id"], "autonomous", "actor")
            db.execute("UPDATE workers SET active_executions=0 WHERE id=?", (worker["id"],))
            service.close(); db.close()

    def test_policy_applied_to_thread_resume_and_turn(self):
        autonomous = CapturingServer("autonomous")
        self.assertEqual(autonomous.start_thread(), "thread")
        autonomous.resume_thread("thread")
        # Avoid the event loop: inspect the turn/start request then abort.
        with self.assertRaises(TimeoutError):
            autonomous.execute("hello", "thread")
        calls = {method: params for method, params in autonomous.calls}
        self.assertEqual(calls["thread/start"]["approvalPolicy"], "never")
        self.assertEqual(calls["thread/start"]["sandbox"], "workspace-write")
        self.assertEqual(calls["thread/resume"]["approvalPolicy"], "never")
        self.assertEqual(calls["turn/start"]["sandboxPolicy"],
                         {"type": "workspaceWrite", "networkAccess": False})
        manual = CapturingServer("manual")
        manual.start_thread()
        self.assertEqual(manual.calls[-1][1]["approvalPolicy"], "on-request")
        self.assertEqual(manual.calls[-1][1]["sandbox"], "read-only")

    def test_autonomous_residual_request_is_denied_without_broker(self):
        class NoBroker:
            def request(self, *_args, **_kwargs): raise AssertionError("broker must not be used")
        bridge = CodexApprovalBridge("s", "e", client=NoBroker(), approval_policy="autonomous")
        reply = bridge("item/commandExecution/requestApproval",
            {"threadId": "t", "turnId": "u", "itemId": "i", "command": "cat /forbidden", "reason": "escalation"}, "1")
        self.assertEqual(reply.response, {"decision": "decline"})
        self.assertEqual(bridge.residual_requests[0]["kind"], "COMMAND")
        bridge.confirm(reply.approval_id)

    def test_cli_add_set_and_status_forms(self):
        parser = build_parser()
        args = parser.parse_args(["worker", "add", "codex"])
        self.assertEqual(_worker_request(args)[1]["approval_policy"], None)
        args = parser.parse_args(["worker", "add", "codex", "--approval-policy", "manual"])
        self.assertEqual(_worker_request(args)[1]["approval_policy"], "manual")
        args = parser.parse_args(["worker", "set", "codex-1", "--approval-policy", "autonomous"])
        self.assertEqual(_worker_request(args), ("worker.set-policy", {"worker": "codex-1", "approval_policy": "autonomous"}))
        args = parser.parse_args(["worker", "status", "codex-1"])
        self.assertEqual(_worker_request(args), ("worker.status", {"worker": "codex-1"}))


if __name__ == "__main__": unittest.main()
