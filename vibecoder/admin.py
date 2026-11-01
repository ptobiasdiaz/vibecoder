"""Administración local reutilizable y CLI de M05.2.

Los secretos nunca entran por argv. La CLI es solo una capa sobre
``AdministrationService`` para que una futura interfaz use las mismas reglas.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import shutil
import sqlite3
import stat
import sys
import grp
import pwd
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from .agents import AgentManagementService
from .domain import AccountStatus, AuthType
from .errors import ConflictError, CoreError, NotFoundError, ValidationError
from .service import OrchestratorCore
from .sqlite import SQLiteRepository
from .supervisor import ProjectRuntime, SupervisorConfig, SupervisorStore


EXIT_USAGE, EXIT_AUTH, EXIT_CONFIG, EXIT_OPERATION = 2, 3, 4, 5
_SAFE_ID = __import__("re").compile(r"[a-z][a-z0-9-]{0,62}\Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class AdministrativeAuthorizationError(PermissionError):
    pass


class AtomicJsonFile:
    """JSON validado con bloqueo advisory y reemplazo atómico."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValidationError(f"JSON raíz inválido: {self.path}")
        return value

    def update(self, transform: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                current = self.read() if self.path.exists() else {}
                result = transform(dict(current))
                temp = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
                existing_stat = self.path.stat() if self.path.exists() else None
                mode = stat.S_IMODE(existing_stat.st_mode) if existing_stat else 0o640
                out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
                try:
                    if existing_stat is not None:
                        os.fchown(out, existing_stat.st_uid, existing_stat.st_gid)
                    with os.fdopen(out, "w", encoding="utf-8") as stream:
                        json.dump(result, stream, indent=2, ensure_ascii=False, sort_keys=True)
                        stream.write("\n")
                        stream.flush(); os.fsync(stream.fileno())
                    os.replace(temp, self.path)
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try: os.fsync(directory)
                    finally: os.close(directory)
                finally:
                    if temp.exists(): temp.unlink()
                return result
        except Exception:
            # fd pertenece a fdopen solo después de entrar en el with.
            raise


class FileSecretStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, credential_id: str) -> Path:
        if not _SAFE_ID.fullmatch(credential_id):
            raise ValidationError("identificador de credencial inválido")
        path = self.root / f"{credential_id}.secret"
        if path.parent != self.root:
            raise ValidationError("ruta de credencial fuera del almacén")
        return path

    def write(self, credential_id: str, secret: bytes) -> Path:
        if not secret or len(secret) > 64 * 1024 or b"\0" in secret:
            raise ValidationError("secreto vacío o inválido")
        target = self._path(credential_id)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValidationError("el almacén de secretos no puede ser un enlace")
        os.chmod(self.root, 0o700)
        if target.is_symlink():
            raise ValidationError("el destino de credencial es un enlace")
        temp = self.root / f".{credential_id}.{uuid4().hex}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, secret.rstrip(b"\r\n") + b"\n"); os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target); os.chmod(target, 0o600)
        try:
            user = pwd.getpwnam("vibecoder"); group = grp.getgrnam("vibecoder")
            os.chown(target, user.pw_uid, group.gr_gid)
        except KeyError:
            pass
        directory = os.open(self.root, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return target

    def read_fd(self, fd: int) -> bytes:
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(8192, 65537 - total))
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
            if total > 65536: raise ValidationError("secreto excesivamente grande")
        return b"".join(chunks)

    def revoke(self, credential_id: str) -> None:
        path = self._path(credential_id)
        if path.exists(): path.unlink()


