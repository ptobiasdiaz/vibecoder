"""Unified vibecoder CLI. It never opens SQLite directly."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import stat
import sys
import uuid
import time
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .control_protocol import (
    MAX_MESSAGE_SIZE, PROTOCOL_VERSION, ProtocolError, receive_frame, send_frame,
)
from .daemon import DEFAULT_SOCKET
from .git_local import LocalGitInspector


class DaemonUnavailable(RuntimeError):
    pass


class DaemonPermissionDenied(RuntimeError):
    pass


class RemoteError(RuntimeError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


class ControlClient:
    def __init__(self, socket_path: Path = DEFAULT_SOCKET, *, timeout: float = 15.0,
                 max_message_size: int = MAX_MESSAGE_SIZE):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.max_message_size = max_message_size

    def request(self, operation: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path))
            send_frame(connection, {"version": PROTOCOL_VERSION, "request_id": request_id,
                "operation": operation, "arguments": dict(arguments or {})},
                max_size=self.max_message_size)
            response = receive_frame(connection, max_size=self.max_message_size)
        except PermissionError as exc:
            raise DaemonPermissionDenied(
                "acceso denegado al socket; solicite pertenencia a vibecoder-admin "
                "con: sudo usermod -aG vibecoder-admin \"$USER\" y vuelva a iniciar sesión") from exc
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, EOFError, OSError) as exc:
            raise DaemonUnavailable(f"daemon no disponible en {self.socket_path}: {exc}") from exc
        finally:
            connection.close()
        if not isinstance(response, dict) or response.get("request_id") != request_id:
            raise DaemonUnavailable("respuesta inválida o no correlacionada del daemon")
        if response.get("version") != PROTOCOL_VERSION:
            raise DaemonUnavailable("versión de protocolo incompatible en la respuesta")
        if not response.get("ok"):
            error = response.get("error", {})
            raise RemoteError(str(error.get("code", "REMOTE_ERROR")),
                              str(error.get("message", "error remoto")), error.get("details"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise DaemonUnavailable("resultado inválido del daemon")
        return result


def socket_diagnostic(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False, "status": "ERROR"}
    mode = stat.S_IMODE(info.st_mode)
    return {"path": str(path), "exists": True,
            "is_socket": stat.S_ISSOCK(info.st_mode), "mode": f"{mode:04o}",
            "uid": info.st_uid, "gid": info.st_gid,
            "status": "OK" if stat.S_ISSOCK(info.st_mode) and mode == 0o660 else "ERROR"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibecoder", description="control local de vibecoder")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--socket", type=Path,
                        default=Path(os.environ.get("VIBECODER_SOCKET", DEFAULT_SOCKET)))
    parser.add_argument("--timeout", type=float, default=15.0)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("status", "muestra el estado operativo"),
                            ("doctor", "diagnostica daemon y persistencia")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true", dest="as_json")
    commands.add_parser("pause", help="activa la pausa administrativa global")
    commands.add_parser("resume", help="levanta únicamente la pausa administrativa global")
    github = commands.add_parser("github", help="gestiona la identidad GitHub global")
    github_commands = github.add_subparsers(dest="github_command", required=True)
    github_commands.add_parser("login", help="valida y almacena un Fine-grained PAT")
    github_status = github_commands.add_parser("status", help="muestra identidad y conectividad")
    github_status.add_argument("--json", action="store_true", dest="as_json")
    project = commands.add_parser("project", help="registra y administra proyectos")
    project.add_argument("project_args", nargs="+")
    project.add_argument("--ignore-dirty", action="store_true")
    project.add_argument("--branch")
    project.add_argument("--long", action="store_true", dest="long_output")
    project.add_argument("--json", action="store_true", dest="as_json")
    plugin = commands.add_parser("plugin", help="consulta plugins confiables")
    plugin.add_argument("plugin_args", nargs="+")
    plugin.add_argument("--json", action="store_true", dest="as_json")
    worker = commands.add_parser("worker", help="administra workers")
    worker.add_argument("worker_args", nargs="+")
    worker.add_argument("--name")
    worker.add_argument("--long", action="store_true", dest="long_output")
    worker.add_argument("--json", action="store_true", dest="as_json")
    approval = commands.add_parser("approval", help="gestiona aprobaciones de ejecución")
    approval.add_argument("approval_args", nargs="+")
    approval.add_argument("--json", action="store_true", dest="as_json")
    approval.add_argument("--pending", action="store_true")
    approval.add_argument("--worker")
    return parser


def _print_status(result: Mapping[str, Any]) -> None:
    print(f"Estado: {result['operational_state']}")
    print(f"GitHub: {result['connectivity']}")
    print(f"Capacidad de ejecución: {result['execution_capacity']}")
    print(f"Pausa administrativa: {'sí' if result['administratively_paused'] else 'no'}")
    print(f"Barrera de recuperación: {'sí' if result['recovery_barrier'] else 'no'}")
    print(f"Proyectos: {result['project_count']}")
    print(f"Workers operativos: {result['ready_worker_count']}")
    print("Daemon: disponible")


def _print_doctor(result: Mapping[str, Any]) -> None:
    for check in result["checks"]:
        detail = f" — {check['detail']}" if check.get("detail") else ""
        print(f"{check['status']}: {check['name']}{detail}")
    sock = result["socket"]
    print(f"{sock['status']}: socket — {sock['path']} mode={sock.get('mode', 'n/a')}")


def _print_github_status(result: Mapping[str, Any]) -> None:
    if not result["configured"]:
        print("GitHub: no configurado")
        return
    print(f"GitHub: {result['login']} (ID {result['github_user_id']})")
    print(f"Conectividad: {result['connectivity']}")
    print(f"Configurado: {result['configured_at']}")
    print(f"Última comprobación: {result['checked_at']}")
    diagnostic = result.get("diagnostic") or {}
    if diagnostic.get("message"):
        print(f"Diagnóstico: {diagnostic['message']}")


def _read_pat() -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("github login requiere un terminal interactivo; el PAT no se acepta por argumentos")
    print("Se requiere un Fine-grained Personal Access Token con acceso a los repositorios elegidos.")
    print("Permisos previstos: Metadata (read), Contents, Issues y Pull requests (read/write).")
    print("La comprobación de identidad no demuestra permisos de escritura.")
    return getpass.getpass("Fine-grained PAT: ")


def _project_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    values = args.project_args
    if values[0] == "add":
        if len(values) > 2:
            raise RuntimeError("uso: vibecoder project add [ruta]")
        path = Path(values[1]) if len(values) == 2 else Path.cwd()
        observation = LocalGitInspector().inspect(path).to_payload()
        if args.ignore_dirty:
            print("WARNING: se ignorarán cambios y commits locales; el workspace partirá del SHA publicado.", file=sys.stderr)
        return "project.add", {"observation": observation, "branch": args.branch,
                               "ignore_dirty": args.ignore_dirty}
    if values == ["list"]:
        return "project.list", {}
    if len(values) < 2:
        raise RuntimeError("indique una acción: status, authorize, deauthorize o users")
    project, action = values[0], values[1]
    if action in {"status", "users"} and len(values) == 2:
        return f"project.{action}", {"project": project}
    if action in {"authorize", "deauthorize"} and len(values) == 3:
        return f"project.{action}", {"project": project, "login": values[2]}
    raise RuntimeError("acción o argumentos de proyecto no válidos")


def _print_project(args: argparse.Namespace, result: Mapping[str, Any]) -> None:
    values = args.project_args
    if values[0] == "list":
        projects = result["projects"]
        if not projects:
            print("No hay proyectos registrados")
        for item in projects:
            if args.long_output:
                print(f"{item['canonical_name']}  {item['id']}  {item['base_branch']}@{item['remote_sha']}")
            else:
                print(f"{item['name']}  {item['canonical_name']}")
    elif len(values) >= 2 and values[1] == "users":
        for user in result["users"]:
            print(f"{user['login']} (ID {user['github_user_id']})")
    elif values[0] == "add" or (len(values) >= 2 and values[1] == "status"):
        print(f"Proyecto: {result['canonical_name']}")
        print(f"Rama base: {result['base_branch']}")
        print(f"SHA remoto: {result['remote_sha']}")
        if "changed" in result: print("Registrado" if result["changed"] else "Ya estaba registrado")
    else:
        print("Autorización actualizada" if result["changed"] else "Sin cambios")


def _plugin_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.plugin_args == ["list"]: return "plugin.list", {}
    if len(args.plugin_args) == 2 and args.plugin_args[0] == "show": return "plugin.show", {"plugin": args.plugin_args[1]}
    raise RuntimeError("uso: vibecoder plugin list | plugin show PLUGIN")


def _worker_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    values = args.worker_args
    if values == ["list"]: return "worker.list", {}
    if len(values) == 2 and values[0] == "add": return "worker.add", {"plugin": values[1], "name": args.name}
    if len(values) == 2 and values[1] in {"status", "enable", "disable", "login", "logout", "verify"}:
        return f"worker.{values[1]}", {"worker": values[0]}
    raise RuntimeError("acción o argumentos de worker no válidos")


def _print_plugin(args: argparse.Namespace, result: Mapping[str, Any]) -> None:
    items = result["plugins"] if args.plugin_args == ["list"] else [result]
    for item in items:
        state = "disponible" if item["available"] and item["compatible"] else "no disponible"
        print(f"{item['id']} {item['version']} ({item['provider']}) — {state}")
        if item.get("diagnostic"): print(f"  {item['diagnostic']}")


def _print_worker(args: argparse.Namespace, result: Mapping[str, Any]) -> None:
    if "worker" in result:
        display = result.get("display")
        if display: print(str(display), end="" if str(display).endswith("\n") else "\n")
        print(f"Autenticación: {result.get('outcome', 'UNKNOWN')}")
        result = result["worker"]
    items = result["workers"] if args.worker_args == ["list"] else [result]
    if not items: print("No hay workers configurados")
    for item in items:
        if args.long_output or args.worker_args != ["list"]:
            print(f"{item['name']}  {item['id']}  {item['plugin_id']}@{item['plugin_version']}  {item['status']}")
            if item.get("last_error"): print(f"  Diagnóstico: {item['last_error']}")
        else: print(f"{item['name']}  {item['status']}")


def _approval_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    values = args.approval_args
    if values == ["list"]:
        return "approval.list", {"pending": args.pending, "worker": args.worker}
    if len(values) == 2 and values[0] in {"show", "approve", "deny"}:
        return f"approval.{values[0]}", {"approval": values[1]}
    raise RuntimeError("uso: vibecoder approval list|show ID|approve ID|deny ID")


def _print_approval(args: argparse.Namespace, result: Mapping[str, Any]) -> None:
    items = result["approvals"] if args.approval_args == ["list"] else [result]
    if not items: print("No hay solicitudes de aprobación")
    for item in items:
        print(f"{item['id']}  {item['status']}  {item['kind']}  worker={item['worker_id']}")
        print(f"  {item['summary']}")
        if args.approval_args != ["list"]:
            print("  Detalles: " + json.dumps(item["details"], ensure_ascii=False, sort_keys=True))
            print("  Decisiones: " + ", ".join(item["allowed_decisions"]))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = ControlClient(args.socket, timeout=args.timeout)
    try:
        operation = args.command
        arguments = None
        if args.command == "github":
            operation = f"github.{args.github_command}"
            if args.github_command == "login":
                arguments = {"token": _read_pat()}
        elif args.command == "approval":
            operation, arguments = _approval_request(args)
            if args.approval_args[0] in {"approve", "deny"}:
                if not sys.stdin.isatty(): raise RuntimeError("la decisión requiere un terminal interactivo")
                preview = client.request("approval.show", {"approval": args.approval_args[1]})
                _print_approval(argparse.Namespace(approval_args=["show", args.approval_args[1]]), preview)
                expected = "APROBAR" if args.approval_args[0] == "approve" else "DENEGAR"
                if input(f"Escriba {expected} para confirmar: ").strip() != expected:
                    raise RuntimeError("decisión cancelada")
        elif args.command == "project":
            operation, arguments = _project_request(args)
        elif args.command == "plugin":
            operation, arguments = _plugin_request(args)
        elif args.command == "worker":
            operation, arguments = _worker_request(args)
        result = client.request(operation, arguments)
        if operation == "worker.login" and result.get("status") == "RUNNING":
            operation_id = str(result["operation_id"]); worker_name = args.worker_args[0]
            shown = ""
            while result.get("status") == "RUNNING":
                time.sleep(0.2)
                result = client.request("worker.login-status", {"worker": worker_name, "operation_id": operation_id})
                display = str(result.get("display", ""))
                if display and display != shown and not getattr(args, "as_json", False):
                    print(display, end="" if display.endswith("\n") else "\n"); shown = display
        if args.command == "doctor":
            result = dict(result)
            result["socket"] = socket_diagnostic(args.socket)
        if getattr(args, "as_json", False):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.command == "status":
            _print_status(result)
        elif args.command == "doctor":
            _print_doctor(result)
        elif args.command == "github":
            _print_github_status(result)
        elif args.command == "project":
            _print_project(args, result)
        elif args.command == "plugin":
            _print_plugin(args, result)
        elif args.command == "worker":
            _print_worker(args, result)
        elif args.command == "approval":
            _print_approval(args, result)
        elif args.command == "pause":
            print("Pausa administrativa activa" if result["changed"] else "La pausa ya estaba activa")
        else:
            print("Pausa administrativa desactivada" if result["changed"] else "No había una pausa activa")
        return 0
    except DaemonPermissionDenied as exc:
        print(f"autorización: {exc}", file=sys.stderr)
        return 4
    except DaemonUnavailable as exc:
        print(f"conexión: daemon no disponible: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        if not isinstance(exc, (RemoteError, ProtocolError, DaemonUnavailable, DaemonPermissionDenied)):
            print(f"entrada: {exc}", file=sys.stderr)
            return 2
        details = exc.details if isinstance(exc, RemoteError) else {}
        print(f"operación: {exc}", file=sys.stderr)
        if details.get("accepted_permissions"):
            print("Permisos aceptados por el endpoint: " + ", ".join(details["accepted_permissions"]), file=sys.stderr)
        if details.get("status_code") in {403, 404}:
            print("Compruebe permisos y que el repositorio esté incluido en el alcance del Fine-grained PAT.", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
