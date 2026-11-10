"""Local vibecoderd process for M06.2."""
from __future__ import annotations

import argparse
import grp
import os
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
from pathlib import Path
from typing import Callable

from .control_protocol import (
    MAX_MESSAGE_SIZE, ControlRequest, ProtocolError, failure, receive_frame,
    send_frame, success,
)
from .control_service import (
    AuthorizationError, ControlService, LocalAuthorizer, PeerIdentity,
    UnknownOperation,
)
from .errors import CoreError
from .errors import OperationError
from .github_identity import ConnectivityMonitor, GitHubIdentityService
from .secret_store import FileSecretStore
from .projects import HttpGitHubProjectAPI, ProjectService
from .plugin_registry import PluginRegistry
from .worker_runtime import PrivilegedWorkerLauncherClient, SystemdWorkerLauncher
from .workers import WorkerService, PrivilegedUnixIdentityProvisioner
from .codex_installation import CodexInstallationManager
from .sqlite import SQLiteRepository
from .approvals import RuntimeApprovalService
from .runtime_approval_protocol import DEFAULT_RUNTIME_APPROVAL_SOCKET, RuntimeApprovalServer
from .workflows_v1 import WorkflowService
from .scheduler_v1 import PersistentScheduler
from .maintenance_v1 import MaintenanceService

DEFAULT_DATABASE = Path("/var/lib/vibecoder/vibecoder.db")
DEFAULT_SOCKET = Path("/run/vibecoder/control.sock")
DEFAULT_SECRETS = Path("/var/lib/vibecoder/secrets")
DEFAULT_WORKSPACES = Path("/var/lib/vibecoder/workspaces")
DEFAULT_WORKERS = Path("/var/lib/vibecoder/workers")
DEFAULT_PLUGIN_BINARIES = Path("/var/lib/vibecoder/plugins")
DEFAULT_CODEX_MANIFEST = Path("/usr/share/vibecoder/codex-artifacts.json")


def supplementary_groups(pid: int, expected_uid: int) -> frozenset[int]:
    """Read kernel-maintained process groups; never accept groups from JSON."""
    try:
        fields = {}
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        uids = [int(value) for value in fields.get("Uid", "").split()]
        if not uids or uids[1] != expected_uid:
            return frozenset()
        return frozenset(int(value) for value in fields.get("Groups", "").split())
    except (OSError, ValueError, IndexError):
        return frozenset()


def socket_peer(connection: socket.socket) -> PeerIdentity:
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return PeerIdentity(pid, uid, gid, supplementary_groups(pid, uid))


def prepare_socket_path(path: Path) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError(f"directorio runtime inseguro: {path.parent}")
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o002:
        raise RuntimeError(f"propietario o permisos inseguros en: {path.parent}")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
        raise RuntimeError(f"se rechaza ruta de socket preexistente insegura: {path}")
    if info.st_uid != os.geteuid():
        raise RuntimeError(f"el socket preexistente pertenece a otro usuario: {path}")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.1)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink()
    else:
        raise RuntimeError(f"ya existe un daemon activo en {path}")
    finally:
        probe.close()


class ControlRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        connection: socket.socket = self.request
        connection.settimeout(self.server.request_timeout)
        request_id = None
        peer = None
        try:
            peer = self.server.peer_resolver(connection)
            payload = receive_frame(connection, max_size=self.server.max_message_size)
            if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                request_id = payload["request_id"]
            request = ControlRequest.from_payload(payload)
            request_id = request.request_id
            result = self.server.control_service.dispatch(
                request.operation, request.arguments, peer)
            response = success(request.request_id, result)
        except ProtocolError as exc:
            response = failure(request_id, exc.code, str(exc))
        except AuthorizationError as exc:
            response = failure(request_id, "FORBIDDEN", str(exc))
        except UnknownOperation as exc:
            response = failure(request_id, "UNKNOWN_OPERATION", str(exc))
        except ValueError as exc:
            response = failure(request_id, "INVALID_ARGUMENTS", str(exc))
        except (EOFError, BrokenPipeError, ConnectionResetError):
            return
        except OperationError as exc:
            response = failure(request_id, exc.code, str(exc), exc.details)
        except CoreError as exc:
            response = failure(request_id, "CORE_ERROR", str(exc))
        except Exception as exc:  # stable boundary; details remain in daemon logs later
            if peer is not None:
                self.server.control_service.audit(peer.actor, "request", "control", None,
                                                  "ERROR", type(exc).__name__)
            response = failure(request_id, "INTERNAL_ERROR", "error interno del daemon")
        try:
            send_frame(connection, response, max_size=self.server.max_message_size)
        except (OSError, ProtocolError):
            return


class ControlServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, socket_path: Path, service: ControlService, *,
                 request_timeout: float = 5.0,
                 max_message_size: int = MAX_MESSAGE_SIZE,
                 peer_resolver: Callable[[socket.socket], PeerIdentity] = socket_peer):
        self.control_service = service
        self.request_timeout = request_timeout
        self.max_message_size = max_message_size
        self.peer_resolver = peer_resolver
        super().__init__(str(socket_path), ControlRequestHandler)