class AdministrationService:
    def __init__(self, config_path: Path, *, secrets_dir: Path | None = None,
                 uid_getter: Callable[[], int] = os.geteuid):
        self.config_path = config_path.resolve()
        self.config_file = AtomicJsonFile(self.config_path)
        self.uid_getter = uid_getter
        self.config = SupervisorConfig.load(self.config_path)
        self.secrets = FileSecretStore(secrets_dir or self.config.secrets_dir or self.config.database.parent / "secrets")

    def require_admin(self) -> None:
        if self.uid_getter() != 0:
            raise AdministrativeAuthorizationError("la operación requiere root")

    def _repo(self) -> SQLiteRepository:
        self.config.database.parent.mkdir(parents=True, exist_ok=True)
        return SQLiteRepository(self.config.database)

    def _accounts_file(self) -> AtomicJsonFile:
        if self.config.accounts is None:
            raise ValidationError("accounts.json no está configurado")
        return AtomicJsonFile(self.config.accounts)

    def _refresh(self) -> None:
        self.config = SupervisorConfig.load(self.config_path)

    def _write_config(self, mutate: Callable[[dict[str, Any]], None]) -> SupervisorConfig:
        self.require_admin()
        def change(raw: dict[str, Any]) -> dict[str, Any]:
            mutate(raw)
            # Un cambio administrativo crítico degrada READY de forma segura.
            if raw.get("operational_state") == "READY": raw["operational_state"] = "CONFIGURED"
            SupervisorConfig.model_validate(raw)
            return raw
        self.config_file.update(change); self._refresh(); return self.config

    def status(self) -> dict[str, Any]:
        with self._repo() as repo:
            jobs = repo.list("supervisor_jobs")
            projects = repo.list("projects")
        return {"operational_state": self.config.operational_state,
                "projects": {"total": len(projects), "configured": len(self.config.projects),
                             "enabled": sum(1 for p in self.config.projects.values() if p.enabled)},
                "jobs": {"total": len(jobs), "blocked": sum(j["state"] == "BLOCKED" for j in jobs),
                         "unknown": sum(j["state"] == "UNKNOWN" for j in jobs)}}

    def list_projects(self) -> list[dict[str, Any]]:
        with self._repo() as repo:
            core = OrchestratorCore(repo)
            return [self._project_dict(p) for p in core.list_projects()]

    def project(self, project_id: str) -> dict[str, Any]:
        with self._repo() as repo:
            return self._project_dict(OrchestratorCore(repo).get_project(project_id))

    def _project_dict(self, project: Any) -> dict[str, Any]:
        value = asdict(project); runtime = self.config.projects.get(project.id)
        value["runtime"] = runtime.model_dump(mode="json") if runtime else None
        return value

    def add_project(self, name: str, account_id: str, repository: str | None = None) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo:
            core = OrchestratorCore(repo)
            existing = next((p for p in core.list_projects() if p.name == name), None)
            project = existing or core.register_project(name, account_id, github_repository=repository)
            if existing and (existing.codex_account_id != account_id or existing.github_repository != repository):
                raise ConflictError("ya existe un proyecto con configuración diferente")
            return self._project_dict(project)

    def configure_project(self, project_id: str, *, repository: str | None,
                          workspace: Path, authorized_users: list[str],
                          inference_enabled: bool = False,
                          publication_enabled: bool = False) -> dict[str, Any]:
        self.require_admin(); workspace = workspace.resolve(strict=True)
        with self._repo() as repo:
            core = OrchestratorCore(repo); project = core.configure_project_repository(project_id, repository)
        def mutate(raw: dict[str, Any]) -> None:
            raw.setdefault("projects", {})[project_id] = {
                "workspace": str(workspace), "allowed_paths": ["src", "tests", "doc", "README.md"],
                "enabled": False, "inference_enabled": inference_enabled,
                "publication_enabled": publication_enabled,
                "authorized_users": authorized_users,
            }
            merged = list(dict.fromkeys([*raw.get("authorized_users", []), *authorized_users]))
            raw["authorized_users"] = merged
        self._write_config(mutate)
        return self._project_dict(project)

    def set_project_enabled(self, project_id: str, enabled: bool) -> dict[str, Any]:
        if project_id not in self.config.projects: raise ConflictError("proyecto sin configuración runtime")
        self._write_config(lambda raw: raw["projects"][project_id].__setitem__("enabled", enabled))
        return self.project(project_id)

    def list_accounts(self) -> list[dict[str, Any]]:
        runtime = self._accounts_file().read().get("accounts", {})
        with self._repo() as repo:
            return [{**asdict(a), "runtime": runtime.get(a.id)} for a in OrchestratorCore(repo).list_accounts()]

    def account(self, account_id: str) -> dict[str, Any]:
        runtime = self._accounts_file().read().get("accounts", {})
        with self._repo() as repo:
            return {**asdict(OrchestratorCore(repo).get_account(account_id)), "runtime": runtime.get(account_id)}

    def add_account(self, name: str, auth_type: str, codex_home: Path,
                    executable: str, cwd: Path, timeout: float = 600) -> dict[str, Any]:
        self.require_admin(); codex_home = codex_home.resolve(); cwd = cwd.resolve()
        codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not cwd.is_dir(): raise ValidationError("cwd no existe")
        current = self._accounts_file().read().get("accounts", {})
        for key, item in current.items():
            if Path(item["codex_home"]).resolve() == codex_home:
                raise ConflictError(f"CODEX_HOME ya pertenece a {key}")
        account_id = str(uuid4())
        with self._repo() as repo:
            core = OrchestratorCore(repo)
            existing = next((a for a in core.list_accounts() if a.name == name), None)
            if existing: raise ConflictError("ya existe una cuenta con ese nombre")
        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            raw.setdefault("accounts", {})[account_id] = {
                "codex_home": str(codex_home), "codex_executable": executable,
                "cwd": str(cwd), "extra_args": [], "timeout_seconds": timeout,
            }; return raw
        self._accounts_file().update(mutate)
        try:
            with self._repo() as repo:
                account = OrchestratorCore(repo).register_account(
                    name, AuthType(auth_type), f"codex-home:{name}", account_id=account_id)
        except Exception:
            self._accounts_file().update(
                lambda raw: {**raw, "accounts": {k: v for k, v in raw.get("accounts", {}).items() if k != account_id}}
            )
            raise
        return self.account(account.id)

    def set_account_disabled(self, account_id: str) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo: OrchestratorCore(repo).set_account_status(account_id, AccountStatus.DISABLED)
        return self.account(account_id)

    def configure_account(self, account_id: str, *, codex_home: Path,
                          executable: str, cwd: Path, timeout: float = 600) -> dict[str, Any]:
        self.require_admin(); self.account(account_id)
        codex_home = codex_home.resolve(); cwd = cwd.resolve()
        if not codex_home.is_dir() or codex_home.is_symlink() or not cwd.is_dir():
            raise ValidationError("CODEX_HOME y cwd deben ser directorios seguros existentes")
        current = self._accounts_file().read().get("accounts", {})
        if any(key != account_id and Path(value["codex_home"]).resolve() == codex_home
               for key, value in current.items()):
            raise ConflictError("CODEX_HOME ya está asignado")
        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            raw.setdefault("accounts", {})[account_id] = {
                "codex_home": str(codex_home), "codex_executable": executable,
                "cwd": str(cwd), "extra_args": [], "timeout_seconds": timeout,
            }; return raw
        self._accounts_file().update(mutate)
        return self.account(account_id)

    def check_account(self, account_id: str) -> dict[str, Any]:
        account = self.account(account_id); runtime = account.get("runtime") or {}; checks = []
        home = Path(runtime.get("codex_home", ""))
        secure_home = home.is_dir() and not home.is_symlink()
        if secure_home: secure_home = not bool(stat.S_IMODE(home.stat().st_mode) & 0o002)
        checks.append({"name": "codex_home", "ok": secure_home})
        executable = runtime.get("codex_executable", "codex")
        found = Path(executable).is_file() if Path(executable).is_absolute() else shutil.which(executable) is not None
        checks.append({"name": "codex_executable", "ok": found})
        cwd = Path(runtime.get("cwd", "")); checks.append({"name": "cwd", "ok": cwd.is_dir()})
        return {"account_id": account_id, "ok": all(c["ok"] for c in checks), "checks": checks}

    def credentials(self) -> list[dict[str, Any]]:
        with self._repo() as repo:
            return [{k: row[k] for k in ("id", "provider", "status", "created_at", "updated_at")}
                    for row in repo.list("admin_credentials")]

    def check_credential(self, credential_id: str) -> dict[str, Any]:
        with self._repo() as repo: row = repo.get("admin_credentials", credential_id)
        path = Path(str(row["secret_path"]))
        valid = (row["status"] == "ACTIVE" and path.parent == self.secrets.root
                 and path.is_file() and not path.is_symlink()
                 and stat.S_IMODE(path.stat().st_mode) == 0o600)
        return {"id": credential_id, "provider": row["provider"],
                "status": row["status"], "ok": valid}

    def add_credential(self, provider: str, secret: bytes, credential_id: str | None = None) -> dict[str, Any]:
        self.require_admin(); provider = provider.lower().strip()
        if provider != "github": raise ValidationError("proveedor no soportado")
        credential_id = credential_id or f"github-{uuid4().hex[:12]}"
        if any(item["id"] == credential_id for item in self.credentials()):
            raise ConflictError("la credencial ya existe; use replace")
        path = self.secrets.write(credential_id, secret); now = _now()
        try:
            with self._repo() as repo:
                repo.insert("admin_credentials", {"id": credential_id, "provider": provider,
                    "secret_path": str(path), "status": "ACTIVE", "created_at": now, "updated_at": now})
        except Exception:
            self.secrets.revoke(credential_id); raise
        return next(c for c in self.credentials() if c["id"] == credential_id)

    def replace_credential(self, credential_id: str, secret: bytes) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo:
            row = repo.get("admin_credentials", credential_id)
            if row["status"] != "ACTIVE": raise ConflictError("credencial revocada")
            self.secrets.write(credential_id, secret)
            repo.update("admin_credentials", credential_id, {"updated_at": _now()})
        return next(c for c in self.credentials() if c["id"] == credential_id)

    def revoke_credential(self, credential_id: str) -> dict[str, Any]:
        self.require_admin(); self.secrets.revoke(credential_id)
        with self._repo() as repo:
            repo.update("admin_credentials", credential_id, {"status": "REVOKED", "updated_at": _now()})
        return next(c for c in self.credentials() if c["id"] == credential_id)

    def config_show(self) -> dict[str, Any]: return self.config.model_dump(mode="json")

    _CONFIG_KEYS: Mapping[str, tuple[str, Callable[[str], Any]]] = {
        "log_level": ("log_level", str), "inference_enabled": ("inference_enabled", lambda x: x.lower() == "true"),
        "publication_enabled": ("publication_enabled", lambda x: x.lower() == "true"),
        "remote_reads_enabled": ("remote_reads_enabled", lambda x: x.lower() == "true"),
        "limits.global_active": ("limits.global_active", int), "limits.github_active": ("limits.github_active", int),
        "intervals.issues": ("intervals.issues", float), "intervals.pull_requests": ("intervals.pull_requests", float),
        "github_credential_id": ("github_credential_id", str),
    }

    def config_set(self, key: str, value: str) -> dict[str, Any]:
        if key not in self._CONFIG_KEYS: raise ValidationError("clave no administrable")
        path, convert = self._CONFIG_KEYS[key]; parsed = convert(value)
        def mutate(raw: dict[str, Any]) -> None:
            target = raw
            bits = path.split(".")
            for bit in bits[:-1]: target = target.setdefault(bit, {})
            target[bits[-1]] = parsed
        return self._write_config(mutate).model_dump(mode="json")

    def setup_configure(self) -> dict[str, Any]:
        self.require_admin(); report = self.doctor()
        if any(item["level"] == "ERROR" for item in report["checks"]):
            raise ConflictError("el diagnóstico contiene errores")
        self.config_file.update(lambda raw: {**raw, "operational_state": "CONFIGURED"}); self._refresh()
        return self.status()

    def setup_activate(self) -> dict[str, Any]:
        self.require_admin()
        if self.config.operational_state not in {"CONFIGURED", "READY"}: raise ConflictError("ejecute setup configure")
        if not self.config.projects or not any(p.enabled for p in self.config.projects.values()):
            raise ConflictError("no existe ningún proyecto habilitado")
        accounts = self.list_accounts()
        if not accounts or not all(self.check_account(a["id"])["ok"] for a in accounts if a["status"] != "DISABLED"):
            raise ConflictError("cuentas Codex incompletas")
        self.config_file.update(lambda raw: {**raw, "operational_state": "READY"}); self._refresh()
        return self.status()

    def setup_deactivate(self) -> dict[str, Any]:
        self.require_admin(); self.config_file.update(lambda raw: {**raw, "operational_state": "CONFIGURED"}); self._refresh()
        return self.status()

    def service_pause(self, kind: str, reason: str) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo:
            store = SupervisorStore(repo); store.set_pause("GLOBAL", "global", actor="admin-cli", reason=reason,
                all_work=kind == "all", inference=kind == "inference", publication=kind == "publication")
        return {"paused": kind, "reason": reason}

    def service_resume(self) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo:
            SupervisorStore(repo).set_pause("GLOBAL", "global", actor="admin-cli", reason="resume",
                all_work=False, inference=False, publication=False)
        return {"paused": False}

    def agents(self, project_id: str) -> list[dict[str, Any]]:
        with self._repo() as repo:
            service = AgentManagementService(OrchestratorCore(repo))
            return [asdict(item) for item in service.list(project_id)]

    def agent(self, agent_id: str) -> dict[str, Any]:
        with self._repo() as repo: return asdict(AgentManagementService(OrchestratorCore(repo)).get(agent_id))

    def disable_agent(self, agent_id: str) -> dict[str, Any]:
        self.require_admin()
        with self._repo() as repo:
            value = AgentManagementService(OrchestratorCore(repo)).suspend(
                agent_id, actor="local-root", authorized=True)
            return asdict(value)

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "level": "OK" if ok else "ERROR", "detail": detail})
        add("configuration", True)
        add("database_parent", self.config.database.parent.is_dir())
        if self.config.database.exists():
            try:
                con = sqlite3.connect(f"file:{self.config.database}?mode=ro", uri=True)
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                foreign = con.execute("PRAGMA foreign_key_check").fetchall()
                version = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
                con.close(); add("sqlite_integrity", integrity == "ok" and not foreign, str(integrity))
                expected = max(int(path.name[:4]) for path in Path(__file__).with_name("migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
                add("schema_version", int(version) == expected, f"{version}/{expected}")
            except sqlite3.Error as exc: add("sqlite_integrity", False, type(exc).__name__)
        else: checks.append({"name": "database", "level": "NOT_CONFIGURED", "detail": "ausente"})
        add("workspace_root", self.config.task_workspace_root.is_dir())
        if self.config.secrets_dir:
            add("secrets_dir", self.config.secrets_dir.is_dir() and not self.config.secrets_dir.is_symlink())
        add("git", shutil.which("git") is not None)
        if self.config.accounts and self.config.database.exists():
            try:
                for account in self.list_accounts():
                    result = self.check_account(account["id"])
                    checks.append({"name": f"account:{account['id']}", "level": "OK" if result["ok"] else "WARNING", "detail": "local"})
            except Exception as exc: checks.append({"name": "accounts", "level": "ERROR", "detail": type(exc).__name__})
        if self.config.database.exists():
            try:
                with self._repo() as repo:
                    unknown = len(repo.list("supervisor_jobs", state="UNKNOWN"))
                    blocked = len(repo.list("supervisor_jobs", state="BLOCKED"))
                    known_projects = {str(row["id"]) for row in repo.list("projects")}
                checks.append({"name": "jobs", "level": "WARNING" if unknown else "OK",
                               "detail": f"unknown={unknown}, blocked={blocked}"})
                missing = sorted(set(self.config.projects) - known_projects)
                checks.append({"name": "project_coherence", "level": "ERROR" if missing else "OK",
                               "detail": ",".join(missing)})
            except Exception as exc:
                checks.append({"name": "runtime_state", "level": "ERROR", "detail": type(exc).__name__})
        return {"operational_state": self.config.operational_state, "checks": checks,
                "summary": {level: sum(c["level"] == level for c in checks) for level in ("OK", "WARNING", "ERROR", "NOT_CONFIGURED")}}


def _secret_from_args(args: argparse.Namespace) -> bytes:
    if args.fd is not None:
        chunks = []
        while True:
            chunk = os.read(args.fd, 8192)
            if not chunk: break
            chunks.append(chunk)
        return b"".join(chunks)
    if not sys.stdin.isatty(): raise ValidationError("use --fd para automatización")
    return getpass.getpass("Token GitHub: ").encode()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vibecoder-admin")
    p.add_argument("--config", type=Path, default=Path("/etc/vibecoder/supervisor.json")); p.add_argument("--secrets-dir", type=Path)
    p.add_argument("--json", action="store_true"); p.add_argument("--yes", action="store_true")
    sub = p.add_subparsers(dest="group", required=True)
    sub.add_parser("status"); sub.add_parser("doctor")
    cfg = sub.add_parser("config").add_subparsers(dest="action", required=True); cfg.add_parser("show"); cfg.add_parser("validate")
    cs = cfg.add_parser("set"); cs.add_argument("key"); cs.add_argument("value")
    cr = cfg.add_parser("reset"); cr.add_argument("key")
    projects = sub.add_parser("projects").add_subparsers(dest="action", required=True)
    projects.add_parser("list"); ps = projects.add_parser("show"); ps.add_argument("id")
    pa = projects.add_parser("add"); pa.add_argument("--name", required=True); pa.add_argument("--account", required=True); pa.add_argument("--repository")
    pc = projects.add_parser("configure"); pc.add_argument("id"); pc.add_argument("--repository"); pc.add_argument("--workspace", required=True, type=Path); pc.add_argument("--authorized-user", action="append", default=[]); pc.add_argument("--enable-inference", action="store_true"); pc.add_argument("--enable-publication", action="store_true")
    for action in ("enable", "disable", "status"):
        q = projects.add_parser(action); q.add_argument("id")
    accounts = sub.add_parser("accounts").add_subparsers(dest="action", required=True); accounts.add_parser("list")
    for action in ("show", "check", "disable"):
        q = accounts.add_parser(action); q.add_argument("id")
    aa = accounts.add_parser("add"); aa.add_argument("--name", required=True); aa.add_argument("--auth-type", choices=[x.value for x in AuthType], required=True); aa.add_argument("--codex-home", type=Path, required=True); aa.add_argument("--executable", default="codex"); aa.add_argument("--cwd", type=Path, required=True)
    ac = accounts.add_parser("configure"); ac.add_argument("id"); ac.add_argument("--codex-home", type=Path, required=True); ac.add_argument("--executable", default="codex"); ac.add_argument("--cwd", type=Path, required=True)
    credentials = sub.add_parser("credentials").add_subparsers(dest="action", required=True); credentials.add_parser("list")
    ca = credentials.add_parser("add"); ca.add_argument("provider", choices=["github"]); ca.add_argument("--id"); ca.add_argument("--fd", type=int)
    for action in ("replace", "revoke", "check"):
        q = credentials.add_parser(action); q.add_argument("id");
        if action == "replace": q.add_argument("--fd", type=int)
    setup = sub.add_parser("setup").add_subparsers(dest="action", required=True)
    for action in ("status", "validate", "configure", "activate", "deactivate"): setup.add_parser(action)
    service = sub.add_parser("service").add_subparsers(dest="action", required=True); service.add_parser("status"); service.add_parser("readiness"); service.add_parser("resume")
    sp = service.add_parser("pause"); sp.add_argument("--kind", choices=["all", "inference", "publication"], default="all"); sp.add_argument("--reason", default="administrative pause")
    agents = sub.add_parser("agents").add_subparsers(dest="action", required=True); al = agents.add_parser("list"); al.add_argument("--project", required=True)
    for action in ("show", "status", "disable"):
        q = agents.add_parser(action); q.add_argument("id")
    return p


def _dispatch(service: AdministrationService, args: argparse.Namespace) -> Any:
    def confirm(message: str) -> None:
        if args.yes: return
        if not sys.stdin.isatty(): raise ValidationError("la operación requiere confirmación o --yes")
        if input(f"{message} [y/N] ").strip().lower() not in {"y", "yes", "s", "si", "sí"}:
            raise ConflictError("operación cancelada")
    if args.group == "status": return service.status()
    if args.group == "doctor": return service.doctor()
    if args.group == "config":
        if args.action in {"show", "validate"}: return service.config_show()
        if args.action == "reset":
            defaults = SupervisorConfig.model_fields[args.key].default if args.key in SupervisorConfig.model_fields else None
            if args.key not in {"github_credential_id"}: raise ValidationError("clave no reiniciable")
            return service._write_config(lambda raw: raw.__setitem__(args.key, defaults)).model_dump(mode="json")
        return service.config_set(args.key, args.value)
    if args.group == "projects":
        if args.action == "list": return service.list_projects()
        if args.action in {"show", "status"}: return service.project(args.id)
        if args.action == "add": return service.add_project(args.name, args.account, args.repository)
        if args.action == "configure": return service.configure_project(args.id, repository=args.repository, workspace=args.workspace, authorized_users=args.authorized_user, inference_enabled=args.enable_inference, publication_enabled=args.enable_publication)
        if args.action == "disable": confirm("¿Deshabilitar el proyecto?")
        return service.set_project_enabled(args.id, args.action == "enable")
    if args.group == "accounts":
        if args.action == "list": return service.list_accounts()
        if args.action == "show": return service.account(args.id)
        if args.action == "check": return service.check_account(args.id)
        if args.action == "disable":
            confirm("¿Deshabilitar la cuenta Codex?"); return service.set_account_disabled(args.id)
        if args.action == "configure": return service.configure_account(args.id, codex_home=args.codex_home, executable=args.executable, cwd=args.cwd)
        return service.add_account(args.name, args.auth_type, args.codex_home, args.executable, args.cwd)
    if args.group == "credentials":
        if args.action == "list": return service.credentials()
        if args.action == "add": return service.add_credential(args.provider, _secret_from_args(args), args.id)
        if args.action == "replace": return service.replace_credential(args.id, _secret_from_args(args))
        if args.action == "revoke":
            confirm("¿Revocar localmente la credencial?"); return service.revoke_credential(args.id)
        return service.check_credential(args.id)
    if args.group == "setup":
        if args.action == "status": return service.status()
        if args.action == "validate": return service.doctor()
        if args.action == "configure": return service.setup_configure()
        if args.action == "activate": return service.setup_activate()
        confirm("¿Desactivar la operación normal?"); return service.setup_deactivate()
    if args.group == "service":
        if args.action in {"status", "readiness"}: return service.status()
        if args.action == "pause": return service.service_pause(args.kind, args.reason)
        return service.service_resume()
    if args.action == "list": return service.agents(args.project)
    if args.action in {"show", "status"}: return service.agent(args.id)
    confirm("¿Suspender el agente?"); return service.disable_agent(args.id)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv); service = AdministrationService(args.config, secrets_dir=args.secrets_dir)
        result = _dispatch(service, args)
        print(json.dumps(result, indent=None if args.json else 2, ensure_ascii=False, default=str))
        return 0
    except AdministrativeAuthorizationError as exc:
        print(f"autorización: {exc}", file=sys.stderr); return EXIT_AUTH
    except (PydanticValidationError, ValidationError, json.JSONDecodeError) as exc:
        print(f"configuración: {exc}", file=sys.stderr); return EXIT_CONFIG
    except (CoreError, OSError, sqlite3.Error) as exc:
        print(f"operación: {exc}", file=sys.stderr); return EXIT_OPERATION


if __name__ == "__main__": raise SystemExit(main())
