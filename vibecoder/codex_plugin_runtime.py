"""Isolated Codex provider runtime. This module is never imported by vibecoderd."""
from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import sys
import re
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

from .codex_app_server import CodexAppServer, CodexApprovalReply, CodexProtocolError
from .codex_installation import CodexInstallationManager
from .runtime_protocol import MAX_RUNTIME_MESSAGE, RUNTIME_PROTOCOL_VERSION, encode_runtime
from .runtime_approval_protocol import RuntimeApprovalClient


def _receive() -> dict[str, Any]:
    header = sys.stdin.buffer.read(4)
    if len(header) != 4: raise ValueError("missing frame")
    size = struct.unpack("!I", header)[0]
    if size > MAX_RUNTIME_MESSAGE: raise ValueError("large frame")
    value = json.loads(sys.stdin.buffer.read(size))
    if not isinstance(value, dict) or value.get("version") != RUNTIME_PROTOCOL_VERSION: raise ValueError("bad protocol")
    return value


def _paths(arguments: dict[str, Any]) -> tuple[Path, Path]:
    config = arguments.get("runtime_config", {})
    executable = Path(str(config.get("executable", ""))).absolute()
    home = Path(os.environ["HOME"]).absolute(); codex_home = home / "codex-home"
    if home.is_symlink(): raise ValueError("worker home inseguro")
    codex_home.mkdir(mode=0o700, exist_ok=True)
    info = codex_home.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("CODEX_HOME inseguro")
    return executable, codex_home


def _login_help(executable: Path, codex_home: Path) -> str:
    result = subprocess.run((str(executable), "login", "--help"), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=10, close_fds=True,
        env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home), "HOME": str(codex_home.parent), "LANG": "C"})
    return result.stdout


def _auth_status(executable: Path, codex_home: Path) -> bool:
    result = subprocess.run((str(executable), "login", "status"), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=10, close_fds=True,
        env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home), "HOME": str(codex_home.parent), "LANG": "C"})
    return result.returncode == 0


def _code_mode_host(executable: Path) -> Path:
    host = executable.parent / "codex-code-mode-host"
    try: info = host.lstat()
    except FileNotFoundError as exc:
        raise CodexProtocolError("falta codex-code-mode-host de la misma versión") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR:
        raise CodexProtocolError("codex-code-mode-host inseguro")
    return host


