import json
import os
import subprocess
import tempfile
import unittest
import uuid
import stat
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from vibecoder.control_service import AuthorizationError, ControlService, LocalAuthorizer, PeerIdentity
from vibecoder.domain import (Agent, AgentStatus, ExecutionOutcome, PluginCapability,
                              WorkerStatus)
from vibecoder.errors import ConflictError, OperationError
from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.worker_runtime import (DirectProcessLauncher, RuntimeLaunchSpec,
                                      SystemdWorkerLauncher, runtime_request)
from vibecoder.workers import ProvisionedIdentity, WorkerService, _safe_storage_root

NOW = "2026-11-09T10:00:00+00:00"


class TestProvisioner:
    def prepare(self, worker_id, root, admin_gid):
        path = root / worker_id; path.mkdir(parents=True, mode=0o700)
        (path / "tmp").mkdir(mode=0o700)
        return ProvisionedIdentity(f"vibecoder-w-{worker_id.replace('-', '')[:12]}", path,
                                   "TEST_SAME_UID", False)


class PluginWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.db = SQLiteRepository(root / "db")
        self.plugins = PluginRegistry(self.db)
        self.workers = WorkerService(self.db, self.plugins, root / "workers",
            DirectProcessLauncher(), TestProvisioner())
    def tearDown(self): self.db.close(); self.tmp.cleanup()

    def add(self, name=None, configuration=None):
        return self.workers.add("fake", name, "test", configuration=configuration)

    def seed_project_agent(self):
        self.db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)",
            ("r", 1, None, "o", "p", "o/p", "main", NOW, NOW))
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
            ("p", "p", "r", "main", "a" * 40, "ENABLED", NOW, NOW))
        self.db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)",
            ("a", "p", "main", "main", "ACTIVE", 1, None, NOW, NOW))

    def test_declarative_discovery_does_not_import_runtime_and_codex_pending(self):
        listed = self.plugins.list()
        self.assertEqual({x["id"] for x in listed}, {"fake", "codex"})
        codex = self.plugins.resolve("codex").public()
        self.assertTrue(codex["available"])
        self.assertIn("0.167.0", codex["diagnostic"])

    def test_worker_identity_is_stable_across_failed_provisioning_retries(self):
        class MissingIdentity:
            ids = []
            def prepare(inner, worker_id, storage_root, admin_gid):
                inner.ids.append(worker_id)
                raise OperationError("missing", details={"category": "IDENTITY_NOT_PROVISIONED"})
        missing = MissingIdentity()
        service = WorkerService(self.db, self.plugins, Path(self.tmp.name) / "missing-workers",
                                DirectProcessLauncher(), missing)
        for _ in range(2):
            with self.assertRaises(OperationError): service.add("fake", "stable-worker", "test")
        self.assertEqual(len(set(missing.ids)), 1)
        service.close()

    def test_preprovisioned_storage_allows_only_private_or_daemon_traverse(self):
        directory = stat.S_IFDIR
        self.assertTrue(_safe_storage_root(SimpleNamespace(st_mode=directory | 0o710, st_uid=1001, st_gid=103), 1001, 103))
        self.assertTrue(_safe_storage_root(SimpleNamespace(st_mode=directory | 0o700, st_uid=1001, st_gid=1001), 1001, 103))
        self.assertFalse(_safe_storage_root(SimpleNamespace(st_mode=directory | 0o750, st_uid=1001, st_gid=103), 1001, 103))
        self.assertFalse(_safe_storage_root(SimpleNamespace(st_mode=directory | 0o710, st_uid=1001, st_gid=999), 1001, 103))
        with self.assertRaises(OperationError): self.workers.add("codex", None, "test")
        self.assertEqual(self.db.query_one("SELECT count(*) n FROM workers")["n"], 0)

    def test_add_auto_names_persistence_disable_enable_and_duplicate(self):
        first = self.add(); second = self.add()
        self.assertEqual((first["name"], second["name"]), ("fake-1", "fake-2"))
        self.assertEqual(first["status"], "READY")
        self.assertIn("STRUCTURED_OUTPUT", first["capabilities"])
        with self.assertRaises(ConflictError): self.add("fake-1")
        disabled = self.workers.disable("fake-1", "test")
        self.assertEqual(disabled["status"], "DISABLED")
        self.assertFalse(self.workers.disable("fake-1", "test")["changed"])
        enabled = self.workers.enable(first["id"], "test")
        self.assertEqual(enabled["status"], "READY")
        restarted = WorkerService(self.db, self.plugins, Path(self.tmp.name) / "workers",
                                  DirectProcessLauncher(), TestProvisioner())
        self.assertEqual(len(restarted.list()), 2)

    def test_auth_required_unavailable_crash_and_capability_validation(self):
        auth = self.add("auth", {"mode": "auth-required"})
        self.assertEqual(auth["status"], "AUTHENTICATION_REQUIRED")
        unavailable = self.add("down", {"mode": "unavailable"})
        self.assertEqual(unavailable["status"], "UNAVAILABLE")
        crashed = self.add("crash", {"mode": "crash"})
        self.assertEqual(crashed["status"], "UNAVAILABLE")
        invalid = self.add("invalid", {"capabilities": ["WORKSPACE_EXECUTION", "UNKNOWN_CAP"]})
        self.assertEqual(invalid["status"], "ERROR")

    def test_selection_uses_persisted_ready_workers(self):
        first = self.add("z"); second = self.add("a")
        agent = Agent("agent", "project", "main", "role", (),
            frozenset({PluginCapability.STRUCTURED_OUTPUT}), (), 1, AgentStatus.ACTIVE)
        self.assertEqual(self.workers.select(agent).worker.name, "a")
        pinned = Agent(**{**agent.__dict__, "pinned_worker_id": first["id"]})
        self.assertEqual(self.workers.select(pinned).worker.id, first["id"])
        self.workers.disable(first["id"], "test")
        with self.assertRaises(ConflictError): self.workers.select(pinned)

    def test_sessions_execution_idempotency_unknown_reconciliation_and_cancel(self):
        worker = self.add(); self.seed_project_agent()
        session = self.workers.create_session("p", "a", "implementation", "hash", worker["id"])
        self.assertEqual(session["status"], "ACTIVE"); self.assertTrue(session["provider_reference"].startswith("fake-session-"))
        self.assertTrue(json.loads(self.workers.continue_session(session["id"])["continuity_json"])["continued"])
        success = self.workers.execute(session["id"], "key-success", "hello")
        self.assertEqual(success["outcome"], "SUCCEEDED")
        same = self.workers.execute(session["id"], "key-success", "must not run")
        self.assertEqual(same["id"], success["id"])
        unknown = self.workers.execute(session["id"], "key-unknown", "uncertain", mode="unknown")
        self.assertEqual(unknown["outcome"], "UNKNOWN")
        reconciled = self.workers.reconcile(unknown["id"])
        self.assertEqual(reconciled["outcome"], "SUCCEEDED")
        uncertain = self.workers.execute(session["id"], "key-cancel", "cancel", mode="unknown")
        cancelled = self.workers.cancel(uncertain["id"])
        self.assertEqual(cancelled["outcome"], "CANCELLED")
        quota = self.workers.execute(session["id"], "key-quota", "quota", mode="quota")
        self.assertEqual(quota["outcome"], "WAITING_QUOTA")
        failed = self.workers.execute(session["id"], "key-failed", "bad", mode="failure")
        self.assertEqual(failed["outcome"], "FAILED")
        unsupported = self.workers.execute(session["id"], "key-unsupported", "x", mode="unknown")
        self.assertEqual(self.workers.cancel(unsupported["id"], mode="unsupported")["outcome"], "UNSUPPORTED")

    def test_restart_turns_running_into_unknown_without_retry(self):
        worker = self.add(); self.seed_project_agent()
        session = self.workers.create_session("p", "a", "x", "h", worker["id"])
        execution_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO provider_executions VALUES(?,?,?,?,'RUNNING',NULL,'opaque',NULL,'{}','{}',?,?)",
            (execution_id, session["id"], worker["id"], "interrupted", NOW, NOW))
        self.db.execute("UPDATE workers SET active_executions=1 WHERE id=?", (worker["id"],))
        WorkerService(self.db, self.plugins, Path(self.tmp.name) / "workers",
                      DirectProcessLauncher(), TestProvisioner())
        row = self.db.query_one("SELECT state,outcome,error_category FROM provider_executions WHERE id=?", (execution_id,))
        self.assertEqual((row["state"], row["outcome"], row["error_category"]),
                         ("FINISHED", "UNKNOWN", "DAEMON_RESTART"))
        self.assertEqual(self.db.query_one("SELECT active_executions FROM workers WHERE id=?", (worker["id"],))["active_executions"], 0)

    def test_late_runtime_result_does_not_overwrite_recovered_unknown(self):
        worker = self.add(); self.seed_project_agent()
        session = self.workers.create_session("p", "a", "x", "h", worker["id"])
        original = self.workers.launcher.invoke

        def recovered_result(spec, request):
            if request["operation"] == "execute":
                execution_id = request["arguments"]["execution_id"]
                self.db.execute("UPDATE provider_executions SET state='FINISHED',outcome='UNKNOWN',"
                    "error_category='DAEMON_RESTART' WHERE id=?", (execution_id,))
                self.db.execute("UPDATE workers SET active_executions=0 WHERE id=?", (worker["id"],))
                return {"outcome": "SUCCEEDED", "structured_result": {"text": "late"}}
            return original(spec, request)

        self.workers.launcher.invoke = recovered_result
        result = self.workers.execute(session["id"], "late-key", "test")
        self.assertEqual((result["outcome"], result["error_category"]), ("UNKNOWN", "DAEMON_RESTART"))
        self.assertEqual(self.db.query_one("SELECT active_executions FROM workers WHERE id=?", (worker["id"],))["active_executions"], 0)

    def test_worker_cannot_receive_workspace_from_another_project(self):
        worker = self.add(); self.seed_project_agent()
        self.db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)",
            ("r2", 2, None, "o", "q", "o/q", "main", NOW, NOW))
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
            ("q", "q", "r2", "main", "b" * 40, "ENABLED", NOW, NOW))
        path = Path(self.tmp.name) / "foreign-workspace"; path.mkdir()
        self.db.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)",
            ("wq", "q", "TASK", str(path), "main", "b" * 40, "READY", NOW, NOW))
        with self.assertRaises(ConflictError):
            self.workers.create_session("p", "a", "x", "h", worker["id"], "wq")

    def test_control_operations_authorization_and_ready_state(self):
        control = ControlService(self.db, LocalAuthorizer(9), plugins=self.plugins, workers=self.workers)
        self.assertEqual(len(control.dispatch("plugin.list", {}, PeerIdentity(1, 100, 100))["plugins"]), 2)
        with self.assertRaises(AuthorizationError):
            control.dispatch("worker.add", {"plugin": "fake", "name": None}, PeerIdentity(1, 100, 100))
        result = control.dispatch("worker.add", {"plugin": "fake", "name": None}, PeerIdentity(1, 100, 9))
        self.assertEqual(result["status"], "READY")
        self.db.execute("INSERT INTO secret_metadata VALUES(?,?,?,?,?,?)", ("s", "GITHUB_PAT", "opaque", "ACTIVE", NOW, NOW))
        self.db.execute("INSERT INTO github_identity VALUES(1,42,'owner','s','ONLINE',?,?,?,0,?)", (NOW, NOW, "{}", NOW))
        self.assertEqual(control.status()["operational_state"], "READY/ONLINE")
        self.workers.disable(result["id"], "test")
        self.assertEqual(control.status()["operational_state"], "STANDBY/ONLINE")


