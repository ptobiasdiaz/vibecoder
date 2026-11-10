from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import time
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import httpx

from vibecoder.codex_app_server import CodexAppServer, CodexProtocolError
from vibecoder.codex_installation import CodexInstallationManager
from vibecoder.codex_plugin_runtime import _result
from vibecoder.errors import OperationError
from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.workers import WorkerService, ProvisionedIdentity
from vibecoder.codex_installation import CodexExecutable


FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.167.0"); raise SystemExit(0)
if sys.argv[1:] == ["login", "--help"]:
    print("usage: codex login [--device-auth]"); raise SystemExit(0)
if sys.argv[1:] == ["login", "status"]:
    raise SystemExit(0 if (os.path.exists(os.path.join(os.environ["CODEX_HOME"], "authenticated"))) else 1)
if sys.argv[1:] == ["login", "--device-auth"]:
    print("Open https://example.invalid/device and enter ABCD-EFGH")
    open(os.path.join(os.environ["CODEX_HOME"], "authenticated"), "w").close(); raise SystemExit(0)
if sys.argv[1:] == ["logout"]:
    try: os.unlink(os.path.join(os.environ["CODEX_HOME"], "authenticated"))
    except FileNotFoundError: pass
    raise SystemExit(0)
if sys.argv[1:] == ["app-server", "--listen", "stdio://"]:
    thread = "thread-fixture"
    for line in sys.stdin:
        value=json.loads(line); method=value.get("method")
        if method == "initialize": print(json.dumps({"id":value["id"],"result":{"serverInfo":{"version":"fixture"}}}),flush=True)
        elif method == "initialized": pass
        elif method == "thread/start": print(json.dumps({"id":value["id"],"result":{"thread":{"id":thread}}}),flush=True)
        elif method == "thread/resume": print(json.dumps({"id":value["id"],"result":{"thread":{"id":value["params"]["threadId"]}}}),flush=True)
        elif method == "turn/start":
            print(json.dumps({"id":value["id"],"result":{"turn":{"id":"turn-fixture"}}}),flush=True)
            print(json.dumps({"method":"item/agentMessage/delta","params":{"delta":"done"}}),flush=True)
            print(json.dumps({"method":"turn/completed","params":{"turn":{"id":"turn-fixture","status":"completed"}}}),flush=True)
    raise SystemExit(0)
