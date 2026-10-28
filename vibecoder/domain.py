"""Entidades y máquinas de estado del núcleo, sin dependencias de persistencia."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class AuthType(StrEnum):
    CHATGPT = "CHATGPT"
    API_KEY = "API_KEY"
    ACCESS_TOKEN = "ACCESS_TOKEN"


class AccountStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    DISABLED = "DISABLED"


class ProjectStatus(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    DESIGNING = "DESIGNING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AgentType(StrEnum):
    PRINCIPAL = "PRINCIPAL"
    SPECIALIZED = "SPECIALIZED"


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNAVAILABLE = "UNAVAILABLE"


class SessionPersistenceState(StrEnum):
    NOT_CREATED = "NOT_CREATED"
    THREAD_CREATED_UNCONFIRMED = "THREAD_CREATED_UNCONFIRMED"
    THREAD_PERSISTED = "THREAD_PERSISTED"
    RECOVERABLE = "RECOVERABLE"
    UNAVAILABLE = "UNAVAILABLE"
    LOST = "LOST"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_QUOTA = "WAITING_QUOTA"
    WAITING_AUTH = "WAITING_AUTH"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING,
            TaskStatus.WAITING_QUOTA,
            TaskStatus.WAITING_AUTH,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING_QUOTA: frozenset(
        {TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING_AUTH: frozenset(
        {TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.PENDING}),
    TaskStatus.CANCELLED: frozenset(),
}


SESSION_TRANSITIONS: Mapping[
    SessionPersistenceState, frozenset[SessionPersistenceState]
] = {
    SessionPersistenceState.NOT_CREATED: frozenset(
        {
            SessionPersistenceState.THREAD_CREATED_UNCONFIRMED,
            SessionPersistenceState.UNAVAILABLE,
        }
    ),
    SessionPersistenceState.THREAD_CREATED_UNCONFIRMED: frozenset(
        {
            SessionPersistenceState.THREAD_PERSISTED,
            SessionPersistenceState.UNAVAILABLE,
            SessionPersistenceState.LOST,
        }
    ),
    SessionPersistenceState.THREAD_PERSISTED: frozenset(
        {
            SessionPersistenceState.RECOVERABLE,
            SessionPersistenceState.UNAVAILABLE,
            SessionPersistenceState.LOST,
        }
    ),
    SessionPersistenceState.RECOVERABLE: frozenset(
        {SessionPersistenceState.UNAVAILABLE, SessionPersistenceState.LOST}
    ),
    SessionPersistenceState.UNAVAILABLE: frozenset(
        {SessionPersistenceState.RECOVERABLE, SessionPersistenceState.LOST}
    ),
    SessionPersistenceState.LOST: frozenset(),
}


@dataclass(frozen=True)
class CodexAccount:
    id: str
    name: str
    auth_type: AuthType
    credential_ref: str
    status: AccountStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    github_repository: str | None
    codex_account_id: str
    status: ProjectStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Agent:
    id: str
    project_id: str
    name: str
    agent_type: AgentType
    config_ref: str
    documentation_ref: str
    status: AgentStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentSession:
    id: str
    agent_id: str
    codex_thread_id: str | None
    codex_account_id: str
    persistence_state: SessionPersistenceState
    last_activity_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Task:
    id: str
    project_id: str
    agent_id: str
    task_type: str
    status: TaskStatus
    external_ref: str | None
    idempotency_key: str | None
    prompt: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ExecutionAttempt:
    id: str
    task_id: str
    attempt_number: int
    status: AttemptStatus
    started_at: str
    finished_at: str | None
    error_code: str | None
    error_category: str | None
    error_message: str | None
    error_details: Mapping[str, Any] | None
    session_id: str | None
    codex_thread_id: str | None
    codex_turn_id: str | None
    result_text: str | None