def _result(request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request.get("operation")); arguments = dict(request.get("arguments", {}))
    executable, codex_home = _paths(arguments)
    verified = CodexInstallationManager(executable.parent).verify(executable, managed=True)
    if operation == "probe":
        authenticated = _auth_status(executable, codex_home)
        if not authenticated:
            return {"available": True, "authenticated": False, "capabilities": [],
                    "diagnostic": "autenticación Codex requerida", "version": verified.version}
        try:
            _code_mode_host(executable)
            with CodexAppServer(executable, codex_home, None, timeout=10): pass
        except (OSError, TimeoutError, CodexProtocolError) as exc:
            return {"available": False, "authenticated": True, "capabilities": [],
                    "diagnostic": f"App Server incompatible: {type(exc).__name__}", "version": verified.version}
        return {"available": True, "authenticated": True, "version": verified.version,
                "capabilities": ["PERSISTENT_SESSIONS", "SESSION_CONTINUATION", "STRUCTURED_OUTPUT", "WORKSPACE_EXECUTION"]}
    if operation == "authentication_methods":
        return {"methods": ["DEVICE_CODE"] if "--device-auth" in _login_help(executable, codex_home) else []}
    if operation == "login":
        if "--device-auth" not in _login_help(executable, codex_home):
            return {"outcome": "UNSUPPORTED", "error_category": "DEVICE_AUTH_UNSUPPORTED"}
        progress = codex_home / "vibecoder-auth-progress.json"; pid_path = codex_home / "vibecoder-auth.pid"
        child = subprocess.Popen((str(executable), "login", "--device-auth"), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home), "HOME": str(codex_home.parent), "LANG": "C.UTF-8"})
        pid_path.write_text(str(child.pid), encoding="ascii"); pid_path.chmod(0o600)
        lines: list[str] = []
        try:
            assert child.stdout is not None
            for line in child.stdout:
                lines.append(line); lines = lines[-32:]
                _write_progress(progress, "RUNNING", _sanitize("".join(lines)))
            code = child.wait(timeout=float(arguments.get("timeout", 600)))
        except subprocess.TimeoutExpired:
            child.terminate(); code = -signal.SIGTERM
        finally:
            if child.stdout is not None: child.stdout.close()
            pid_path.unlink(missing_ok=True)
        outcome = "SUCCEEDED" if code == 0 and _auth_status(executable, codex_home) else "FAILED"
        display = _sanitize("".join(lines)); progress.unlink(missing_ok=True)
        return {"outcome": outcome, "display": display}
    if operation == "authentication_progress":
        progress = codex_home / "vibecoder-auth-progress.json"
        if not progress.is_file() or progress.is_symlink(): return {"status": "RUNNING", "display": ""}
        value = json.loads(progress.read_text(encoding="utf-8"))
        return {"status": str(value.get("status", "RUNNING")), "display": _sanitize(str(value.get("display", "")))}
    if operation == "authentication_cancel":
        pid_path = codex_home / "vibecoder-auth.pid"
        if not pid_path.is_file() or pid_path.is_symlink(): return {"cancelled": False}
        pid = int(pid_path.read_text(encoding="ascii")); os.kill(pid, signal.SIGTERM)
        return {"cancelled": True}
    if operation == "logout":
        completed = subprocess.run((str(executable), "logout"), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=30, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home), "HOME": str(codex_home.parent), "LANG": "C"})
        return {"outcome": "SUCCEEDED" if completed.returncode == 0 else "FAILED"}
    if operation == "create_session":
        return {"provider_reference": None, "continuity": {"lazy_thread": True}}
    if operation == "continue_session":
        if not arguments.get("provider_reference"): return {"continued": False}
        with CodexAppServer(executable, codex_home, _workspace(), timeout=20) as server:
            server.resume_thread(str(arguments["provider_reference"]))
        return {"continued": True, "provider_reference": arguments["provider_reference"]}
    if operation == "execute":
        _code_mode_host(executable)
        bridge = CodexApprovalBridge(str(arguments["session_id"]), str(arguments["execution_id"]),
            timeout=float(arguments.get("approval_timeout", 300)))
        try:
            with CodexAppServer(executable, codex_home, _workspace(), timeout=float(arguments.get("timeout", 600)),
                                approval_handler=bridge) as server:
                turn = server.execute(str(arguments["instructions"]), arguments.get("session_reference"))
        except (TimeoutError, EOFError, OSError, CodexProtocolError) as exc:
            return {"outcome": "UNKNOWN", "error_category": type(exc).__name__}
        mapping = {"completed": "SUCCEEDED", "failed": "FAILED", "interrupted": "CANCELLED"}
        outcome = mapping.get(turn.status, "UNKNOWN")
        category = None if outcome == "SUCCEEDED" else _classify(turn.error, turn.status)
        if category == "AUTHENTICATION_REQUIRED": outcome = "WAITING_AUTH"
        elif category == "QUOTA_OR_RATE_LIMIT": outcome = "WAITING_QUOTA"
        return {"outcome": outcome, "provider_session_reference": turn.thread_id,
                "provider_execution_reference": turn.turn_id,
                "structured_result": {"text": turn.text}, "error_category": category}
    if operation in {"observe", "cancel"}:
        return {"outcome": "UNSUPPORTED", "error_category": "CAPABILITY_UNVERIFIED"}
    raise ValueError("unknown operation")