class LauncherSecurityContractTests(unittest.TestCase):
    def test_systemd_command_switches_uid_and_hardens_before_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); spec = RuntimeLaunchSpec("a" * 36, "vibecoder-w-" + "a" * 12,
                "vibecoder.fake_plugin_runtime", root, root / "workspace")
            (root / "workspace").mkdir()
            response = {"version": 1, "request_id": "x", "worker_id": spec.worker_id,
                        "ok": True, "result": {}}
            class Result:
                returncode = 0; stderr = b""
                from vibecoder.runtime_protocol import encode_runtime
                stdout = encode_runtime(response)
            captured = {}
            def run(command, **kwargs):
                captured.update(command=command, kwargs=kwargs)
                # correlate to actual request
                from vibecoder.runtime_protocol import decode_runtime, encode_runtime
                req = decode_runtime(kwargs["input"])
                Result.stdout = encode_runtime({**response, "request_id": req["request_id"]})
                return Result()
            with patch("vibecoder.worker_runtime.subprocess.run", side_effect=run):
                launcher = SystemdWorkerLauncher()
                launcher.invoke(spec, runtime_request(spec, "probe", {}))
            command = captured["command"]
            self.assertIn("--uid=" + spec.unix_user, command)
            self.assertIn("--property=NoNewPrivileges=yes", command)
            self.assertIn("--property=ProtectSystem=strict", command)
            self.assertIn("--property=CapabilityBoundingSet=", command)
            self.assertIn("--property=KillMode=control-group", command)
            self.assertIn("--property=SupplementaryGroups=vibecoder-runtime", command)
            self.assertIn("--property=RuntimeMaxSec=35", command)
            inaccessible = next(x for x in command if x.startswith("--property=InaccessiblePaths="))
            self.assertIn("/var/lib/vibecoder/secrets", inaccessible)
            self.assertIn("/var/lib/vibecoder/vibecoder.db", inaccessible)
            self.assertIn("/run/vibecoder/control.sock", inaccessible)
            self.assertIn("-/run/vibecoder/control.sock", inaccessible)
            self.assertTrue(captured["kwargs"]["close_fds"])
            env = captured["kwargs"]["env"]
            for forbidden in ("GITHUB_TOKEN", "VIBECODER_PAT", "DATABASE_URL", "SSH_AUTH_SOCK"):
                self.assertNotIn(forbidden, env)

    def test_direct_launcher_is_explicitly_not_security_boundary(self):
        self.assertFalse(DirectProcessLauncher.security_boundary)

    def test_timeout_is_typed_and_does_not_expose_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = RuntimeLaunchSpec("b" * 36, "vibecoder-w-" + "b" * 12,
                "vibecoder.fake_plugin_runtime", Path(tmp), timeout=0.01)
            with self.assertRaises(OperationError) as caught:
                DirectProcessLauncher().invoke(spec, runtime_request(spec, "execute", {"mode": "timeout", "seconds": .2}))
            self.assertEqual(caught.exception.details["category"], "TIMEOUT")
