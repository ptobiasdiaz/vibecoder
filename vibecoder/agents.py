"""Gestión persistente y auditada de agentes especializados."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .documents import DocumentResolver, InstructionLoader
from .domain import (
    SPECIALIST_TRANSITIONS,
    AccountStatus,
    AgentConfigurationRevision,
    AgentRevisionStatus,
    AgentStatus,
    AgentType,
    ProjectStatus,
    SpecialistAgent,
    SpecialistStatus,
    TaskStatus,
)
from .errors import ConflictError, InvalidTransition, NotFoundError, ValidationError
from .git import GitAdapter
from .service import OrchestratorCore
from .sqlite import SQLiteRepository


_LOGICAL_ID = re.compile(r"[a-z](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(f"{field} no puede estar vacío")
    return value


def _items(values: Sequence[str], field: str) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in values if item.strip())
    if not normalized:
        raise ValidationError(f"{field} requiere al menos un elemento")
    if len(set(normalized)) != len(normalized):
        raise ValidationError(f"{field} contiene duplicados")
    return normalized


class AgentManagementService:
    def __init__(self, core: OrchestratorCore, *, git: GitAdapter | None = None):
        self.core = core
        self.repository = core.repository
        self.git = git or GitAdapter()

    def propose(
        self,
        project_id: str,
        logical_id: str,
        display_name: str,
        role: str,
        responsibilities: Sequence[str],
        capabilities: Sequence[str],
        restrictions: Sequence[str],
        instructions: str,
        account_id: str,
        workspace: Path,
        *,
        actor: str,
    ) -> tuple[SpecialistAgent, AgentConfigurationRevision]:
        project = self.core.get_project(project_id)
        if project.status is not ProjectStatus.ACTIVE:
            raise ConflictError("solo un proyecto ACTIVE admite especialistas")
        logical_id = logical_id.strip()
        if logical_id == "main" or not _LOGICAL_ID.fullmatch(logical_id):
            raise ValidationError("identificador lógico no válido o reservado")
        account = self.core.get_account(account_id)
        if account.status is AccountStatus.DISABLED:
            raise ConflictError("la cuenta Codex está deshabilitada")
        actor = _required(actor, "actor")
        display_name = _required(display_name, "display_name")
        role = _required(role, "role")
        responsibilities = _items(responsibilities, "responsibilities")
        capabilities = _items(capabilities, "capabilities")
        restrictions = _items(restrictions, "restrictions")
        instructions = _required(instructions, "instructions")
        if len(instructions.encode("utf-8")) > 128 * 1024:
            raise ValidationError("instrucciones excesivamente grandes")
        instructions = instructions.rstrip() + "\n"
        workspace = workspace.resolve(strict=True)
        path = f".vibecoder/agents/{logical_id}/AGENTS.md"
        existing = self.repository.find_one(
            "specialist_agents", project_id=project_id, logical_id=logical_id
        )
        now = _now()
        with self.repository.transaction():
            if existing:
                specialist = self._specialist(existing)
                if specialist.status is SpecialistStatus.RETIRED:
                    raise ConflictError("un identificador retirado no puede reutilizarse")
                if specialist.assigned_account_id != account_id:
                    raise ConflictError("use assign-account para cambiar la cuenta")
                agent_id = specialist.agent_id
            else:
                agent = self.core.register_agent(
                    project_id, logical_id, AgentType.SPECIALIZED,
                    f"specialist:{logical_id}", path,
                    status=AgentStatus.INACTIVE,
                )
                agent_id = agent.id
                self.repository.insert(
                    "specialist_agents",
                    {
                        "agent_id": agent_id, "project_id": project_id,
                        "logical_id": logical_id, "display_name": display_name,
                        "status": SpecialistStatus.PROPOSED.value,
                        "assigned_account_id": account_id, "active_revision_id": None,
                        "created_by": actor, "created_at": now, "updated_at": now,
                    },
                )
            revisions = self.repository.list("agent_configuration_revisions", agent_id=agent_id)
            version = max((int(row["version"]) for row in revisions), default=0) + 1
            instruction_hash = self._hash(instructions)
            payload = {
                "logical_id": logical_id, "display_name": display_name, "role": role,
                "responsibilities": responsibilities, "capabilities": capabilities,
                "restrictions": restrictions, "instructions_path": path,
                "instructions_hash": instruction_hash,
            }
            configuration_hash = self._configuration_hash(payload)
            revision_id = str(uuid4())
            self.repository.insert(
                "agent_configuration_revisions",
                {
                    "id": revision_id, "agent_id": agent_id, "version": version,
                    "status": AgentRevisionStatus.PROPOSED.value, "role": role,
                    "responsibilities_json": json.dumps(responsibilities, ensure_ascii=False),
                    "capabilities_json": json.dumps(capabilities, ensure_ascii=False),
                    "restrictions_json": json.dumps(restrictions, ensure_ascii=False),
                    "instructions_path": path, "instructions_hash": instruction_hash,
                    "configuration_hash": configuration_hash, "source_commit": None,
                    "created_by": actor, "authorized_by": None,
                    "created_at": now, "updated_at": now,
                },
            )
            if existing and specialist.status is not SpecialistStatus.ACTIVE:
                self.repository.update(
                    "specialist_agents", agent_id,
                    {"display_name": display_name, "status": SpecialistStatus.PROPOSED.value,
                     "updated_at": now},
                )
            self._event(agent_id, project_id, "PROPOSE", actor,
                        specialist.status.value if existing else None,
                        self.get(agent_id).status.value, None, version, "COMPLETED")
        resolver = DocumentResolver(workspace)
        resolver.write(path, instructions, overwrite=True)
        return self.get(agent_id), self.revision(revision_id)

    def validate(self, agent_id: str, workspace: Path, *, actor: str) -> AgentConfigurationRevision:
        actor = _required(actor, "actor")
        specialist = self.get(agent_id)
        revision = self.latest_revision(agent_id)
        if revision.status is not AgentRevisionStatus.PROPOSED:
            if revision.status in {AgentRevisionStatus.VALIDATED, AgentRevisionStatus.ACTIVE}:
                return revision
            raise ConflictError("la última revisión no puede validarse")
        self._validate_revision(specialist, revision, workspace, require_versioned=False)
        with self.repository.transaction():
            self.repository.update(
                "agent_configuration_revisions", revision.id,
                {"status": AgentRevisionStatus.VALIDATED.value, "updated_at": _now()},
            )
            self._event(agent_id, specialist.project_id, "VALIDATE", actor,
                        specialist.status.value, specialist.status.value,
                        revision.version, revision.version, "COMPLETED")
        return self.revision(revision.id)

    def activate(
        self, agent_id: str, workspace: Path, *, actor: str, authorized: bool
    ) -> SpecialistAgent:
        specialist = self.get(agent_id)
        self._require_authorized(specialist, "ACTIVATE", actor, authorized)
        project = self.core.get_project(specialist.project_id)
        if project.status is not ProjectStatus.ACTIVE:
            raise ConflictError("el proyecto no está ACTIVE")
        if specialist.status is SpecialistStatus.RETIRED:
            raise ConflictError("un agente retirado no puede activarse")
        account = self.core.get_account(specialist.assigned_account_id)
        if account.status is not AccountStatus.AVAILABLE:
            raise ConflictError("la cuenta Codex asignada no está disponible")
        revision = self.latest_revision(agent_id, AgentRevisionStatus.VALIDATED)
        if revision is None:
            raise ConflictError("no existe una revisión validada pendiente de activación")
        commit = self._validate_revision(specialist, revision, workspace, require_versioned=True)
        previous = self.active_revision(agent_id)
        now = _now()
        with self.repository.transaction():
            if previous and previous.id != revision.id:
                self.repository.update(
                    "agent_configuration_revisions", previous.id,
                    {"status": AgentRevisionStatus.SUPERSEDED.value, "updated_at": now},
                )
            self.repository.update(
                "agent_configuration_revisions", revision.id,
                {"status": AgentRevisionStatus.ACTIVE.value, "source_commit": commit,
                 "authorized_by": _required(actor, "actor"), "updated_at": now},
            )
            self.repository.update(
                "specialist_agents", agent_id,
                {"status": SpecialistStatus.ACTIVE.value, "active_revision_id": revision.id,
                 "updated_at": now},
            )
            self.repository.update(
                "agents", agent_id, {"status": AgentStatus.ACTIVE.value, "updated_at": now},
            )
            self._event(agent_id, specialist.project_id, "ACTIVATE", actor,
                        specialist.status.value, SpecialistStatus.ACTIVE.value,
                        previous.version if previous else None, revision.version, "COMPLETED")
        return self.get(agent_id)

    def suspend(self, agent_id: str, *, actor: str, authorized: bool) -> SpecialistAgent:
        return self._lifecycle(agent_id, SpecialistStatus.SUSPENDED, "SUSPEND", actor, authorized)

    def resume(self, agent_id: str, workspace: Path, *, actor: str, authorized: bool) -> SpecialistAgent:
        specialist = self.get(agent_id)
        if specialist.status is not SpecialistStatus.SUSPENDED:
            raise InvalidTransition("specialist", specialist.status, SpecialistStatus.ACTIVE)
        revision = self.active_revision(agent_id)
        if revision is None:
            raise ConflictError("agente suspendido sin configuración activa")
        self._validate_revision(specialist, revision, workspace, require_versioned=True)
        return self._lifecycle(agent_id, SpecialistStatus.ACTIVE, "RESUME", actor, authorized)

    def retire(self, agent_id: str, *, actor: str, authorized: bool) -> SpecialistAgent:
        return self._lifecycle(agent_id, SpecialistStatus.RETIRED, "RETIRE", actor, authorized)

    def assign_account(
        self, agent_id: str, account_id: str, *, actor: str, authorized: bool
    ) -> SpecialistAgent:
        specialist = self.get(agent_id)
        self._require_authorized(specialist, "ASSIGN_ACCOUNT", actor, authorized)
        if specialist.status is SpecialistStatus.ACTIVE:
            raise ConflictError("suspenda el agente antes de cambiar su cuenta")
        if self._running_tasks(agent_id):
            raise ConflictError("el agente tiene tareas en ejecución")
        account = self.core.get_account(account_id)
        if account.status is AccountStatus.DISABLED:
            raise ConflictError("la cuenta está deshabilitada")
        previous = specialist.assigned_account_id
        with self.repository.transaction():
            self.repository.update(
                "specialist_agents", agent_id,
                {"assigned_account_id": account.id, "updated_at": _now()},
            )
            self._event(agent_id, specialist.project_id, "ASSIGN_ACCOUNT", actor,
                        specialist.status.value, specialist.status.value, None, None,
                        "COMPLETED", f"{previous} -> {account.id}")
        return self.get(agent_id)

    def create_session(self, agent_id: str):
        specialist = self.get(agent_id)
        if specialist.status is not SpecialistStatus.ACTIVE:
            raise ConflictError("solo un especialista activo puede crear sesión")
        return self.core.create_session(agent_id)

    def build_context(self, agent_id: str, workspace: Path, task_context: str):
        specialist = self.get(agent_id)
        if specialist.status is not SpecialistStatus.ACTIVE:
            raise ConflictError("el especialista no está activo")
        revision = self.active_revision(agent_id)
        if revision is None:
            raise ConflictError("el especialista no tiene configuración activa")
        self._validate_revision(specialist, revision, workspace, require_versioned=True)
        return InstructionLoader(Path(workspace).resolve(strict=True)).load(
            specialist.logical_id, task_context
        )

    def check(self, agent_id: str, workspace: Path) -> Mapping[str, Any]:
        specialist = self.get(agent_id)
        issues: list[str] = []
        revision = self.active_revision(agent_id) or self.latest_revision(agent_id)
        if self.core.get_project(specialist.project_id).status is not ProjectStatus.ACTIVE:
            issues.append("proyecto no activo")
        account = self.core.get_account(specialist.assigned_account_id)
        if account.status is not AccountStatus.AVAILABLE:
            issues.append(f"cuenta {account.status.value}")
        try:
            if revision:
                self._validate_revision(
                    specialist, revision, workspace,
                    require_versioned=revision.status is AgentRevisionStatus.ACTIVE,
                )
            else:
                issues.append("sin revisiones")
        except (ConflictError, ValidationError) as exc:
            issues.append(str(exc))
        sessions = self.repository.list("agent_sessions", agent_id=agent_id)
        incompatible = [
            str(row["id"]) for row in sessions
            if str(row["codex_account_id"]) != specialist.assigned_account_id
        ]
        return {
            "consistent": not issues,
            "issues": tuple(issues),
            "historical_sessions_with_other_account": tuple(incompatible),
            "active_revision": revision.version if revision and revision.status is AgentRevisionStatus.ACTIVE else None,
        }

    def get(self, agent_id: str) -> SpecialistAgent:
        row = self.repository.find_one("specialist_agents", agent_id=agent_id)
        if row is None:
            raise NotFoundError(f"especialista no encontrado: {agent_id}")
        return self._specialist(row)

    def get_by_logical_id(self, project_id: str, logical_id: str) -> SpecialistAgent:
        row = self.repository.find_one(
            "specialist_agents", project_id=project_id, logical_id=logical_id
        )
        if row is None:
            raise NotFoundError(f"especialista no encontrado: {logical_id}")
        return self._specialist(row)

    def list(self, project_id: str) -> list[SpecialistAgent]:
        self.core.get_project(project_id)
        return [
            self._specialist(row)
            for row in self.repository.list("specialist_agents", project_id=project_id)
        ]

    def revision(self, revision_id: str) -> AgentConfigurationRevision:
        return self._revision(self.repository.get("agent_configuration_revisions", revision_id))

    def latest_revision(
        self, agent_id: str, status: AgentRevisionStatus | None = None
    ) -> AgentConfigurationRevision | None:
        criteria: dict[str, object] = {"agent_id": agent_id}
        if status:
            criteria["status"] = status.value
        rows = self.repository.list("agent_configuration_revisions", **criteria)
        return self._revision(max(rows, key=lambda row: int(row["version"]))) if rows else None

    def active_revision(self, agent_id: str) -> AgentConfigurationRevision | None:
        return self.latest_revision(agent_id, AgentRevisionStatus.ACTIVE)

    def history(self, agent_id: str) -> list[Mapping[str, object]]:
        self.get(agent_id)
        return self.repository.list("agent_audit_events", agent_id=agent_id)

    def _lifecycle(self, agent_id: str, requested: SpecialistStatus, operation: str,
                   actor: str, authorized: bool) -> SpecialistAgent:
        specialist = self.get(agent_id)
        self._require_authorized(specialist, operation, actor, authorized)
        if requested not in SPECIALIST_TRANSITIONS[specialist.status]:
            raise InvalidTransition("specialist", specialist.status, requested)
        if requested in {SpecialistStatus.SUSPENDED, SpecialistStatus.RETIRED} and self._running_tasks(agent_id):
            raise ConflictError("el agente tiene tareas en ejecución")
        now = _now()
        with self.repository.transaction():
            self.repository.update(
                "specialist_agents", agent_id,
                {"status": requested.value, "updated_at": now},
            )
            legacy_status = AgentStatus.ACTIVE if requested is SpecialistStatus.ACTIVE else AgentStatus.INACTIVE
            self.repository.update("agents", agent_id, {"status": legacy_status.value, "updated_at": now})
            active = self.active_revision(agent_id)
            self._event(agent_id, specialist.project_id, operation, actor,
                        specialist.status.value, requested.value,
                        active.version if active else None, active.version if active else None,
                        "COMPLETED")
        return self.get(agent_id)

    def _require_authorized(
        self, specialist: SpecialistAgent, operation: str, actor: str, authorized: bool
    ) -> None:
        actor = _required(actor, "actor")
        if authorized:
            return
        active = self.active_revision(specialist.agent_id)
        self._event(
            specialist.agent_id, specialist.project_id, operation, actor,
            specialist.status.value, specialist.status.value,
            active.version if active else None, active.version if active else None,
            "REJECTED", "falta autorización explícita",
        )
        raise ConflictError("la operación requiere autorización explícita")

    def _validate_revision(self, specialist: SpecialistAgent, revision: AgentConfigurationRevision,
                           workspace: Path, *, require_versioned: bool) -> str | None:
        workspace = Path(workspace).resolve(strict=True)
        expected = f".vibecoder/agents/{specialist.logical_id}/AGENTS.md"
        if revision.instructions_path != expected:
            raise ValidationError("ruta de instrucciones incompatible")
        context = InstructionLoader(workspace).load(specialist.logical_id, "validation")
        current = context.versions.get(expected)
        if current != revision.instructions_hash:
            raise ConflictError("las instrucciones cambiaron respecto a la revisión")
        if not require_versioned:
            return None
        status = self.git.status(workspace)
        if status.changes or not status.head:
            raise ConflictError("el checkout debe estar limpio y versionado")
        main_branch = self._main_branch(specialist.project_id)
        if status.branch != main_branch:
            raise ConflictError(f"la configuración debe estar integrada en {main_branch}")
        tracked = self.git.tracked_file_at_head(workspace, expected)
        if tracked is None or self._hash(tracked) != revision.instructions_hash:
            raise ConflictError("las instrucciones no están integradas en HEAD")
        return status.head

    def _main_branch(self, project_id: str) -> str:
        bootstrap = self.repository.find_one("bootstrap_workflows", project_id=project_id)
        if bootstrap:
            configs = [
                *self.repository.list("git_configurations", workflow_id=str(bootstrap["id"]), status="APPLIED"),
                *self.repository.list("git_configurations", workflow_id=str(bootstrap["id"]), status="APPROVED"),
            ]
            if configs:
                payload = json.loads(str(max(configs, key=lambda row: int(row["revision"]))["config_json"]))
                return str(payload["main_branch"])
        return "main"

    def _running_tasks(self, agent_id: str) -> bool:
        return bool(self.repository.list("tasks", agent_id=agent_id, status=TaskStatus.RUNNING.value))

    def _event(self, agent_id: str, project_id: str, operation: str, actor: str,
               previous_status: str | None, new_status: str | None,
               previous_version: int | None, new_version: int | None,
               result: str, detail: str | None = None) -> None:
        self.repository.insert(
            "agent_audit_events",
            {"id": str(uuid4()), "agent_id": agent_id, "project_id": project_id,
             "operation": operation, "actor": _required(actor, "actor"),
             "previous_status": previous_status, "new_status": new_status,
             "previous_version": previous_version, "new_version": new_version,
             "result": result, "detail": detail, "created_at": _now()},
        )

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _configuration_hash(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _specialist(row: Mapping[str, Any]) -> SpecialistAgent:
        return SpecialistAgent(
            agent_id=str(row["agent_id"]), project_id=str(row["project_id"]),
            logical_id=str(row["logical_id"]), display_name=str(row["display_name"]),
            status=SpecialistStatus(str(row["status"])),
            assigned_account_id=str(row["assigned_account_id"]),
            active_revision_id=str(row["active_revision_id"]) if row["active_revision_id"] else None,
            created_by=str(row["created_by"]), created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _revision(row: Mapping[str, Any]) -> AgentConfigurationRevision:
        return AgentConfigurationRevision(
            id=str(row["id"]), agent_id=str(row["agent_id"]), version=int(row["version"]),
            status=AgentRevisionStatus(str(row["status"])), role=str(row["role"]),
            responsibilities=tuple(json.loads(str(row["responsibilities_json"]))),
            capabilities=tuple(json.loads(str(row["capabilities_json"]))),
            restrictions=tuple(json.loads(str(row["restrictions_json"]))),
            instructions_path=str(row["instructions_path"]),
            instructions_hash=str(row["instructions_hash"]),
            configuration_hash=str(row["configuration_hash"]),
            source_commit=str(row["source_commit"]) if row["source_commit"] else None,
            created_by=str(row["created_by"]),
            authorized_by=str(row["authorized_by"]) if row["authorized_by"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--actor", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("list"); command.add_argument("project_id")
    command = sub.add_parser("show"); command.add_argument("agent_id")
    command = sub.add_parser("propose")
    command.add_argument("project_id"); command.add_argument("logical_id")
    command.add_argument("--name", required=True); command.add_argument("--role", required=True)
    command.add_argument("--responsibility", action="append", required=True)
    command.add_argument("--capability", action="append", required=True)
    command.add_argument("--restriction", action="append", required=True)
    command.add_argument("--instructions-file", required=True, type=Path)
    command.add_argument("--account", required=True)
    for name in ("validate", "activate", "suspend", "resume", "retire", "history", "check", "session"):
        command = sub.add_parser(name); command.add_argument("agent_id")
        if name in {"activate", "suspend", "resume", "retire"}:
            command.add_argument("--authorize", action="store_true")
    command = sub.add_parser("assign-account"); command.add_argument("agent_id")
    command.add_argument("account_id"); command.add_argument("--authorize", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    with SQLiteRepository(Path(args.database).resolve()) as repository:
        service = AgentManagementService(OrchestratorCore(repository))
        workspace = args.workspace.resolve()
        if args.command == "list": result: object = service.list(args.project_id)
        elif args.command == "show": result = service.get(args.agent_id)
        elif args.command == "propose":
            result = service.propose(
                args.project_id, args.logical_id, args.name, args.role,
                args.responsibility, args.capability, args.restriction,
                args.instructions_file.read_text(encoding="utf-8"), args.account,
                workspace, actor=args.actor,
            )
        elif args.command == "validate": result = service.validate(args.agent_id, workspace, actor=args.actor)
        elif args.command == "activate": result = service.activate(args.agent_id, workspace, actor=args.actor, authorized=args.authorize)
        elif args.command == "suspend": result = service.suspend(args.agent_id, actor=args.actor, authorized=args.authorize)
        elif args.command == "resume": result = service.resume(args.agent_id, workspace, actor=args.actor, authorized=args.authorize)
        elif args.command == "retire": result = service.retire(args.agent_id, actor=args.actor, authorized=args.authorize)
        elif args.command == "history": result = service.history(args.agent_id)
        elif args.command == "check": result = service.check(args.agent_id, workspace)
        elif args.command == "session": result = service.create_session(args.agent_id)
        else: result = service.assign_account(args.agent_id, args.account_id, actor=args.actor, authorized=args.authorize)
        if isinstance(result, tuple): output = [asdict(item) for item in result]
        elif isinstance(result, list): output = [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in result]
        else: output = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
