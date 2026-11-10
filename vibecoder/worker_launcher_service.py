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
import re
import subprocess
from pathlib import Path
from typing import Any
from .runtime_protocol import MAX_RUNTIME_MESSAGE, RUNTIME_PROTOCOL_VERSION, decode_runtime, encode_runtime
from .worker_runtime import RuntimeLaunchSpec, SystemdWorkerLauncher

DEFAULT_SOCKET = Path("/run/vibecoder/worker-launch.sock")
_WORKER_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


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

def provision_worker(worker_id: str) -> dict[str, Any]:
    if not isinstance(worker_id,str) or not _WORKER_ID.fullmatch(worker_id):raise ValueError("worker id no válido")
    user="vibecoder-w-"+worker_id.replace("-","")[:12];storage=Path("/var/lib/vibecoder/workers")/worker_id
    try:account=pwd.getpwnam(user)
    except KeyError:
        result=subprocess.run(("/usr/sbin/useradd","--system","--user-group","--home-dir",str(storage),"--no-create-home","--shell","/usr/sbin/nologin","--groups","vibecoder-runtime","--",user),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,check=False,timeout=30)
        if result.returncode:raise RuntimeError("no se pudo crear identidad fija de worker")
        account=pwd.getpwnam(user)
    if account.pw_uid in {0,pwd.getpwnam("vibecoder").pw_uid}:raise RuntimeError("UID de worker inseguro")
    storage.mkdir(parents=True,exist_ok=True,mode=0o700);os.chown(storage,account.pw_uid,account.pw_gid);os.chmod(storage,0o700)
    for name in ("tmp","codex-home"):
        path=storage/name
        if path.is_symlink():raise RuntimeError("storage worker contiene symlink")
        path.mkdir(mode=0o700,exist_ok=True);os.chown(path,account.pw_uid,account.pw_gid);os.chmod(path,0o700)
    return {"unix_user":user,"storage_path":str(storage)}

def prepare_workspace(path: Path, unix_user: str) -> None:
    account=pwd.getpwnam(unix_user);root=Path("/var/lib/vibecoder/workspaces")
    if path.parent!=root or path.is_symlink() or not path.is_dir():raise ValueError("workspace inseguro")
    for current,dirs,files in os.walk(path,followlinks=False):
        current_path=Path(current)
        for name in dirs+files:
            candidate=current_path/name
            if candidate.is_symlink():
                try:candidate.resolve(strict=False).relative_to(path)
                except ValueError as exc:raise ValueError("symlink fuera del workspace") from exc
                os.lchown(candidate,account.pw_uid,account.pw_gid)
        os.chown(current_path,account.pw_uid,account.pw_gid)
        for name in files:
            candidate=current_path/name
            if candidate.is_symlink():continue
            mode=stat.S_IMODE(candidate.stat().st_mode)&0o700;os.chown(candidate,account.pw_uid,account.pw_gid);os.chmod(candidate,mode or 0o600)

def restore_workspace(path: Path) -> None:
    account=pwd.getpwnam("vibecoder")
    for current,dirs,files in os.walk(path,followlinks=False):
        current_path=Path(current);os.chown(current_path,account.pw_uid,account.pw_gid);os.chmod(current_path,0o700)
        for name in files:
            candidate=current_path/name
            if candidate.is_symlink():
                try:candidate.resolve(strict=False).relative_to(path)
                except ValueError as exc:raise ValueError("symlink fuera del workspace") from exc
                os.lchown(candidate,account.pw_uid,account.pw_gid);continue
            mode=stat.S_IMODE(candidate.stat().st_mode)&0o700;os.chown(candidate,account.pw_uid,account.pw_gid);os.chmod(candidate,mode or 0o600)


def _handle(connection: socket.socket, launcher: SystemdWorkerLauncher, allowed_uid: int) -> None:
    with connection:
        value: dict[str, Any] = {}
        try:
            peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", peer)
            if uid != allowed_uid: raise PermissionError("cliente launcher no autorizado")
            raw = receive_frame(connection)
            value = decode_runtime(raw)
            if value.get("operation")=="provision" and value.get("arguments")=={}:
                result=provision_worker(value.get("worker_id"))
            else:
                spec, request = validate_request(value)
                if spec.workspace_path:prepare_workspace(spec.workspace_path,spec.unix_user)
                try:result = launcher.invoke(spec, request)
                finally:
                    if spec.workspace_path:restore_workspace(spec.workspace_path)
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