class VibecoderDaemon:
    def __init__(self, database_path: Path, socket_path: Path, *, admin_gid: int,
                 socket_gid: int | None = None, request_timeout: float = 5.0,
                 max_message_size: int = MAX_MESSAGE_SIZE,
                 peer_resolver: Callable[[socket.socket], PeerIdentity] = socket_peer,
                 secrets_path: Path | None = None,
                 verifier_factory=None, workspaces_path: Path | None = None,
                 project_api_factory=None, workspace_preparer=None,
                 workers_path: Path | None = None, worker_launcher=None,
                 worker_provisioner=None, codex_installation=None,
                 worker_python: str = "/usr/bin/python3", worker_helper: str | None = None,
                 runtime_approval_socket: Path | None = None,
                 runtime_approval_gid: int | None = None):
        self.database_path = database_path
        self.socket_path = socket_path
        self.admin_gid = admin_gid
        self.socket_gid = admin_gid if socket_gid is None else socket_gid
        self.request_timeout = request_timeout
        self.max_message_size = max_message_size
        self.peer_resolver = peer_resolver
        self.secrets_path = secrets_path or database_path.parent / "secrets"
        self.verifier_factory = verifier_factory
        self.workspaces_path = workspaces_path or database_path.parent / "workspaces"
        self.project_api_factory = project_api_factory or HttpGitHubProjectAPI
        self.workspace_preparer = workspace_preparer
        self.workers_path = workers_path or database_path.parent / "workers"
        self.worker_launcher = worker_launcher
        self.worker_provisioner = worker_provisioner
        self.codex_installation = codex_installation
        self.worker_python, self.worker_helper = worker_python, worker_helper
        self.runtime_approval_socket = runtime_approval_socket or socket_path.with_name("runtime-approval.sock")
        self.runtime_approval_gid = runtime_approval_gid
        self.repository: SQLiteRepository | None = None
        self.service: ControlService | None = None
        self.server: ControlServer | None = None
        self.monitor: ConnectivityMonitor | None = None
        self.workers: WorkerService | None = None
        self.approvals: RuntimeApprovalService | None = None
        self.approval_server: RuntimeApprovalServer | None = None
        self.approval_thread: threading.Thread | None = None
        self.stopping = False
        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_stop = threading.Event()

    def start(self) -> None:
        prepare_socket_path(self.socket_path)
        self.repository = SQLiteRepository(self.database_path)
        self.approvals = RuntimeApprovalService(self.repository); self.approvals.recover()
        secrets = FileSecretStore(self.secrets_path)
        identity = GitHubIdentityService(self.repository, secrets,
            **({"verifier_factory": self.verifier_factory} if self.verifier_factory else {}))
        projects = ProjectService(self.repository, identity, self.project_api_factory,
                                  self.workspaces_path, self.workspace_preparer)
        plugins = PluginRegistry(self.repository)
        launcher = self.worker_launcher or (PrivilegedWorkerLauncherClient(Path(self.worker_helper))
                                    if self.worker_helper else SystemdWorkerLauncher(python=self.worker_python))
        provisioner = self.worker_provisioner or (PrivilegedUnixIdentityProvisioner(launcher) if self.worker_helper else None)
        workers = WorkerService(self.repository, plugins, self.workers_path, launcher,
                                provisioner, self.admin_gid,
                                {"codex": self.codex_installation or CodexInstallationManager(
                                    DEFAULT_PLUGIN_BINARIES / "codex", DEFAULT_CODEX_MANIFEST)})
        self.workers = workers
        workflows = WorkflowService(self.repository, identity, workers)
        scheduler = PersistentScheduler(self.repository, workflows)
        scheduler.recover()
        maintenance = MaintenanceService(self.repository, self.database_path.parent)
        self.service = ControlService(self.repository, LocalAuthorizer(self.admin_gid), identity,
                                      projects, plugins, workers, self.approvals, scheduler, maintenance)
        self.monitor = ConnectivityMonitor(identity)
        old_umask = os.umask(0o077)
        try:
            self.server = ControlServer(self.socket_path, self.service,
                request_timeout=self.request_timeout, max_message_size=self.max_message_size,
                peer_resolver=self.peer_resolver)
        finally:
            os.umask(old_umask)
        try:
            os.chown(self.socket_path, os.geteuid(), self.socket_gid)
            os.chmod(self.socket_path, 0o660)
            prepare_socket_path(self.runtime_approval_socket)
            self.approval_server = RuntimeApprovalServer(self.runtime_approval_socket, self.database_path)
            os.chown(self.runtime_approval_socket, os.geteuid(), self.runtime_approval_gid if self.runtime_approval_gid is not None else self.socket_gid)
            os.chmod(self.runtime_approval_socket, 0o660)
            self.approval_thread = threading.Thread(target=self.approval_server.serve_forever,
                name="runtime-approvals", daemon=True); self.approval_thread.start()
            self.scheduler_stop.clear()
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop,
                name="workflow-scheduler", daemon=True); self.scheduler_thread.start()
        except Exception:
            self.server.server_close()
            self.socket_path.unlink(missing_ok=True)
            self.repository.close()
            raise
        self.server.timeout = 0.2
        self.service.audit("daemon", "start", "daemon", None, "SUCCEEDED", "daemon iniciado")

    def request_stop(self, signum: int | None = None, frame: object = None) -> None:
        self.stopping = True
        self.scheduler_stop.set()

    def _scheduler_loop(self) -> None:
        """Use an independent SQLite connection so remote polling never blocks control."""
        repository = None
        workers = None
        try:
            repository = SQLiteRepository(self.database_path)
            secrets = FileSecretStore(self.secrets_path)
            identity = GitHubIdentityService(repository, secrets,
                **({"verifier_factory": self.verifier_factory} if self.verifier_factory else {}))
            plugins = PluginRegistry(repository)
            launcher=self.worker_launcher or (PrivilegedWorkerLauncherClient(Path(self.worker_helper)) if self.worker_helper else SystemdWorkerLauncher(python=self.worker_python))
            provisioner=self.worker_provisioner or (PrivilegedUnixIdentityProvisioner(launcher) if self.worker_helper else None)
            workers = WorkerService(repository, plugins, self.workers_path, launcher,
                provisioner, self.admin_gid,
                {"codex": self.codex_installation or CodexInstallationManager(DEFAULT_PLUGIN_BINARIES / "codex", DEFAULT_CODEX_MANIFEST)})
            scheduler = PersistentScheduler(repository, WorkflowService(repository, identity, workers))
            scheduler.recover()
            while not self.scheduler_stop.wait(1.0):
                try: scheduler.cycle()
                except Exception:
                    # Jobs retain their durable failure state; daemon availability
                    # must not depend on an external service.
                    continue
        finally:
            if workers is not None: workers.close()
            if repository is not None: repository.close()

    def run(self) -> None:
        self.start()
        assert self.server is not None
        try:
            while not self.stopping:
                self.server.handle_request()
                if self.monitor is not None:
                    self.monitor.cycle()
        finally:
            self.close()

    def close(self) -> None:
        try:
            if self.service is not None and self.repository is not None:
                self.service.audit("daemon", "stop", "daemon", None, "SUCCEEDED", "daemon detenido")
        finally:
            try:
                if self.server is not None:
                    self.server.server_close()
                    self.server = None
                self.socket_path.unlink(missing_ok=True)
                if self.approval_server is not None:
                    self.approval_server.shutdown(); self.approval_server.server_close(); self.approval_server = None
                self.runtime_approval_socket.unlink(missing_ok=True)
                self.scheduler_stop.set()
                if self.scheduler_thread is not None:
                    self.scheduler_thread.join(timeout=5); self.scheduler_thread = None
            finally:
                if self.repository is not None:
                    if self.workers is not None:
                        self.workers.close(); self.workers = None
                    self.repository.close()
                    self.repository = None


