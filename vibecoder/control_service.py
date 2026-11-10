"""Administrative application service owned by the daemon."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from .domain import ExecutionCapacity, GitHubConnectivity
from .operational import derive_operational_state
from .sqlite import SQLiteRepository
if TYPE_CHECKING:
    from .approvals import RuntimeApprovalService
    from .github_identity import GitHubIdentityService
    from .projects import ProjectService
    from .plugin_registry import PluginRegistry
    from .workers import WorkerService


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    gid: int
    supplementary_gids: frozenset[int] = frozenset()

    @property
    def actor(self) -> str:
        return f"unix:uid={self.uid}:gid={self.gid}:pid={self.pid}"


class AuthorizationError(RuntimeError):
    pass


class UnknownOperation(RuntimeError):
    pass


class LocalAuthorizer:
    def __init__(self, admin_gid: int):
        self.admin_gid = admin_gid

    def is_administrator(self, peer: PeerIdentity) -> bool:
        return peer.uid == 0 or peer.gid == self.admin_gid or self.admin_gid in peer.supplementary_gids


class ControlService:
    READ_OPERATIONS = frozenset({"status", "doctor", "github.status", "project.list",
                                 "project.status", "project.users", "plugin.list", "plugin.show",
                                 "worker.list", "worker.status", "approval.list", "approval.show"})
    MUTATING_OPERATIONS = frozenset({"pause", "resume", "github.login", "github.verify",
                                     "project.add", "project.authorize", "project.deauthorize"})

    def __init__(self, repository: SQLiteRepository, authorizer: LocalAuthorizer,
                 github_identity: "GitHubIdentityService | None" = None,
                 projects: "ProjectService | None" = None,
                 plugins: "PluginRegistry | None" = None,
                 workers: "WorkerService | None" = None,
                 approvals: "RuntimeApprovalService | None" = None):
        self.repository = repository
        self.authorizer = authorizer
        self.github_identity = github_identity
        self.projects = projects
        self.plugins = plugins
        self.workers = workers
        self.approvals = approvals
        self.MUTATING_OPERATIONS = self.MUTATING_OPERATIONS | frozenset(
            {"worker.add", "worker.enable", "worker.disable", "worker.login", "worker.login-status", "worker.login-cancel", "worker.logout", "worker.verify",
             "approval.approve", "approval.deny"})
        self._ensure_installation()

    def _ensure_installation(self) -> None:
        if not self.repository.query_one("SELECT id FROM installation LIMIT 1"):
            now = utc_now()
            self.repository.execute("INSERT INTO installation VALUES(?,?,?)",
                                    (str(uuid.uuid4()), now, now))

    def dispatch(self, operation: str, arguments: Mapping[str, Any], peer: PeerIdentity) -> dict[str, Any]:
        if operation not in self.READ_OPERATIONS | self.MUTATING_OPERATIONS:
            raise UnknownOperation(f"operación desconocida: {operation}")
        if operation == "github.login":
            if set(arguments) != {"token"} or not isinstance(arguments.get("token"), str):
                raise ValueError("github.login requiere exclusivamente un token")
        elif operation == "project.add":
            if set(arguments) != {"observation", "branch", "ignore_dirty"} or not isinstance(arguments.get("observation"), Mapping) or (arguments["branch"] is not None and not isinstance(arguments["branch"], str)) or not isinstance(arguments["ignore_dirty"], bool):
                raise ValueError("argumentos de project.add no válidos")
        elif operation in {"project.status", "project.users"}:
            if set(arguments) != {"project"} or not isinstance(arguments.get("project"), str):
                raise ValueError(f"{operation} requiere project")
        elif operation in {"project.authorize", "project.deauthorize"}:
            if set(arguments) != {"project", "login"} or not all(isinstance(arguments.get(k), str) for k in ("project", "login")):
                raise ValueError(f"argumentos de {operation} no válidos")
        elif operation == "plugin.show":
            if set(arguments) != {"plugin"} or not isinstance(arguments.get("plugin"), str): raise ValueError("plugin.show requiere plugin")
        elif operation == "worker.add":
            if set(arguments) != {"plugin", "name"} or not isinstance(arguments.get("plugin"), str) or (arguments["name"] is not None and not isinstance(arguments["name"], str)): raise ValueError("argumentos de worker.add no válidos")
        elif operation in {"worker.status", "worker.enable", "worker.disable", "worker.login", "worker.logout", "worker.verify"}:
            if set(arguments) != {"worker"} or not isinstance(arguments.get("worker"), str): raise ValueError(f"{operation} requiere worker")
        elif operation in {"worker.login-status", "worker.login-cancel"}:
            if set(arguments) != {"worker", "operation_id"} or not all(isinstance(arguments.get(x), str) for x in ("worker", "operation_id")):
                raise ValueError("worker.login-status requiere worker y operation_id")
        elif operation == "approval.list":
            if set(arguments) - {"pending", "worker"} or not isinstance(arguments.get("pending", False), bool) or (arguments.get("worker") is not None and not isinstance(arguments["worker"], str)):
                raise ValueError("argumentos de approval.list no válidos")
        elif operation in {"approval.show", "approval.approve", "approval.deny"}:
            if set(arguments) != {"approval"} or not isinstance(arguments.get("approval"), str):
                raise ValueError(f"{operation} requiere approval")
        elif arguments:
            raise ValueError(f"{operation} no acepta argumentos")
        if operation in self.MUTATING_OPERATIONS and not self.authorizer.is_administrator(peer):
            self.audit(peer.actor, operation, "control", None, "DENIED", "autorización local rechazada")
            raise AuthorizationError("se requiere root o pertenencia a vibecoder-admin")
        if operation == "status":
            return self.status()
        if operation == "doctor":
            return self.doctor()
        if operation.startswith("github."):
            if self.github_identity is None:
                raise ValueError("subsistema GitHub no disponible")
            if operation == "github.status":
                return self.github_identity.status()
            if operation == "github.login":
                return self.github_identity.login(str(arguments["token"]), peer.actor)
            return self.github_identity.verify_current(peer.actor)
        if operation.startswith("project."):
            if self.projects is None:
                raise ValueError("subsistema de proyectos no disponible")
            if operation == "project.add":
                return self.projects.register(arguments["observation"], branch=arguments["branch"],
                                              ignore_dirty=arguments["ignore_dirty"], actor=peer.actor)
            if operation == "project.list":
                return {"projects": self.projects.list()}
            if operation == "project.status":
                return self.projects.get(str(arguments["project"]))
            if operation == "project.users":
                return self.projects.users(str(arguments["project"]))
            if operation == "project.authorize":
                return self.projects.authorize(str(arguments["project"]), str(arguments["login"]), peer.actor)
            return self.projects.deauthorize(str(arguments["project"]), str(arguments["login"]), peer.actor)
        if operation.startswith("plugin."):
            if self.plugins is None: raise ValueError("subsistema de plugins no disponible")
            if operation == "plugin.list": return {"plugins": self.plugins.list()}
            return self.plugins.resolve(str(arguments["plugin"])).public()
        if operation.startswith("worker."):
            if self.workers is None: raise ValueError("subsistema de workers no disponible")
            if operation == "worker.list": return {"workers": self.workers.list()}
            if operation == "worker.status": return self.workers.get(str(arguments["worker"]))
            if operation == "worker.add": return self.workers.add(str(arguments["plugin"]), arguments["name"], peer.actor)
            if operation == "worker.login": return self.workers.start_login(str(arguments["worker"]), peer.actor)
            if operation == "worker.login-status": return self.workers.login_status(str(arguments["worker"]), str(arguments["operation_id"]), peer.actor)
            if operation == "worker.login-cancel": return self.workers.cancel_login(str(arguments["worker"]), str(arguments["operation_id"]), peer.actor)
            if operation == "worker.logout": return self.workers.logout(str(arguments["worker"]), peer.actor)
            if operation == "worker.verify": return self.workers.probe(str(arguments["worker"]), peer.actor)
            if operation == "worker.enable": return self.workers.enable(str(arguments["worker"]), peer.actor)
            return self.workers.disable(str(arguments["worker"]), peer.actor)
        if operation.startswith("approval."):
            if self.approvals is None: raise ValueError("subsistema de aprobaciones no disponible")
            if operation == "approval.list":
                worker_id = None
                if arguments.get("worker"):
                    if self.workers is None: raise ValueError("subsistema de workers no disponible")
                    worker_id = str(self.workers.get(str(arguments["worker"]))["id"])
                return {"approvals": self.approvals.list(pending=bool(arguments.get("pending", False)), worker_id=worker_id)}
            approval_id = str(arguments["approval"])
            if operation == "approval.show": return self.approvals.get(approval_id)
            decision = "APPROVE" if operation == "approval.approve" else "DENY"
            result = self.approvals.decide(approval_id, decision, peer.actor)
            self.audit(peer.actor, operation, "runtime_approval", approval_id, "SUCCEEDED", f"decisión {decision}")
            return result
        if operation == "pause":
            return self.pause(peer.actor)
        return self.resume(peer.actor)

    def status(self) -> dict[str, Any]:
        github_row = self.repository.query_one("SELECT status FROM github_identity WHERE singleton=1")
        github = GitHubConnectivity(github_row["status"]) if github_row else GitHubConnectivity.UNCONFIGURED
        ready_workers = int(self.repository.query_one(
            "SELECT count(*) AS n FROM workers WHERE status='READY' AND active_executions<concurrency_limit")["n"])
        all_workers = int(self.repository.query_one("SELECT count(*) AS n FROM workers")["n"])
        execution = (ExecutionCapacity.AVAILABLE if ready_workers else
                     ExecutionCapacity.DEGRADED if all_workers else ExecutionCapacity.NO_WORKER)
        project_count = int(self.repository.query_one("SELECT count(*) AS n FROM projects")["n"])
        paused = self.repository.query_one(
            "SELECT 1 FROM operational_pauses WHERE scope_type='GLOBAL' AND scope_id IS NULL AND kind='ALL' AND released_at IS NULL LIMIT 1") is not None
        barrier_count = int(self.repository.query_one(
            "SELECT count(*) AS n FROM recovery_barriers WHERE status='ACTIVE'")["n"])
        state = derive_operational_state(github, execution,
            administratively_paused=paused, recovery_barrier=barrier_count > 0,
            projects_available=project_count > 0, eligible_work=False)
        display_state = ("READY/ONLINE" if state.readiness.value == "READY" else
                         "STANDBY/ONLINE" if state.online else "STANDBY/OFFLINE")
        return {
            "operational_state": display_state,
            "public_state": state.readiness.value,
            "connectivity": github.value,
            "online": state.online,
            "execution_capacity": execution.value,
            "administratively_paused": paused,
            "recovery_barrier": barrier_count > 0,
            "recovery_barrier_count": barrier_count,
            "project_count": project_count,
            "ready_worker_count": ready_workers,
            "daemon_available": True,
            "work_allowed": state.work_allowed,
        }

    def doctor(self) -> dict[str, Any]:
        status = self.status()
        integrity = self.repository.integrity_check()
        checks = [
            {"name": "daemon", "status": "OK", "detail": "control API disponible"},
            {"name": "protocol", "status": "OK", "detail": "version 1"},
            {"name": "sqlite", "status": "OK" if integrity == ["ok"] else "ERROR",
             "detail": ", ".join(integrity)},
            {"name": "github", "status": "NOT_CONFIGURED" if status["connectivity"] == "UNCONFIGURED" else status["connectivity"],
             "detail": "ejecute vibecoder github login" if status["connectivity"] == "UNCONFIGURED" else ""},
            {"name": "workers", "status": "NOT_CONFIGURED" if status["ready_worker_count"] == 0 else "OK",
             "detail": "no hay workers READY" if status["ready_worker_count"] == 0 else ""},
            {"name": "recovery_barrier", "status": "ERROR" if status["recovery_barrier"] else "OK",
             "detail": f'{status["recovery_barrier_count"]} barrera(s) activa(s)'},
        ]
        return {"schema_version": self.repository.schema_version, "protocol_version": 1,
                "database_integrity": integrity, "operational": status, "checks": checks}

    def pause(self, actor: str) -> dict[str, Any]:
        row = self.repository.query_one(
            "SELECT id,released_at FROM operational_pauses WHERE scope_type='GLOBAL' AND scope_id IS NULL AND kind='ALL' ORDER BY created_at DESC LIMIT 1")
        if row and row["released_at"] is None:
            return {"changed": False, "paused": True}
        now, pause_id = utc_now(), str(uuid.uuid4())
        with self.repository.transaction():
            self.repository.execute(
                "INSERT INTO operational_pauses(id,scope_type,scope_id,kind,reason,actor,created_at,released_at) VALUES(?,?,?,?,?,?,?,NULL)",
                (pause_id, "GLOBAL", None, "ALL", "administrative pause", actor, now))
            self.audit(actor, "pause", "operational_pause", pause_id, "SUCCEEDED", "pausa global activada")
        return {"changed": True, "paused": True}

    def resume(self, actor: str) -> dict[str, Any]:
        rows = self.repository.query_all(
            "SELECT id FROM operational_pauses WHERE scope_type='GLOBAL' AND scope_id IS NULL AND kind='ALL' AND released_at IS NULL")
        if not rows:
            return {"changed": False, "paused": False}
        now = utc_now()
        with self.repository.transaction():
            for row in rows:
                self.repository.execute("UPDATE operational_pauses SET released_at=? WHERE id=?", (now, row["id"]))
            self.audit(actor, "resume", "operational_pause", None, "SUCCEEDED", "pausa global levantada")
        return {"changed": True, "paused": False}

    def audit(self, actor: str, operation: str, target_type: str,
              target_id: str | None, result: str, detail: str) -> None:
        self.repository.execute(
            "INSERT INTO audit_events(id,actor,operation,target_type,target_id,result,detail_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), actor, operation, target_type, target_id, result,
             json.dumps({"message": detail}, ensure_ascii=False), utc_now()))