class CodexApprovalBridge:
    METHODS = {"item/commandExecution/requestApproval": "COMMAND",
        "item/fileChange/requestApproval": "FILE_CHANGE",
        "item/permissions/requestApproval": "PERMISSIONS"}

    def __init__(self, session_id: str, execution_id: str, *, timeout: float = 300,
                 client: RuntimeApprovalClient | None = None):
        self.session_id, self.execution_id, self.timeout = session_id, execution_id, timeout
        self.client = client or RuntimeApprovalClient()

    def __call__(self, method: str, params: dict[str, Any], provider_ref: str) -> CodexApprovalReply:
        kind = self.METHODS.get(method)
        if kind is None: raise CodexProtocolError("solicitud App Server no reconocida")
        if not all(isinstance(params.get(key), str) and params[key] for key in ("threadId", "turnId", "itemId")):
            raise CodexProtocolError("solicitud App Server no correlacionada")
        details = {key: params[key] for key in ("command", "cwd", "reason", "kind", "permissions",
            "itemId", "threadId", "turnId") if key in params}
        if isinstance(details.get("command"), str): details["command"] = _sanitize(details["command"])[:2000]
        summary = (f"Comando: {details.get('command')}" if kind == "COMMAND" else
            f"Cambio de archivos: {details.get('reason') or params['itemId']}" if kind == "FILE_CHANGE" else
            f"Permisos adicionales: {details.get('reason') or params['itemId']}")
        try:
            approval = self.client.request("approval.register", {"session_id": self.session_id,
                "execution_id": self.execution_id, "provider_request_ref": provider_ref, "kind": kind,
                "summary": summary, "details": details, "allowed_decisions": ["APPROVE", "DENY"],
                "timeout_seconds": int(self.timeout)})
        except Exception as exc:
            diagnostic = Path(os.environ.get("HOME", "/nonexistent")) / "approval-diagnostic.json"
            try:
                _write_progress(diagnostic, "ERROR", json.dumps({"method": method,
                    "parameter_keys": sorted(params), "error": type(exc).__name__}))
            except OSError:
                pass
            raise
        deadline = time.monotonic() + self.timeout + 5
        while approval["status"] in {"PENDING", "DECISION_RECORDED"}:
            if time.monotonic() >= deadline: raise TimeoutError("aprobación runtime no resuelta")
            time.sleep(.2); approval = self.client.request("approval.claim", {"approval_id": approval["id"]})
        if approval["status"] not in {"DELIVERING", "EXPIRED"}:
            raise CodexProtocolError("aprobación runtime no entregable")
        approved = approval.get("decision") == "APPROVE" and approval["status"] != "EXPIRED"
        if kind in {"COMMAND", "FILE_CHANGE"}: response = {"decision": "accept" if approved else "decline"}
        elif approved: response = {"permissions": details.get("permissions", {}), "scope": "turn"}
        else: response = {"permissions": {"fileSystem": None, "network": None}, "scope": "turn"}
        return CodexApprovalReply(response, str(approval["id"]))

    def confirm(self, approval_id: str) -> None:
        self.client.request("approval.confirm", {"approval_id": approval_id, "delivered": True, "diagnostic": None})


def _workspace() -> Path | None:
    value = os.environ.get("VIBECODER_WORKSPACE")
    return Path(value).absolute() if value else None


def _sanitize(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)\bsk-[a-z0-9_-]{8,}\b", "[REDACTED]", value)
    value = re.sub(r'(?i)(access_token["\']?\s*[:=]\s*["\']?)[^\s"\']+', r"\1[REDACTED]", value)
    return value[-4096:]


def _write_progress(path: Path, status: str, display: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"status": status, "display": display}, output); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary, 0o600); os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def _classify(error: dict[str, Any] | None, status: str) -> str:
    text = json.dumps(error or {}).casefold()
    if "auth" in text or "unauthorized" in text: return "AUTHENTICATION_REQUIRED"
    if "quota" in text or "rate" in text: return "QUOTA_OR_RATE_LIMIT"
    return "TURN_" + status.upper()


def main() -> int:
    request: dict[str, Any] = {}
    try:
        request = _receive(); result = _result(request)
        response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": request.get("request_id"),
                    "worker_id": request.get("worker_id"), "ok": True, "result": result}
    except Exception as exc:
        response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": request.get("request_id"),
                    "worker_id": request.get("worker_id"), "ok": False,
                    "error": {"category": type(exc).__name__, "message": "operación Codex fallida"}}
    sys.stdout.buffer.write(encode_runtime(response)); sys.stdout.buffer.flush(); return 0


if __name__ == "__main__": raise SystemExit(main())
