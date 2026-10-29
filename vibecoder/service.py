"""Servicios de aplicación del núcleo persistente de M01."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .domain import (
    SESSION_TRANSITIONS,
    TASK_TRANSITIONS,
    PROJECT_TRANSITIONS,
    AccountStatus,
    Agent,
    AgentSession,
    AgentStatus,
    AgentType,
    AttemptStatus,
    AuthType,
    CodexAccount,
    ExecutionAttempt,
    Project,
    ProjectStatus,
    SessionPersistenceState,
    Task,
    TaskStatus,
)
from .errors import ConflictError, InvalidTransition, ValidationError
from .ports import Repository


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _id() -> str:
    return str(uuid4())


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(f"{field} no puede estar vacío")
    return value


class OrchestratorCore:
    """Fachada programática; no conoce GitHub, red, credenciales ni App Server."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def close(self) -> None:
        self.repository.close()

    def __enter__(self) -> OrchestratorCore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def register_account(
        self,
        name: str,
        auth_type: AuthType | str,
        credential_ref: str,
        *,
        status: AccountStatus | str = AccountStatus.AVAILABLE,
        account_id: str | None = None,
    ) -> CodexAccount:
        now = _now()
        values = {
            "id": account_id or _id(),
            "name": _required(name, "name"),
            "auth_type": AuthType(auth_type).value,
            "credential_ref": _required(credential_ref, "credential_ref"),
            "status": AccountStatus(status).value,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.insert("codex_accounts", values)
        return self.get_account(str(values["id"]))

    def get_account(self, account_id: str) -> CodexAccount:
        row = self.repository.get("codex_accounts", account_id)
        return CodexAccount(
            id=str(row["id"]),
            name=str(row["name"]),
            auth_type=AuthType(str(row["auth_type"])),
            credential_ref=str(row["credential_ref"]),
            status=AccountStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def set_account_status(
        self, account_id: str, status: AccountStatus | str
    ) -> CodexAccount:
        self.get_account(account_id)
        self.repository.update(
            "codex_accounts",
            account_id,
            {"status": AccountStatus(status).value, "updated_at": _now()},
        )
        return self.get_account(account_id)

    def register_project(
        self,
        name: str,
        codex_account_id: str,
        *,
        github_repository: str | None = None,
        status: ProjectStatus | str = ProjectStatus.UNINITIALIZED,
        project_id: str | None = None,
    ) -> Project:
        self.get_account(codex_account_id)
        now = _now()
        values = {
            "id": project_id or _id(),
            "name": _required(name, "name"),
            "github_repository": github_repository.strip() if github_repository else None,
            "codex_account_id": codex_account_id,
            "status": ProjectStatus(status).value,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.insert("projects", values)
        return self.get_project(str(values["id"]))

    def get_project(self, project_id: str) -> Project:
        row = self.repository.get("projects", project_id)
        return Project(
            id=str(row["id"]),
            name=str(row["name"]),
            github_repository=str(row["github_repository"]) if row["github_repository"] else None,
            codex_account_id=str(row["codex_account_id"]),
            status=ProjectStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_projects(self) -> list[Project]:
        return [self.get_project(str(row["id"])) for row in self.repository.list("projects")]

    def transition_project(
        self, project_id: str, requested: ProjectStatus | str
    ) -> Project:
        project = self.get_project(project_id)
        requested = ProjectStatus(requested)
        if requested not in PROJECT_TRANSITIONS[project.status]:
            raise InvalidTransition("project", project.status, requested)
        self.repository.update(
            "projects", project_id, {"status": requested.value, "updated_at": _now()}
        )
        return self.get_project(project_id)

    def assign_project_account(self, project_id: str, account_id: str) -> Project:
        project = self.get_project(project_id)
        self.get_account(account_id)
        if project.codex_account_id == account_id:
            return project
        agents = self.repository.list("agents", project_id=project_id)
        if any(self.repository.list("agent_sessions", agent_id=str(agent["id"])) for agent in agents):
            raise ConflictError("no se puede cambiar la cuenta de un proyecto con sesiones")
        self.repository.update(
            "projects", project_id, {"codex_account_id": account_id, "updated_at": _now()}
        )
        return self.get_project(project_id)

    def register_agent(
        self,
        project_id: str,
        name: str,
        agent_type: AgentType | str,
        config_ref: str,
        documentation_ref: str,
        *,
        status: AgentStatus | str = AgentStatus.ACTIVE,
        agent_id: str | None = None,
    ) -> Agent:
        self.get_project(project_id)
        now = _now()
        values = {
            "id": agent_id or _id(),
            "project_id": project_id,
            "name": _required(name, "name"),
            "agent_type": AgentType(agent_type).value,
            "config_ref": _required(config_ref, "config_ref"),
            "documentation_ref": _required(documentation_ref, "documentation_ref"),
            "status": AgentStatus(status).value,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.insert("agents", values)
        return self.get_agent(str(values["id"]))

    def get_agent(self, agent_id: str) -> Agent:
        row = self.repository.get("agents", agent_id)
        return Agent(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            agent_type=AgentType(str(row["agent_type"])),
            config_ref=str(row["config_ref"]),
            documentation_ref=str(row["documentation_ref"]),
            status=AgentStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_agents(self, project_id: str) -> list[Agent]:
        self.get_project(project_id)
        return [
            self.get_agent(str(row["id"]))
            for row in self.repository.list("agents", project_id=project_id)
        ]

    def create_session(
        self, agent_id: str, *, session_id: str | None = None
    ) -> AgentSession:
        agent = self.get_agent(agent_id)
        project = self.get_project(agent.project_id)
        now = _now()
        values = {
            "id": session_id or _id(),
            "agent_id": agent_id,
            "codex_thread_id": None,
            "codex_account_id": project.codex_account_id,
            "persistence_state": SessionPersistenceState.NOT_CREATED.value,
            "last_activity_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.insert("agent_sessions", values)
        return self.get_session(str(values["id"]))

    def get_session(self, session_id: str) -> AgentSession:
        row = self.repository.get("agent_sessions", session_id)
        return AgentSession(
            id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            codex_thread_id=str(row["codex_thread_id"]) if row["codex_thread_id"] else None,
            codex_account_id=str(row["codex_account_id"]),
            persistence_state=SessionPersistenceState(str(row["persistence_state"])),
            last_activity_at=str(row["last_activity_at"]) if row["last_activity_at"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def transition_session(
        self,
        session_id: str,
        requested: SessionPersistenceState | str,
        *,
        codex_thread_id: str | None = None,
    ) -> AgentSession:
        session = self.get_session(session_id)
        requested = SessionPersistenceState(requested)
        if requested not in SESSION_TRANSITIONS[session.persistence_state]:
            raise InvalidTransition("session", session.persistence_state, requested)
        thread_id = codex_thread_id or session.codex_thread_id
        if requested in {
            SessionPersistenceState.THREAD_CREATED_UNCONFIRMED,
            SessionPersistenceState.THREAD_PERSISTED,
            SessionPersistenceState.RECOVERABLE,
        } and not thread_id:
            raise ValidationError("la sesión requiere codex_thread_id para ese estado")
        now = _now()
        self.repository.update(
            "agent_sessions",
            session_id,
            {
                "persistence_state": requested.value,
                "codex_thread_id": thread_id,
                "last_activity_at": now,
                "updated_at": now,
            },
        )
        return self.get_session(session_id)

    def create_task(
        self,
        project_id: str,
        agent_id: str,
        task_type: str,
        *,
        external_ref: str | None = None,
        idempotency_key: str | None = None,
        task_id: str | None = None,
        prompt: str | None = None,
    ) -> Task:
        self.get_project(project_id)
        agent = self.get_agent(agent_id)
        if agent.project_id != project_id:
            raise ConflictError("el agente no pertenece al proyecto de la tarea")
        task_type = _required(task_type, "task_type")
        idempotency_key = idempotency_key.strip() if idempotency_key else None
        external_ref = external_ref.strip() if external_ref else None
        if idempotency_key:
            existing = self.repository.find_one(
                "tasks", project_id=project_id, idempotency_key=idempotency_key
            )
            if existing:
                if (
                    existing["agent_id"] != agent_id
                    or existing["task_type"] != task_type
                    or existing["external_ref"] != external_ref
                    or existing["prompt"] != prompt
                ):
                    raise ConflictError("idempotency_key reutilizada con datos diferentes")
                return self.get_task(str(existing["id"]))
        now = _now()
        values = {
            "id": task_id or _id(),
            "project_id": project_id,
            "agent_id": agent_id,
            "task_type": task_type,
            "status": TaskStatus.PENDING.value,
            "external_ref": external_ref,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
            "prompt": prompt,
        }
        try:
            self.repository.insert("tasks", values)
        except ConflictError:
            if not idempotency_key:
                raise
            existing = self.repository.find_one(
                "tasks", project_id=project_id, idempotency_key=idempotency_key
            )
            if existing is None:
                raise
            if (
                existing["agent_id"] != agent_id
                or existing["task_type"] != task_type
                or existing["external_ref"] != external_ref
                or existing["prompt"] != prompt
            ):
                raise ConflictError("idempotency_key reutilizada con datos diferentes")
            return self.get_task(str(existing["id"]))
        return self.get_task(str(values["id"]))

    def get_task(self, task_id: str) -> Task:
        row = self.repository.get("tasks", task_id)
        return Task(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            agent_id=str(row["agent_id"]),
            task_type=str(row["task_type"]),
            status=TaskStatus(str(row["status"])),
            external_ref=str(row["external_ref"]) if row["external_ref"] else None,
            idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] else None,
            prompt=str(row["prompt"]) if row["prompt"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_pending_tasks(self, project_id: str | None = None) -> list[Task]:
        pending_states = (
            TaskStatus.PENDING,
            TaskStatus.WAITING,
            TaskStatus.WAITING_QUOTA,
            TaskStatus.WAITING_AUTH,
        )
        tasks: list[Task] = []
        for status in pending_states:
            criteria: dict[str, object] = {"status": status.value}
            if project_id:
                criteria["project_id"] = project_id
            tasks.extend(
                self.get_task(str(row["id"])) for row in self.repository.list("tasks", **criteria)
            )
        return sorted(tasks, key=lambda task: (task.created_at, task.id))

    def transition_task(self, task_id: str, requested: TaskStatus | str) -> Task:
        task = self.get_task(task_id)
        requested = TaskStatus(requested)
        if requested not in TASK_TRANSITIONS[task.status]:
            raise InvalidTransition("task", task.status, requested)
        self.repository.update(
            "tasks", task_id, {"status": requested.value, "updated_at": _now()}
        )
        return self.get_task(task_id)

    def assign_task(self, task_id: str, agent_id: str) -> Task:
        task = self.get_task(task_id)
        if task.status not in {
            TaskStatus.PENDING,
            TaskStatus.WAITING,
            TaskStatus.WAITING_QUOTA,
            TaskStatus.WAITING_AUTH,
        }:
            raise ConflictError("solo se puede reasignar una tarea que no está ejecutándose")
        agent = self.get_agent(agent_id)
        if agent.project_id != task.project_id:
            raise ConflictError("el agente no pertenece al proyecto de la tarea")
        self.repository.update(
            "tasks", task_id, {"agent_id": agent_id, "updated_at": _now()}
        )
        return self.get_task(task_id)

    def start_attempt(self, task_id: str) -> ExecutionAttempt:
        with self.repository.transaction():
            task = self.get_task(task_id)
            if TaskStatus.RUNNING not in TASK_TRANSITIONS[task.status]:
                raise InvalidTransition("task", task.status, TaskStatus.RUNNING)
            next_number = self.repository.next_attempt_number(task_id)
            now = _now()
            attempt_id = _id()
            self.repository.update(
                "tasks", task_id, {"status": TaskStatus.RUNNING.value, "updated_at": now}
            )
            self.repository.insert(
                "execution_attempts",
                {
                    "id": attempt_id,
                    "task_id": task_id,
                    "attempt_number": next_number,
                    "status": AttemptStatus.RUNNING.value,
                    "started_at": now,
                    "finished_at": None,
                    "error_code": None,
                    "error_category": None,
                    "error_message": None,
                    "error_details_json": None,
                    "session_id": None,
                    "codex_thread_id": None,
                    "codex_turn_id": None,
                    "result_text": None,
                },
            )
        return self.get_attempt(attempt_id)

    def finish_attempt(
        self,
        attempt_id: str,
        outcome: AttemptStatus | str,
        *,
        error_code: str | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
        error_details: dict[str, Any] | None = None,
        task_status: TaskStatus | str | None = None,
        result_text: str | None = None,
    ) -> ExecutionAttempt:
        outcome = AttemptStatus(outcome)
        if outcome not in {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.UNKNOWN,
            AttemptStatus.CANCELLED,
        }:
            raise ValidationError("estado final de intento no válido")
        with self.repository.transaction():
            attempt = self.get_attempt(attempt_id)
            if attempt.status is not AttemptStatus.RUNNING:
                raise ConflictError("el intento ya tiene estado final")
            final_task_status = {
                AttemptStatus.SUCCEEDED: TaskStatus.COMPLETED,
                AttemptStatus.FAILED: TaskStatus.FAILED,
                AttemptStatus.UNKNOWN: TaskStatus.WAITING,
                AttemptStatus.CANCELLED: TaskStatus.CANCELLED,
            }[outcome]
            if task_status is not None:
                final_task_status = TaskStatus(task_status)
            now = _now()
            self.repository.update(
                "execution_attempts",
                attempt_id,
                {
                    "status": outcome.value,
                    "finished_at": now,
                    "error_code": error_code,
                    "error_category": error_category,
                    "error_message": error_message,
                    "error_details_json": json.dumps(error_details, sort_keys=True)
                    if error_details
                    else None,
                    "result_text": result_text,
                },
            )
            self.repository.update(
                "tasks",
                attempt.task_id,
                {"status": final_task_status.value, "updated_at": now},
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt:
        row = self.repository.get("execution_attempts", attempt_id)
        details = (
            json.loads(str(row["error_details_json"])) if row["error_details_json"] else None
        )
        return ExecutionAttempt(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            attempt_number=int(row["attempt_number"]),
            status=AttemptStatus(str(row["status"])),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_category=str(row["error_category"]) if row["error_category"] else None,
            error_message=str(row["error_message"]) if row["error_message"] else None,
            error_details=details,
            session_id=str(row["session_id"]) if row["session_id"] else None,
            codex_thread_id=str(row["codex_thread_id"]) if row["codex_thread_id"] else None,
            codex_turn_id=str(row["codex_turn_id"]) if row["codex_turn_id"] else None,
            result_text=str(row["result_text"]) if row["result_text"] else None,
        )

    def bind_attempt_session(self, attempt_id: str, session_id: str) -> ExecutionAttempt:
        session = self.get_session(session_id)
        attempt = self.get_attempt(attempt_id)
        task = self.get_task(attempt.task_id)
        agent = self.get_agent(session.agent_id)
        if agent.project_id != task.project_id or agent.id != task.agent_id:
            raise ConflictError("la sesión no pertenece al agente de la tarea")
        self.repository.update("execution_attempts", attempt_id, {"session_id": session_id})
        return self.get_attempt(attempt_id)

    def bind_attempt_thread(self, attempt_id: str, thread_id: str) -> ExecutionAttempt:
        self.repository.update(
            "execution_attempts", attempt_id, {"codex_thread_id": _required(thread_id, "thread_id")}
        )
        return self.get_attempt(attempt_id)

    def bind_attempt_turn(self, attempt_id: str, turn_id: str) -> ExecutionAttempt:
        self.repository.update(
            "execution_attempts", attempt_id, {"codex_turn_id": _required(turn_id, "turn_id")}
        )
        return self.get_attempt(attempt_id)

    def get_or_create_session(self, agent_id: str) -> AgentSession:
        self.get_agent(agent_id)
        rows = self.repository.list("agent_sessions", agent_id=agent_id)
        usable = [row for row in rows if row["persistence_state"] != SessionPersistenceState.LOST]
        if usable:
            return self.get_session(str(usable[-1]["id"]))
        return self.create_session(agent_id)

    def list_attempts_by_status(self, status: AttemptStatus | str) -> list[ExecutionAttempt]:
        return [
            self.get_attempt(str(row["id"]))
            for row in self.repository.list("execution_attempts", status=AttemptStatus(status).value)
        ]

    def resolve_unknown_attempt(
        self,
        attempt_id: str,
        outcome: AttemptStatus | str,
        *,
        result_text: str | None = None,
        error_code: str | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> ExecutionAttempt:
        outcome = AttemptStatus(outcome)
        if outcome not in {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
        }:
            raise ValidationError("la reconciliación requiere un resultado final confirmado")
        with self.repository.transaction():
            attempt = self.get_attempt(attempt_id)
            if attempt.status is not AttemptStatus.UNKNOWN:
                raise ConflictError("solo se reconcilian intentos UNKNOWN")
            task_status = {
                AttemptStatus.SUCCEEDED: TaskStatus.COMPLETED,
                AttemptStatus.FAILED: TaskStatus.FAILED,
                AttemptStatus.CANCELLED: TaskStatus.CANCELLED,
            }[outcome]
            now = _now()
            self.repository.update(
                "execution_attempts",
                attempt_id,
                {
                    "status": outcome.value,
                    "finished_at": now,
                    "result_text": result_text,
                    "error_code": error_code,
                    "error_category": error_category,
                    "error_message": error_message,
                },
            )
            self.repository.update(
                "tasks", attempt.task_id, {"status": task_status.value, "updated_at": now}
            )
        return self.get_attempt(attempt_id)

    def list_attempts(self, task_id: str) -> list[ExecutionAttempt]:
        self.get_task(task_id)
        return [
            self.get_attempt(str(row["id"]))
            for row in self.repository.list("execution_attempts", task_id=task_id)
        ]

    def recover_interrupted_tasks(self) -> list[Task]:
        """Marca resultados inciertos como WAITING; nunca los reejecuta automáticamente."""
        recovered: list[Task] = []
        with self.repository.transaction():
            running_tasks = self.repository.list("tasks", status=TaskStatus.RUNNING.value)
            now = _now()
            for row in running_tasks:
                task_id = str(row["id"])
                for attempt in self.repository.list(
                    "execution_attempts", task_id=task_id, status=AttemptStatus.RUNNING.value
                ):
                    self.repository.update(
                        "execution_attempts",
                        str(attempt["id"]),
                        {
                            "status": AttemptStatus.UNKNOWN.value,
                            "finished_at": now,
                            "error_code": "PROCESS_RESTARTED",
                            "error_category": "recovery",
                            "error_message": "resultado desconocido tras reiniciar el proceso",
                            "error_details_json": None,
                        },
                    )
                self.repository.update(
                    "tasks",
                    task_id,
                    {"status": TaskStatus.WAITING.value, "updated_at": now},
                )
                recovered.append(self.get_task(task_id))
        return recovered

    def recover_interrupted_task(self, task_id: str) -> Task:
        """Recupera una tarea concreta sin afectar otros workers o flujos."""
        with self.repository.transaction():
            task = self.get_task(task_id)
            if task.status is not TaskStatus.RUNNING:
                return task
            now = _now()
            for attempt in self.repository.list(
                "execution_attempts",
                task_id=task_id,
                status=AttemptStatus.RUNNING.value,
            ):
                self.repository.update(
                    "execution_attempts",
                    str(attempt["id"]),
                    {
                        "status": AttemptStatus.UNKNOWN.value,
                        "finished_at": now,
                        "error_code": "PROCESS_RESTARTED",
                        "error_category": "recovery",
                        "error_message": "resultado desconocido tras reiniciar el proceso",
                        "error_details_json": None,
                    },
                )
            self.repository.update(
                "tasks",
                task_id,
                {"status": TaskStatus.WAITING.value, "updated_at": now},
            )
        return self.get_task(task_id)

