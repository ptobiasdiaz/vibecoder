"""Root-owned, fixed-policy systemd worker launcher service."""
from __future__ import annotations
import argparse
import grp
import os
import socket
import stat
import struct
import sys
import threading
import pwd
from pathlib import Path
from typing import Any
from .runtime_protocol import MAX_RUNTIME_MESSAGE, RUNTIME_PROTOCOL_VERSION, decode_runtime, encode_runtime
from .worker_runtime import RuntimeLaunchSpec, SystemdWorkerLauncher

DEFAULT_SOCKET = Path("/run/vibecoder/worker-launch.sock")


def receive_frame(connection: socket.socket) -> bytes:
    """Read exactly one bounded frame; SOCK_STREAM may split any send."""
    header = bytearray()
    while len(header) < 4:
        chunk = connection.recv(4 - len(header))
        if not chunk: raise ValueError("frame truncado")
        header.extend(chunk)
    size = struct.unpack("!I", header)[0]
    if size > MAX_RUNTIME_MESSAGE: raise ValueError("frame demasiado grande")
    body = bytearray()
    while len(body) < size:
        chunk = connection.recv(min(65536, size - len(body)))
        if not chunk: raise ValueError("frame truncado")
        body.extend(chunk)
    return bytes(header + body)


def validate_request(value: dict[str, Any]) -> tuple[RuntimeLaunchSpec, dict[str, Any]]:
    if value.get("version") != RUNTIME_PROTOCOL_VERSION or value.get("operation") != "launch": raise ValueError("petición no válida")
    worker_id, args = value.get("worker_id"), value.get("arguments")
    if not isinstance(worker_id, str) or not isinstance(args, dict) or set(args) != {"unix_user","module","storage_path","workspace_path","timeout","runtime_request"}:
        raise ValueError("argumentos no válidos")
    compact = worker_id.replace("-", "")
    if args["unix_user"] != "vibecoder-w-" + compact[:12]: raise ValueError("usuario no corresponde al worker")
    storage = Path(str(args["storage_path"])); expected = Path("/var/lib/vibecoder/workers") / worker_id
    if storage != expected: raise ValueError("storage fuera del worker")
    workspace = Path(str(args["workspace_path"])) if args["workspace_path"] else None
    if workspace and (workspace.parent != Path("/var/lib/vibecoder/workspaces") or workspace.name.replace("-", "") == ""):
        raise ValueError("workspace fuera del área gestionada")
    timeout = float(args["timeout"])
    if not 1 <= timeout <= 900: raise ValueError("timeout fuera de rango")
    request = args["runtime_request"]
    if not isinstance(request, dict) or request.get("worker_id") != worker_id: raise ValueError("runtime request no correlacionada")
    spec = RuntimeLaunchSpec(worker_id, str(args["unix_user"]), str(args["module"]), storage, workspace, timeout)
    spec.validate(); return spec, request


def _handle(connection: socket.socket, launcher: SystemdWorkerLauncher, allowed_uid: int) -> None:
    with connection:
        value: dict[str, Any] = {}
        try:
            peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", peer)
            if uid != allowed_uid: raise PermissionError("cliente launcher no autorizado")
            raw = receive_frame(connection)
            value = decode_runtime(raw); spec, request = validate_request(value)
            result = launcher.invoke(spec, request)
            response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": value.get("request_id"),
                "worker_id": value.get("worker_id"), "ok": True, "result": result}
        except Exception as exc:
            category = getattr(exc, "details", {}).get("category") if hasattr(exc, "details") else None
            print(f"worker launcher: {type(exc).__name__}: category={category}", file=sys.stderr, flush=True)
            response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": value.get("request_id"),
                "worker_id": value.get("worker_id"), "ok": False,
                "error": {"category": type(exc).__name__, "message": "lanzamiento rechazado o fallido"}}
        connection.sendall(encode_runtime(response))


def serve(path: Path, group: int, python: str, allowed_uid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0: raise RuntimeError("socket launcher preexistente inseguro")
        path.unlink()
    old = os.umask(0o117)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try: server.bind(str(path))
    finally: os.umask(old)
    os.chown(path, 0, group); os.chmod(path, 0o660); server.listen(8)
    launcher = SystemdWorkerLauncher(python=python)
    while True:
        connection, _ = server.accept()
        threading.Thread(target=_handle, args=(connection, launcher, allowed_uid), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--group", default="vibecoder"); parser.add_argument("--python", default="/usr/bin/python3")
    args = parser.parse_args()
    if os.geteuid() != 0: raise SystemExit("launcher debe ejecutarse como root")
    gid = int(args.group) if str(args.group).isdigit() else grp.getgrnam(args.group).gr_gid
    serve(args.socket, gid, args.python, pwd.getpwnam("vibecoder").pw_uid); return 0


if __name__ == "__main__": raise SystemExit(main())
