"""Neutral plugin workers, sessions and conservative provider executions."""
from __future__ import annotations

import json
import os
import pwd
import re
import stat
import uuid
import concurrent.futures
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from .domain import (Agent, AgentStatus, ExecutionOutcome, ExecutionState,
                     PluginCapability, ProviderExecution, ProviderSession,
                     ProviderSessionStatus, Worker, WorkerStatus)
from .errors import ConflictError, InvalidTransition, NotFoundError, OperationError
from .github_identity import utc_now
from .plugin_registry import PluginRegistry, RegisteredPlugin
from .resolution import ResourceResolver
from .selection import WorkerSelection, select_worker
from .sqlite import SQLiteRepository
from .worker_runtime import (DirectProcessLauncher, RuntimeLaunchSpec,
                             WorkerProcessLauncher, runtime_request)

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
_TRANSITIONS = {
    WorkerStatus.INSTALLING: {WorkerStatus.AUTHENTICATION_REQUIRED, WorkerStatus.VERIFYING, WorkerStatus.ERROR},
    WorkerStatus.AUTHENTICATION_REQUIRED: {WorkerStatus.VERIFYING, WorkerStatus.DISABLED, WorkerStatus.ERROR},
    WorkerStatus.VERIFYING: {WorkerStatus.READY, WorkerStatus.UNAVAILABLE, WorkerStatus.AUTHENTICATION_REQUIRED, WorkerStatus.ERROR},
    WorkerStatus.READY: {WorkerStatus.UNAVAILABLE, WorkerStatus.DISABLED, WorkerStatus.ERROR, WorkerStatus.VERIFYING},
    WorkerStatus.UNAVAILABLE: {WorkerStatus.VERIFYING, WorkerStatus.DISABLED, WorkerStatus.ERROR},
    WorkerStatus.DISABLED: {WorkerStatus.VERIFYING},
    WorkerStatus.ERROR: {WorkerStatus.VERIFYING, WorkerStatus.DISABLED},
}


def _safe_storage_root(info: os.stat_result, worker_uid: int, daemon_gid: int) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    return (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and
            info.st_uid == worker_uid and
            (mode == 0o700 or (mode == 0o710 and info.st_gid == daemon_gid)))


@dataclass(frozen=True)
class ProvisionedIdentity:
    unix_user: str
    storage_path: Path
    isolation_kind: str
    security_boundary: bool


class WorkerIdentityProvisioner(Protocol):
    def prepare(self, worker_id: str, storage_root: Path, admin_gid: int | None) -> ProvisionedIdentity: ...


class PluginExecutableManager(Protocol):
    def detect(self, explicit: Path | None = None): ...
    def install(self): ...


class PreprovisionedUnixIdentity:
    """Validate a package-provisioned per-worker Unix account and private path."""
    def prepare(self, worker_id: str, storage_root: Path, admin_gid: int | None) -> ProvisionedIdentity:
        user = f"vibecoder-w-{worker_id.replace('-', '')[:12]}"
        path = storage_root / worker_id
        try: account = pwd.getpwnam(user)
        except KeyError as exc:
            raise OperationError(f"identidad Unix {user} no preparada; M06.9 instalará el aprovisionador",
                details={"category": "IDENTITY_NOT_PROVISIONED", "unix_user": user,
                         "storage_path": str(path), "worker_id": worker_id}) from exc
        if account.pw_uid in {0, os.geteuid()} or (admin_gid is not None and account.pw_gid == admin_gid):
            raise OperationError("identidad de worker no aislada del daemon o administración")
        try: info = path.lstat()
        except FileNotFoundError as exc: raise OperationError("storage privado del worker no preparado") from exc
        if not _safe_storage_root(info, account.pw_uid, os.getegid()):
            raise OperationError("propietario o permisos inseguros en storage del worker")
        temporary = path / "tmp"
        try: temp_info = temporary.lstat()
        except FileNotFoundError as exc: raise OperationError("directorio temporal privado del worker no preparado") from exc
        if stat.S_ISLNK(temp_info.st_mode) or not stat.S_ISDIR(temp_info.st_mode) or temp_info.st_uid != account.pw_uid or stat.S_IMODE(temp_info.st_mode) & 0o077:
            raise OperationError("directorio temporal del worker inseguro")
        return ProvisionedIdentity(user, path, "UNIX_USER_SYSTEMD", True)

