"""Constrained worker process launch boundary.

Production uses systemd to switch identity before Python loads provider code.
DirectProcessLauncher exists only for offline contract tests and is explicitly
not a security boundary.
"""
from __future__ import annotations

import os
import re
import math
import subprocess
import sys
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import OperationError
from .runtime_protocol import RUNTIME_PROTOCOL_VERSION, decode_runtime, encode_runtime

_MODULE = re.compile(r"vibecoder\.[a-zA-Z0-9_.]+\Z")
_USER = re.compile(r"vibecoder-w-[a-f0-9]{12}\Z")


@dataclass(frozen=True)
class RuntimeLaunchSpec:
    worker_id: str
    unix_user: str
    module: str
    storage_path: Path
    workspace_path: Path | None = None
    timeout: float = 30.0
    protected_paths: tuple[Path, ...] = (Path("/var/lib/vibecoder/secrets"),
        Path("/var/lib/vibecoder/vibecoder.db"), Path("/run/vibecoder/control.sock"))

    def validate(self) -> None:
        if not _USER.fullmatch(self.unix_user): raise OperationError("identidad Unix de worker no válida")
        if not _MODULE.fullmatch(self.module): raise OperationError("módulo runtime no confiable")
        if not self.storage_path.is_absolute() or (self.workspace_path and not self.workspace_path.is_absolute()):
            raise OperationError("rutas de runtime deben ser absolutas")


class WorkerProcessLauncher(Protocol):
    security_boundary: bool
    def invoke(self, spec: RuntimeLaunchSpec, request: Mapping[str, Any]) -> dict[str, Any]: ...


def _request(spec: RuntimeLaunchSpec, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"version": RUNTIME_PROTOCOL_VERSION, "request_id": os.urandom(16).hex(),
            "worker_id": spec.worker_id, "operation": operation, "arguments": dict(arguments)}


class SystemdWorkerLauncher:
    """Launch a trusted module in a transient, fixed-policy systemd unit."""
    security_boundary = True
    def __init__(self, executable: str = "/usr/bin/systemd-run", python: str = "/usr/bin/python3"):
        self.executable, self.python = executable, python

    def invoke(self, spec: RuntimeLaunchSpec, request: Mapping[str, Any]) -> dict[str, Any]:
        spec.validate()
        command = [self.executable, "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec",
            f"--uid={spec.unix_user}", f"--unit=vibecoder-worker-{spec.worker_id[:12]}-{os.urandom(4).hex()}",
            "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes", "--property=RestrictSUIDSGID=yes", "--property=LockPersonality=yes",
            "--property=CapabilityBoundingSet=", "--property=UMask=0077", "--property=KillMode=control-group",
            "--property=SupplementaryGroups=vibecoder-runtime",
            f"--property=RuntimeMaxSec={math.ceil(spec.timeout) + 5}",
            # A protected endpoint may legitimately be absent while the daemon
            # is stopped.  systemd's '-' prefix keeps the path inaccessible
            # when present without making namespace setup depend on existence.
            "--property=InaccessiblePaths=" + " ".join("-" + str(x) for x in spec.protected_paths),
            f"--property=ReadWritePaths={spec.storage_path}",
            f"--setenv=HOME={spec.storage_path}", f"--setenv=TMPDIR={spec.storage_path / 'tmp'}",
            "--setenv=LANG=C.UTF-8", "--setenv=LC_ALL=C.UTF-8",
            "--setenv=VIBECODER_APPROVAL_SOCKET=/run/vibecoder/runtime-approval.sock",
            f"--setenv=VIBECODER_WORKER_ID={spec.worker_id}"]
        if spec.workspace_path:
            command.append(f"--property=ReadWritePaths={spec.workspace_path}")
            command.append(f"--setenv=VIBECODER_WORKSPACE={spec.workspace_path}")
        command.extend(("--", self.python, "-I", "-m", spec.module))
        return _run(command, spec, request)