raise SystemExit(2)
'''


class CodexPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.binary = self.root / "codex"; self.binary.write_text(FAKE_CODEX); self.binary.chmod(0o755)
        self.code_mode_host = self.root / "codex-code-mode-host"
        self.code_mode_host.write_text("#!/bin/sh\nexit 0\n"); self.code_mode_host.chmod(0o755)
        self.home = self.root / "home"; self.home.mkdir(); (self.home / "codex-home").mkdir(mode=0o700)

    def tearDown(self): self.temp.cleanup()

    def test_detects_only_compatible_binary_and_records_digest(self):
        found = CodexInstallationManager(self.root).verify(self.binary, managed=True)
        self.assertEqual(found.version, "0.167.0")
        self.assertEqual(found.sha256, hashlib.sha256(self.binary.read_bytes()).hexdigest())

    def test_rejects_symlink_and_incompatible_version(self):
        link = self.root / "link"; link.symlink_to(self.binary)
        with self.assertRaises(OperationError): CodexInstallationManager(self.root).verify(link, managed=True)
        self.binary.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\n"); self.binary.chmod(0o755)
        with self.assertRaises(OperationError) as caught: CodexInstallationManager(self.root).verify(self.binary, managed=True)
        self.assertEqual(caught.exception.details["category"], "INCOMPATIBLE_VERSION")

    def test_manifest_install_is_checksum_verified_and_atomic(self):
        payload = FAKE_CODEX.encode(); digest = hashlib.sha256(payload).hexdigest()
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"artifacts": {"x86_64": {"url": "https://official.invalid/codex",
            "sha256": digest, "version": "0.167.0", "format": "binary"}}}))
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
        managed = self.root / "managed"
        with patch("vibecoder.codex_installation.platform.machine", return_value="x86_64"):
            installed = CodexInstallationManager(managed, manifest, transport=transport).install()
        self.assertEqual(installed.sha256, digest); self.assertTrue((managed / "current").is_symlink())

    def test_bad_download_is_never_published(self):
        manifest = self.root / "manifest.json"; managed = self.root / "managed"
        manifest.write_text(json.dumps({"artifacts": {"x86_64": {"url": "https://official.invalid/codex",
            "sha256": "0" * 64, "version": "0.167.0", "format": "binary"}}}))
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad"))
        with patch("vibecoder.codex_installation.platform.machine", return_value="x86_64"):
            with self.assertRaises(OperationError): CodexInstallationManager(managed, manifest, transport=transport).install()
        self.assertFalse((managed / "current").exists())

    def test_manifest_installs_verified_code_mode_host_companion(self):
        executable, host = FAKE_CODEX.encode(), b"#!/bin/sh\nexit 0\n"
        manifest = self.root / "companion.json"; managed = self.root / "companion-managed"
        manifest.write_text(json.dumps({"artifacts": {"x86_64": {
            "url": "https://official.invalid/codex", "sha256": hashlib.sha256(executable).hexdigest(),
            "version": "0.167.0", "format": "binary", "companions": {
                "codex-code-mode-host": {"url": "https://official.invalid/host",
                    "sha256": hashlib.sha256(host).hexdigest(), "format": "binary"}}}}}))
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, content=host if str(request.url).endswith("/host") else executable))
        with patch("vibecoder.codex_installation.platform.machine", return_value="x86_64"):
            installed = CodexInstallationManager(managed, manifest, transport=transport).install()
        self.assertEqual(installed.version, "0.167.0")
        self.assertEqual((managed / "0.167.0" / "codex-code-mode-host").read_bytes(), host)
        self.assertTrue(os.access(managed / "0.167.0" / "codex-code-mode-host", os.X_OK))

    def test_tar_install_accepts_one_regular_binary_and_rejects_symlink(self):
        def archive(kind=tarfile.REGTYPE):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as bundle:
                info = tarfile.TarInfo("codex-x86_64-unknown-linux-musl"); info.type = kind
                if kind == tarfile.REGTYPE:
                    info.size = len(FAKE_CODEX.encode()); bundle.addfile(info, io.BytesIO(FAKE_CODEX.encode()))
                else:
                    info.linkname = "/etc/passwd"; bundle.addfile(info)
            return output.getvalue()
        for payload, succeeds in ((archive(), True), (archive(tarfile.SYMTYPE), False)):
            managed = self.root / ("tar-ok" if succeeds else "tar-bad"); manifest = self.root / (managed.name + ".json")
            manifest.write_text(json.dumps({"artifacts": {"x86_64": {"url": "https://official.invalid/codex",
                "sha256": hashlib.sha256(payload).hexdigest(), "version": "0.167.0", "format": "tar.gz"}}}))
            transport = httpx.MockTransport(lambda request, content=payload: httpx.Response(200, content=content))
            with patch("vibecoder.codex_installation.platform.machine", return_value="x86_64"):
                if succeeds: self.assertEqual(CodexInstallationManager(managed, manifest, transport=transport).install().version, "0.167.0")
                else:
                    with self.assertRaises(OperationError): CodexInstallationManager(managed, manifest, transport=transport).install()

    def test_verified_app_server_session_and_turn(self):
        (self.home / "codex-home" / "authenticated").touch()
        with CodexAppServer(self.binary, self.home / "codex-home", self.root, timeout=2) as server:
            turn = server.execute("hello")
            self.assertEqual((turn.thread_id, turn.turn_id, turn.status, turn.text),
                             ("thread-fixture", "turn-fixture", "completed", "done"))
            server.resume_thread(turn.thread_id)

    def test_runtime_probe_login_execute_and_logout(self):
        config = {"executable": str(self.binary)}
        with patch.dict(os.environ, {"HOME": str(self.home), "VIBECODER_WORKSPACE": str(self.root)}, clear=False):
            probe = _result({"operation": "probe", "arguments": {"runtime_config": config}})
            self.assertFalse(probe["authenticated"])
            self.assertEqual(_result({"operation": "authentication_methods", "arguments": {"runtime_config": config}})["methods"], ["DEVICE_CODE"])
            login = _result({"operation": "login", "arguments": {"runtime_config": config, "timeout": 2}})
            self.assertEqual(login["outcome"], "SUCCEEDED"); self.assertNotIn("token", login["display"].casefold())
            self.assertFalse((self.home / "codex-home" / "vibecoder-auth-progress.json").exists())
            self.assertTrue(_result({"operation": "probe", "arguments": {"runtime_config": config}})["available"])
            executed = _result({"operation": "execute", "arguments": {"runtime_config": config,
                "instructions": "hello", "session_reference": None, "timeout": 2,
                "session_id": "session-test", "execution_id": "execution-test"}})
            self.assertEqual(executed["outcome"], "SUCCEEDED")
            self.assertEqual(executed["provider_session_reference"], "thread-fixture")
            self.assertEqual(_result({"operation": "logout", "arguments": {"runtime_config": config}})["outcome"], "SUCCEEDED")

    def test_authenticated_probe_rejects_missing_code_mode_host(self):
        (self.home / "codex-home" / "authenticated").touch()
        self.code_mode_host.unlink()
        config = {"executable": str(self.binary)}
        with patch.dict(os.environ, {"HOME": str(self.home)}, clear=False):
            result = _result({"operation": "probe", "arguments": {"runtime_config": config}})
        self.assertFalse(result["available"])
        self.assertIn("CodexProtocolError", result["diagnostic"])

    def test_unverified_observe_and_cancel_are_unsupported(self):
        config = {"executable": str(self.binary)}
        with patch.dict(os.environ, {"HOME": str(self.home)}, clear=False):
            for operation in ("observe", "cancel"):
                value = _result({"operation": operation, "arguments": {"runtime_config": config}})
                self.assertEqual(value["outcome"], "UNSUPPORTED")

    def test_worker_codex_requires_isolation_and_async_login_reaches_ready(self):
        database = SQLiteRepository(self.root / "db.sqlite")
        class Identity:
            def prepare(_self, worker_id, storage_root, admin_gid):
                path = storage_root / worker_id; path.mkdir(parents=True); (path / "tmp").mkdir()
                return ProvisionedIdentity("vibecoder-w-" + worker_id.replace("-", "")[:12], path, "TEST", True)
        class Manager:
            def detect(_self, explicit=None):
                return CodexExecutable(self.binary, "0.167.0", "x86_64", "a" * 64, "managed")
            def install(_self): raise AssertionError("not needed")
        class Launcher:
            security_boundary = True
            def invoke(_self, spec, request):
                operation = request["operation"]
                if operation == "probe":
                    authenticated = getattr(_self, "authenticated", False)
                    return {"available": True, "authenticated": authenticated,
                            "capabilities": ["PERSISTENT_SESSIONS", "SESSION_CONTINUATION"]}
                if operation == "login":
                    time.sleep(.05); _self.authenticated = True
                    return {"outcome": "SUCCEEDED", "display": "Open URL, code ABCD"}
                if operation == "authentication_progress": return {"status": "RUNNING", "display": "Open URL, code ABCD"}
                raise AssertionError(operation)
        service = WorkerService(database, PluginRegistry(database), self.root / "workers", Launcher(), Identity(),
                                executable_managers={"codex": Manager()})
        worker = service.add("codex", "codex-1", "test")
        self.assertEqual(worker["status"], "AUTHENTICATION_REQUIRED")
        started = service.start_login("codex-1", "test", timeout=1)
        self.assertEqual(started["status"], "RUNNING")
        while True:
            status = service.login_status("codex-1", started["operation_id"], "test")
            if status["status"] != "RUNNING": break
            time.sleep(.02)
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["worker"]["status"], "READY")
        stored = database.query_one("SELECT public_json FROM worker_authentication_operations WHERE id=?", (started["operation_id"],))
        self.assertNotIn("display", json.loads(stored["public_json"]))
        service.close(); database.close()


if __name__ == "__main__": unittest.main()