class PrivilegedUnixIdentityProvisioner:
    """Ask the fixed-policy root launcher to create exactly one worker UID."""
    def __init__(self, client: Any): self.client=client
    def prepare(self, worker_id: str, storage_root: Path, admin_gid: int | None) -> ProvisionedIdentity:
        value=self.client.provision(worker_id);path=Path(str(value["storage_path"]));expected=storage_root/worker_id
        if path!=expected:raise OperationError("launcher devolvió storage inesperado")
        return ProvisionedIdentity(str(value["unix_user"]),path,"UNIX_USER_SYSTEMD",True)


class WorkerService:
    def __init__(self, repository: SQLiteRepository, plugins: PluginRegistry,
                 storage_root: Path, launcher: WorkerProcessLauncher,
                 provisioner: WorkerIdentityProvisioner | None = None,
                 admin_gid: int | None = None,
                 executable_managers: Mapping[str, PluginExecutableManager] | None = None):
        self.repository, self.plugins = repository, plugins
        self.storage_root, self.launcher = storage_root.absolute(), launcher
        self.provisioner = provisioner or PreprovisionedUnixIdentity()
        self.admin_gid = admin_gid
        self.executable_managers = dict(executable_managers or {})
        self._authentication_jobs: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="worker-auth")
        self._recover_interrupted()

    def close(self) -> None:
        """Stop accepting authentication jobs; systemd owns cleanup of active units."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _recover_interrupted(self) -> None:
        now = utc_now()
        for row in self.repository.query_all("SELECT id,worker_id FROM worker_registration_operations WHERE status='RUNNING'"):
            self.repository.execute("UPDATE worker_registration_operations SET status='FAILED',error_category='DAEMON_RESTART',updated_at=? WHERE id=?", (now, row["id"]))
            if row["worker_id"]:
                self.repository.execute("UPDATE workers SET status='ERROR',last_error='alta interrumpida; requiere enable',updated_at=? WHERE id=? AND status IN ('INSTALLING','VERIFYING')", (now, row["worker_id"]))
        for row in self.repository.query_all("SELECT id FROM provider_executions WHERE state IN ('PENDING','RUNNING')"):
            self.repository.execute("UPDATE provider_executions SET state='FINISHED',outcome='UNKNOWN',error_category='DAEMON_RESTART',updated_at=? WHERE id=?", (now, row["id"]))
        self.repository.execute("UPDATE provider_sessions SET status='UNKNOWN',updated_at=? WHERE status='CREATING'", (now,))
        self.repository.execute("UPDATE workers SET active_executions=(SELECT count(*) FROM provider_executions e WHERE e.worker_id=workers.id AND e.state IN ('PENDING','RUNNING')),updated_at=?", (now,))
        self.repository.execute("UPDATE worker_authentication_operations SET status='INTERRUPTED',error_category='DAEMON_RESTART',updated_at=? WHERE status='RUNNING'", (now,))
        for row in self.repository.query_all("SELECT id,public_json FROM worker_authentication_operations"):
            try: public = json.loads(row["public_json"])
            except (TypeError, ValueError): public = {}
            sanitized = {k: v for k, v in public.items() if k in {"outcome", "error_category"}}
            if sanitized != public:
                self.repository.execute("UPDATE worker_authentication_operations SET public_json=?,updated_at=? WHERE id=?",
                    (json.dumps(sanitized), now, row["id"]))

    def add(self, plugin_query: str, name: str | None, actor: str,
            *, configuration: Mapping[str, Any] | None = None) -> dict[str, Any]:
        plugin = self.plugins.resolve(plugin_query)
        public = plugin.public()
        if not public["compatible"]: raise OperationError("plugin incompatible con esta plataforma")
        if not plugin.available or not plugin.runtime:
            raise OperationError(plugin.diagnostic or "plugin no disponible")
        if not self.launcher.security_boundary and not plugin.test_only:
            raise OperationError("un plugin de producción requiere launcher con frontera de seguridad")
        worker_name = name or self._automatic_name(plugin.descriptor.plugin_id)
        if not _NAME.fullmatch(worker_name): raise ValueError("nombre de worker no válido")
        existing = self.repository.query_one("SELECT id FROM workers WHERE name=? COLLATE NOCASE", (worker_name,))
        if existing: raise ConflictError(f"worker ya existe: {worker_name}")
        worker_id, op_id, now = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"vibecoder-worker:{worker_name.casefold()}")), str(uuid.uuid4()), utc_now()
        op_key = f"worker-add:{worker_name.casefold()}"
        previous = self.repository.query_one("SELECT id,status FROM worker_registration_operations WHERE operation_key=?", (op_key,))
        if previous:
            if previous["status"] != "FAILED": raise ConflictError(f"alta de worker ya registrada: {worker_name}")
            op_id = str(previous["id"])
            self.repository.execute("UPDATE worker_registration_operations SET phase='RESERVED',status='RUNNING',error_category=NULL,updated_at=? WHERE id=?", (now, op_id))
        else:
            self.repository.execute("INSERT INTO worker_registration_operations VALUES(?,NULL,?,'RESERVED','RUNNING',NULL,?,?)", (op_id, op_key, now, now))
        try:
            config = dict(configuration or {})
            manager = self.executable_managers.get(plugin.descriptor.plugin_id)
            if manager is not None:
                try: executable = manager.detect()
                except OperationError as detected:
                    if detected.details.get("category") != "EXECUTABLE_MISSING": raise
                    executable = manager.install()
                config["executable"] = str(executable.path)
                config["executable_version"] = executable.version
                config["executable_sha256"] = executable.sha256
            elif plugin.requirements.get("managed_manifest_required"):
                raise OperationError("gestor de instalación del plugin no configurado en el daemon",
                                     details={"category": "INSTALLATION_MANAGER_UNAVAILABLE"})
            identity = self.provisioner.prepare(worker_id, self.storage_root, self.admin_gid)
            if self.launcher.security_boundary and not identity.security_boundary:
                raise OperationError("el aprovisionador no garantiza una identidad Unix aislada")
            config.update({"runtime_module": plugin.runtime["module"], "test_only": plugin.test_only})
            with self.repository.transaction():
                self.repository.execute("INSERT OR IGNORE INTO plugin_installations VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid5(uuid.NAMESPACE_URL, f"plugin:{plugin.descriptor.plugin_id}:{plugin.descriptor.version}")),
                     plugin.descriptor.plugin_id, plugin.descriptor.version, "INSTALLED", None, None, now, now))
                self.repository.execute("INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (worker_id, worker_name, plugin.descriptor.plugin_id, plugin.descriptor.version,
                     "INSTALLING", json.dumps(config), str(identity.storage_path), 1, 0, None, None, now, now))
                self.repository.execute("INSERT INTO worker_runtime_identities VALUES(?,?,?,?)",
                    (worker_id, identity.unix_user, identity.isolation_kind, now))
                self.repository.execute("UPDATE worker_registration_operations SET worker_id=?,phase='PROBING',updated_at=? WHERE id=?", (worker_id, now, op_id))
            self._transition(worker_id, WorkerStatus.VERIFYING)
            result = self.probe(worker_id, actor)
            self.repository.execute("UPDATE worker_registration_operations SET phase='COMPLETED',status='SUCCEEDED',updated_at=? WHERE id=?", (utc_now(), op_id))
            self._audit(actor, "worker.add", worker_id, "SUCCEEDED", {"plugin": plugin.descriptor.plugin_id, "name": worker_name})
            return {**result, "changed": True}
        except Exception as exc:
            self.repository.execute("UPDATE worker_registration_operations SET status='FAILED',error_category=?,updated_at=? WHERE id=?", (type(exc).__name__, utc_now(), op_id))
            self._audit(actor, "worker.add", worker_id, "FAILED",
                        {"plugin": plugin.descriptor.plugin_id, "category": type(exc).__name__})
            raise

    def list(self) -> list[dict[str, Any]]:
        rows = self.repository.query_all("SELECT w.*,i.unix_user,i.isolation_kind FROM workers w JOIN worker_runtime_identities i ON i.worker_id=w.id ORDER BY w.name COLLATE NOCASE")
        return [self._public(x) for x in rows]

    def get(self, query: str) -> dict[str, Any]:
        return ResourceResolver(id_of=lambda x: x["id"], name_of=lambda x: x["name"]).resolve(query, self.list())

    def disable(self, query: str, actor: str) -> dict[str, Any]:
        item = self.get(query)
        if item["status"] == WorkerStatus.DISABLED.value: return {**item, "changed": False}
        self._transition(item["id"], WorkerStatus.DISABLED)
        self._audit(actor, "worker.disable", item["id"], "SUCCEEDED", {})
        return {**self.get(item["id"]), "changed": True}

    def enable(self, query: str, actor: str) -> dict[str, Any]:
        item = self.get(query)
        if item["status"] == WorkerStatus.READY.value: return {**item, "changed": False}
        self._transition(item["id"], WorkerStatus.VERIFYING)
        result = self.probe(item["id"], actor)
        self._audit(actor, "worker.enable", item["id"], "SUCCEEDED", {"status": result["status"]})
        return {**result, "changed": True}

    def probe(self, query: str, actor: str = "daemon") -> dict[str, Any]:
        item = self.get(query); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        spec = self._spec(item, plugin)
        try:
            result = self.launcher.invoke(spec, runtime_request(spec, "probe",
                {**item["configuration"], "runtime_config": item["configuration"]}))
        except OperationError as exc:
            self._set_status(item["id"], WorkerStatus.UNAVAILABLE, str(exc), frozenset())
            self._probe_record(item["id"], "UNAVAILABLE", (), exc.details.get("category"))
            return self.get(item["id"])
        try:
            capabilities = frozenset(PluginCapability(x) for x in result.get("capabilities", []))
        except (ValueError, TypeError):
            self._set_status(item["id"], WorkerStatus.ERROR,
                             "runtime declaró una capacidad desconocida", frozenset())
            self._probe_record(item["id"], "ERROR", (), "INVALID_CAPABILITY")
            return self.get(item["id"])
        allowed = plugin.descriptor.capabilities
        if not capabilities <= allowed:
            self._set_status(item["id"], WorkerStatus.ERROR, "runtime declaró capacidades no autorizadas", frozenset())
        elif not result.get("available"):
            self._set_status(item["id"], WorkerStatus.UNAVAILABLE, str(result.get("diagnostic", "no disponible")), capabilities)
        elif not result.get("authenticated", True):
            self._set_status(item["id"], WorkerStatus.AUTHENTICATION_REQUIRED, "autenticación requerida", capabilities)
        else:
            self._set_status(item["id"], WorkerStatus.READY, None, capabilities)
        self._probe_record(item["id"], self.get(item["id"])["status"], capabilities, None)
        return self.get(item["id"])

    def select(self, agent: Agent, sessions: tuple[ProviderSession, ...] = (),
               previous_execution: ProviderExecution | None = None) -> WorkerSelection:
        return select_worker(agent, [self._domain(x) for x in self.list()], sessions=sessions,
                             previous_execution=previous_execution)

    def create_session(self, project_id: str, agent_id: str, purpose: str, context_hash: str,
                       worker_query: str, workspace_id: str | None = None) -> dict[str, Any]:
        item = self.get(worker_query)
        if item["status"] != "READY": raise ConflictError("worker no utilizable")
        plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        spec = self._spec(item, plugin, workspace_id, project_id)
        session_id, now = str(uuid.uuid4()), utc_now()
        self.repository.execute("INSERT INTO provider_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, project_id, agent_id, item["id"], workspace_id, purpose, context_hash,
             None, "CREATING", "{}", now, now))
        try:
            result = self.launcher.invoke(spec, runtime_request(spec, "create_session", {"session_id": session_id, "context_hash": context_hash,
                "runtime_config": item["configuration"]}))
            self.repository.execute("UPDATE provider_sessions SET provider_reference=?,status='ACTIVE',continuity_json=?,updated_at=? WHERE id=?",
                (result.get("provider_reference"), json.dumps(result.get("continuity", {})), utc_now(), session_id))
        except OperationError:
            self.repository.execute("UPDATE provider_sessions SET status='UNKNOWN',updated_at=? WHERE id=?", (utc_now(), session_id)); raise
        return self.repository.query_one("SELECT * FROM provider_sessions WHERE id=?", (session_id,)) or {}

    def continue_session(self, session_id: str) -> dict[str, Any]:
        session = self.repository.query_one("SELECT * FROM provider_sessions WHERE id=?", (session_id,))
        if not session or session["status"] != "ACTIVE": raise ConflictError("sesión no continuable")
        item = self.get(str(session["worker_id"])); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        if PluginCapability.SESSION_CONTINUATION.value not in item["capabilities"]:
            raise ConflictError("continuación de sesión no soportada")
        spec = replace(self._spec(item, plugin, session.get("workspace_id"), str(session["project_id"])), timeout=900)
        result = self.launcher.invoke(spec, runtime_request(spec, "continue_session",
            {"provider_reference": session["provider_reference"], "runtime_config": item["configuration"]}))
        self.repository.execute("UPDATE provider_sessions SET continuity_json=?,updated_at=? WHERE id=?",
            (json.dumps({"continued": bool(result.get("continued"))}), utc_now(), session_id))
        return self.repository.query_one("SELECT * FROM provider_sessions WHERE id=?", (session_id,)) or {}

    def execute(self, session_id: str, idempotency_key: str, instructions: str,
                *, mode: str = "success", approval_timeout: int = 300) -> dict[str, Any]:
        existing = self.repository.query_one("SELECT * FROM provider_executions WHERE idempotency_key=?", (idempotency_key,))
        if existing: return existing
        session = self.repository.query_one("SELECT * FROM provider_sessions WHERE id=?", (session_id,))
        if not session or session["status"] != "ACTIVE": raise ConflictError("sesión no utilizable")
        item = self.get(str(session["worker_id"])); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        if not 1 <= approval_timeout <= 840: raise ValueError("timeout de aprobación no válido")
        spec = replace(self._spec(item, plugin, session.get("workspace_id"), str(session["project_id"])), timeout=approval_timeout + 60)
        execution_id, now = str(uuid.uuid4()), utc_now()
        with self.repository.transaction():
            self.repository.execute("INSERT INTO provider_executions VALUES(?,?,?,?,'RUNNING',NULL,NULL,NULL,'{}','{}',?,?)",
                (execution_id, session_id, item["id"], idempotency_key, now, now))
            self.repository.execute("UPDATE workers SET active_executions=active_executions+1,updated_at=? WHERE id=?", (now, item["id"]))
        try:
            result = self.launcher.invoke(spec, runtime_request(spec, "execute", {"execution_id": execution_id,
                "session_id": session_id, "session_reference": session["provider_reference"],
                "instructions": instructions, "mode": mode, "approval_timeout": approval_timeout,
                "runtime_config": item["configuration"]}))
            outcome = ExecutionOutcome(str(result["outcome"]))
            category = result.get("error_category")
        except Exception as exc:
            result, outcome, category = {}, ExecutionOutcome.UNKNOWN, type(exc).__name__
        with self.repository.transaction():
            current = self.repository.query_one("SELECT state FROM provider_executions WHERE id=?", (execution_id,))
            if not current or current["state"] != "RUNNING":
                return self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,)) or {}
            if result.get("provider_session_reference"):
                self.repository.execute("UPDATE provider_sessions SET provider_reference=?,updated_at=? WHERE id=?",
                    (result["provider_session_reference"], utc_now(), session_id))
            self.repository.execute("UPDATE provider_executions SET state='FINISHED',outcome=?,provider_reference=?,error_category=?,result_json=?,updated_at=? WHERE id=?",
                (outcome.value, result.get("provider_execution_reference"), category,
                 json.dumps(result.get("structured_result", {})), utc_now(), execution_id))
            self.repository.execute("UPDATE workers SET active_executions=(SELECT count(*) FROM provider_executions e "
                "WHERE e.worker_id=workers.id AND e.state IN ('PENDING','RUNNING')),updated_at=? WHERE id=?",
                (utc_now(), item["id"]))
        return self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,)) or {}

    def authentication_methods(self, query: str) -> dict[str, Any]:
        item = self.get(query); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        spec = self._spec(item, plugin)
        return self.launcher.invoke(spec, runtime_request(spec, "authentication_methods",
            {"runtime_config": item["configuration"]}))

    def start_login(self, query: str, actor: str, *, timeout: float = 600.0) -> dict[str, Any]:
        item = self.get(query); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        spec = replace(self._spec(item, plugin), timeout=timeout + 15)
        running = self.repository.query_one("SELECT id FROM worker_authentication_operations WHERE worker_id=? AND status='RUNNING'", (item["id"],))
        if running: return {"operation_id": running["id"], "status": "RUNNING", "changed": False}
        operation_id, now = str(uuid.uuid4()), utc_now()
        self.repository.execute("INSERT INTO worker_authentication_operations VALUES(?,?,'RUNNING','{}',NULL,?,?)",
            (operation_id, item["id"], now, now))
        request = runtime_request(spec, "login", {"runtime_config": item["configuration"], "timeout": timeout})
        self._authentication_jobs[operation_id] = self._executor.submit(self.launcher.invoke, spec, request)
        self._audit(actor, "worker.login.start", item["id"], "SUCCEEDED", {"operation_id": operation_id})
        return {"operation_id": operation_id, "status": "RUNNING", "changed": True}

    def login_status(self, query: str, operation_id: str, actor: str) -> dict[str, Any]:
        item = self.get(query)
        row = self.repository.query_one("SELECT * FROM worker_authentication_operations WHERE id=? AND worker_id=?", (operation_id, item["id"]))
        if not row: raise NotFoundError("operación de autenticación no encontrada")
        future = self._authentication_jobs.get(operation_id)
        if row["status"] != "RUNNING" or future is None or not future.done():
            public = json.loads(row["public_json"])
            if row["status"] == "RUNNING" and future is not None:
                plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}"); spec = self._spec(item, plugin)
                try:
                    progress = self.launcher.invoke(spec, runtime_request(spec, "authentication_progress",
                        {"runtime_config": item["configuration"]}))
                    public = {k: v for k, v in progress.items() if k in {"display"}}
                except OperationError: pass
            return {"operation_id": operation_id, "status": row["status"], **public}
        try:
            result = future.result(); succeeded = result.get("outcome") == "SUCCEEDED"
            status = "SUCCEEDED" if succeeded else "FAILED"
            public = {k: v for k, v in result.items() if k in {"outcome", "display", "error_category"}}
            category = result.get("error_category")
        except Exception as exc:
            status, public, category = "FAILED", {"outcome": "FAILED"}, type(exc).__name__
        persisted = {k: v for k, v in public.items() if k in {"outcome", "error_category"}}
        self.repository.execute("UPDATE worker_authentication_operations SET status=?,public_json=?,error_category=?,updated_at=? WHERE id=?",
            (status, json.dumps(persisted), category, utc_now(), operation_id))
        self._authentication_jobs.pop(operation_id, None)
        if status == "SUCCEEDED":
            self._transition(item["id"], WorkerStatus.VERIFYING); worker = self.probe(item["id"], actor)
        else: worker = self.get(item["id"])
        self._audit(actor, "worker.login.finish", item["id"], status, {"category": category})
        return {"operation_id": operation_id, "status": status, **public, "worker": worker}

    def cancel_login(self, query: str, operation_id: str, actor: str) -> dict[str, Any]:
        item = self.get(query)
        row = self.repository.query_one("SELECT status FROM worker_authentication_operations WHERE id=? AND worker_id=?", (operation_id, item["id"]))
        if not row: raise NotFoundError("operación de autenticación no encontrada")
        if row["status"] != "RUNNING": return {"operation_id": operation_id, "status": row["status"], "changed": False}
        plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}"); spec = self._spec(item, plugin)
        result = self.launcher.invoke(spec, runtime_request(spec, "authentication_cancel", {"runtime_config": item["configuration"]}))
        if result.get("cancelled"):
            self.repository.execute("UPDATE worker_authentication_operations SET status='CANCELLED',updated_at=? WHERE id=?", (utc_now(), operation_id))
        self._audit(actor, "worker.login.cancel", item["id"], "SUCCEEDED", {"operation_id": operation_id})
        return {"operation_id": operation_id, "status": "CANCELLED" if result.get("cancelled") else "RUNNING", "changed": bool(result.get("cancelled"))}

    def logout(self, query: str, actor: str) -> dict[str, Any]:
        item = self.get(query); plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}")
        spec = self._spec(item, plugin)
        result = self.launcher.invoke(spec, runtime_request(spec, "logout", {"runtime_config": item["configuration"]}))
        if result.get("outcome") == "SUCCEEDED":
            self._set_status(item["id"], WorkerStatus.AUTHENTICATION_REQUIRED, "autenticación requerida", frozenset())
        self._audit(actor, "worker.logout", item["id"], str(result.get("outcome", "FAILED")), {})
        return {**result, "worker": self.get(item["id"])}

    def reconcile(self, execution_id: str, *, observed_outcome: str = "SUCCEEDED") -> dict[str, Any]:
        execution = self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,))
        if not execution: raise NotFoundError("ejecución no encontrada")
        if execution["outcome"] != "UNKNOWN": return execution
        item = self.get(str(execution["worker_id"])); capabilities = set(item["capabilities"])
        if PluginCapability.RECONCILIATION.value not in capabilities or not execution["provider_reference"]:
            raise ConflictError("el resultado UNKNOWN no puede reconciliarse automáticamente")
        plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}"); spec = self._spec(item, plugin)
        result = self.launcher.invoke(spec, runtime_request(spec, "observe", {"provider_reference": execution["provider_reference"], "observed_outcome": observed_outcome,
            "runtime_config": item["configuration"]}))
        outcome = ExecutionOutcome(str(result["outcome"]))
        self.repository.execute("UPDATE provider_executions SET outcome=?,result_json=?,reconciliation_json=?,updated_at=? WHERE id=?",
            (outcome.value, json.dumps(result.get("structured_result", {})), json.dumps({"observed": True}), utc_now(), execution_id))
        return self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,)) or {}

    def cancel(self, execution_id: str, *, mode: str = "success") -> dict[str, Any]:
        execution = self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,))
        if not execution: raise NotFoundError("ejecución no encontrada")
        item = self.get(str(execution["worker_id"])); capabilities = set(item["capabilities"])
        if PluginCapability.CANCELLATION.value not in capabilities: raise ConflictError("cancelación no soportada")
        if not execution["provider_reference"]: raise ConflictError("ejecución sin referencia cancelable")
        plugin = self.plugins.resolve(f"{item['plugin_id']}@{item['plugin_version']}"); spec = self._spec(item, plugin)
        result = self.launcher.invoke(spec, runtime_request(spec, "cancel", {"provider_reference": execution["provider_reference"], "mode": mode,
            "runtime_config": item["configuration"]}))
        self.repository.execute("UPDATE provider_executions SET outcome=?,updated_at=? WHERE id=?", (result["outcome"], utc_now(), execution_id))
        return self.repository.query_one("SELECT * FROM provider_executions WHERE id=?", (execution_id,)) or {}

    def _spec(self, item: Mapping[str, Any], plugin: RegisteredPlugin,
              workspace_id: str | None = None, project_id: str | None = None) -> RuntimeLaunchSpec:
        workspace = None
        if workspace_id:
            row = self.repository.query_one("SELECT path,project_id FROM workspaces WHERE id=?", (workspace_id,))
            if not row: raise NotFoundError("workspace no encontrado")
            if project_id is not None and row["project_id"] != project_id:
                raise ConflictError("workspace no pertenece al proyecto de la sesión")
            workspace = Path(str(row["path"])).absolute()
            if workspace.is_symlink() or not workspace.is_dir():
                raise OperationError("workspace asignado inseguro o no disponible")
        assert plugin.runtime
        return RuntimeLaunchSpec(str(item["id"]), str(item["unix_user"]), str(plugin.runtime["module"]),
                                 Path(str(item["storage_path"])).absolute(), workspace)

    def _set_status(self, worker_id: str, status: WorkerStatus, error: str | None, capabilities: frozenset[PluginCapability]) -> None:
        now = utc_now()
        with self.repository.transaction():
            self.repository.execute("UPDATE workers SET status=?,last_checked_at=?,last_error=?,updated_at=? WHERE id=?", (status.value, now, error, now, worker_id))
            self.repository.execute("DELETE FROM worker_capabilities WHERE worker_id=?", (worker_id,))
            for capability in capabilities:
                self.repository.execute("INSERT INTO worker_capabilities VALUES(?,?,?)", (worker_id, capability.value, now))

    def _transition(self, worker_id: str, requested: WorkerStatus) -> None:
        row = self.repository.query_one("SELECT status FROM workers WHERE id=?", (worker_id,))
        if not row: raise NotFoundError("worker no encontrado")
        current = WorkerStatus(str(row["status"]))
        if current == requested: return
        if requested not in _TRANSITIONS[current]: raise InvalidTransition("worker", current.value, requested.value)
        self.repository.execute("UPDATE workers SET status=?,updated_at=? WHERE id=?", (requested.value, utc_now(), worker_id))

    def _probe_record(self, worker_id: str, status: str, capabilities: Any, category: Any) -> None:
        self.repository.execute("INSERT INTO worker_probes VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), worker_id, status, json.dumps(sorted(x.value if isinstance(x, PluginCapability) else str(x) for x in capabilities)), category, utc_now()))

    def _automatic_name(self, plugin_id: str) -> str:
        existing = {str(x["name"]).casefold() for x in self.repository.query_all("SELECT name FROM workers")}
        index = 1
        while f"{plugin_id}-{index}".casefold() in existing: index += 1
        return f"{plugin_id}-{index}"

    def _public(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row); result["configuration"] = json.loads(result.pop("config_json"))
        result["capabilities"] = [x["capability"] for x in self.repository.query_all("SELECT capability FROM worker_capabilities WHERE worker_id=? ORDER BY capability", (row["id"],))]
        return result

    def _domain(self, item: Mapping[str, Any]) -> Worker:
        return Worker(str(item["id"]), str(item["name"]), str(item["plugin_id"]), str(item["plugin_version"]),
            WorkerStatus(str(item["status"])), item["configuration"], (), str(item["storage_path"]),
            frozenset(PluginCapability(x) for x in item["capabilities"]), int(item["concurrency_limit"]),
            int(item["active_executions"]), item.get("last_checked_at"), item.get("last_error"))

    def _audit(self, actor: str, operation: str, target: str, result: str, detail: Mapping[str, Any]) -> None:
        self.repository.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), actor, operation, "worker", target, result, json.dumps(dict(detail)), utc_now()))