class PrivilegedWorkerLauncherClient:
    """Narrow client to the root launcher service; not an administrative API."""
    security_boundary = True
    def __init__(self, socket_path: Path = Path("/run/vibecoder/worker-launch.sock"), timeout: float = 700):
        self.socket_path, self.timeout = socket_path, timeout

    def invoke(self, spec: RuntimeLaunchSpec, request: Mapping[str, Any]) -> dict[str, Any]:
        spec.validate()
        payload = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": request.get("request_id"),
            "worker_id": spec.worker_id, "operation": "launch", "arguments": {
                "unix_user": spec.unix_user, "module": spec.module,
                "storage_path": str(spec.storage_path),
                "workspace_path": str(spec.workspace_path) if spec.workspace_path else None,
                "timeout": spec.timeout, "runtime_request": dict(request)}}
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path)); connection.sendall(encode_runtime(payload))
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk: break
                chunks.append(chunk)
        except OSError as exc: raise OperationError("launcher privilegiado no disponible", details={"category": "LAUNCHER_UNAVAILABLE"}) from exc
        finally: connection.close()
        try: response = decode_runtime(b"".join(chunks))
        except (ValueError, UnicodeError) as exc: raise OperationError("respuesta inválida del launcher") from exc
        if not response.get("ok"):
            error = response.get("error", {}); raise OperationError(str(error.get("message", "fallo del launcher")),
                details={"category": str(error.get("category", "LAUNCH_FAILED"))})
        result = response.get("result")
        if not isinstance(result, dict): raise OperationError("resultado launcher inválido")
        return result

    def provision(self, worker_id: str) -> dict[str, Any]:
        payload={"version":RUNTIME_PROTOCOL_VERSION,"request_id":os.urandom(16).hex(),"worker_id":worker_id,"operation":"provision","arguments":{}}
        return self._exchange(payload)

    def _exchange(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path));connection.sendall(encode_runtime(payload));chunks=[]
            while True:
                chunk=connection.recv(65536)
                if not chunk:break
                chunks.append(chunk)
        except OSError as exc:raise OperationError("launcher privilegiado no disponible",details={"category":"LAUNCHER_UNAVAILABLE"}) from exc
        finally:connection.close()
        response=decode_runtime(b"".join(chunks))
        if not response.get("ok"):raise OperationError("aprovisionamiento de worker rechazado",details={"category":str(response.get("error",{}).get("category","PROVISION_FAILED"))})
        result=response.get("result")
        if not isinstance(result,dict):raise OperationError("respuesta de aprovisionamiento inválida")
        return result


class DirectProcessLauncher:
    """Offline-test launcher. Same UID: never claim production isolation."""
    security_boundary = False
    def invoke(self, spec: RuntimeLaunchSpec, request: Mapping[str, Any]) -> dict[str, Any]:
        spec.validate()
        return _run([sys.executable, "-I", "-m", spec.module], spec, request)


def _run(command: list[str], spec: RuntimeLaunchSpec, request: Mapping[str, Any]) -> dict[str, Any]:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "HOME": str(spec.storage_path), "TMPDIR": str(spec.storage_path / "tmp"),
           "VIBECODER_WORKER_ID": spec.worker_id}
    if spec.workspace_path is not None:
        env["VIBECODER_WORKSPACE"] = str(spec.workspace_path)
    try:
        result = subprocess.run(command, input=encode_runtime(request), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=spec.timeout, check=False, close_fds=True, env=env)
    except subprocess.TimeoutExpired as exc:
        raise OperationError("timeout del runtime del worker", details={"category": "TIMEOUT"}) from exc
    except OSError as exc:
        raise OperationError("no se pudo iniciar el runtime aislado", details={"category": "LAUNCH_FAILED"}) from exc
    if result.returncode:
        raise OperationError("el proceso del worker terminó inesperadamente",
                             details={"category": "PROCESS_CRASH", "returncode": result.returncode})
    try: response = decode_runtime(result.stdout)
    except (ValueError, UnicodeError) as exc: raise OperationError("respuesta inválida del runtime") from exc
    if response.get("request_id") != request.get("request_id") or response.get("worker_id") != spec.worker_id:
        raise OperationError("respuesta de runtime no correlacionada")
    if not response.get("ok"):
        error = response.get("error", {})
        raise OperationError(str(error.get("message", "error del runtime")),
                             details={"category": str(error.get("category", "RUNTIME_ERROR"))})
    result_value = response.get("result")
    if not isinstance(result_value, dict): raise OperationError("resultado runtime inválido")
    return result_value


def runtime_request(spec: RuntimeLaunchSpec, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _request(spec, operation, arguments)