def _group_id(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        try:
            return grp.getgrnam(value).gr_gid
        except KeyError as exc:
            raise argparse.ArgumentTypeError(f"grupo inexistente: {value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibecoderd", description="daemon local de vibecoder")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--secrets", type=Path, default=DEFAULT_SECRETS)
    parser.add_argument("--workspaces", type=Path, default=DEFAULT_WORKSPACES)
    parser.add_argument("--admin-group", default="vibecoder-admin")
    parser.add_argument("--socket-group")
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--worker-python", default="/usr/bin/python3")
    parser.add_argument("--worker-helper", help="socket del launcher root de workers")
    parser.add_argument("--runtime-approval-socket", type=Path, default=DEFAULT_RUNTIME_APPROVAL_SOCKET)
    parser.add_argument("--runtime-group", default="vibecoder-runtime")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    admin_gid = _group_id(args.admin_group)
    socket_gid = _group_id(args.socket_group) if args.socket_group is not None else None
    runtime_gid = _group_id(args.runtime_group)
    daemon = VibecoderDaemon(args.database, args.socket, admin_gid=admin_gid,
                             socket_gid=socket_gid,
                             request_timeout=args.request_timeout,
                             secrets_path=args.secrets, workspaces_path=args.workspaces,
                             worker_python=args.worker_python, worker_helper=args.worker_helper,
                             runtime_approval_socket=args.runtime_approval_socket,
                             runtime_approval_gid=runtime_gid)
    signal.signal(signal.SIGTERM, daemon.request_stop)
    signal.signal(signal.SIGINT, daemon.request_stop)
    try:
        daemon.run()
    except Exception as exc:
        print(f"vibecoderd: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
