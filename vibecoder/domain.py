"""Provider-neutral domain model for vibecoder 0.0.1.

Provider identifiers are opaque strings interpreted only by their plugin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ValidationError


def _required(value: str, name: str) -> None:
    if not value.strip():
        raise ValidationError(f"{name} no puede estar vacío")


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class ProjectStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"


class AgentStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class WorkerStatus(StrEnum):
    INSTALLING = "INSTALLING"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    VERIFYING = "VERIFYING"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class PluginCapability(StrEnum):
    PERSISTENT_SESSIONS = "PERSISTENT_SESSIONS"
    SESSION_CONTINUATION = "SESSION_CONTINUATION"
    CANCELLATION = "CANCELLATION"
    RECONCILIATION = "RECONCILIATION"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    WORKSPACE_EXECUTION = "WORKSPACE_EXECUTION"


class AuthenticationMethod(StrEnum):
    DEVICE_CODE = "DEVICE_CODE"
    API_KEY = "API_KEY"
    IMPORT = "IMPORT"
    NONE = "NONE"


class ProviderSessionStatus(StrEnum):
    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    UNAVAILABLE = "UNAVAILABLE"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class ExecutionState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RECONCILING = "RECONCILING"
    FINISHED = "FINISHED"


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    WAITING_AUTH = "WAITING_AUTH"
    WAITING_QUOTA = "WAITING_QUOTA"
    UNSUPPORTED = "UNSUPPORTED"


class RuntimeApprovalKind(StrEnum):
    COMMAND = "COMMAND"
    FILE_CHANGE = "FILE_CHANGE"
    PERMISSIONS = "PERMISSIONS"


class RuntimeApprovalStatus(StrEnum):
    PENDING = "PENDING"
    DECISION_RECORDED = "DECISION_RECORDED"
    DELIVERING = "DELIVERING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class RuntimeApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


class PlanRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    BLOCKED = "BLOCKED"


class ExternalOperationStatus(StrEnum):
    INTENDED = "INTENDED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class GitHubConnectivity(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    AUTH_FAILED = "AUTH_FAILED"


class ExecutionCapacity(StrEnum):
    NO_WORKER = "NO_WORKER"
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"


class PublicReadiness(StrEnum):
    STANDBY = "STANDBY"
    READY = "READY"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_DEPENDENCIES = "WAITING_DEPENDENCIES"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Installation:
    id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitHubIdentity:
    github_user_id: int
    login: str
    credential_reference: str
    status: GitHubConnectivity
    configured_at: str
    checked_at: str | None = None
    diagnostic: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.github_user_id <= 0:
            raise ValidationError("github_user_id debe ser positivo")
        _required(self.login, "login")
        _required(self.credential_reference, "credential_reference")
        object.__setattr__(self, "diagnostic", _mapping(self.diagnostic))


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    github_repository_id: int
    canonical_name: str
    base_branch: str
    remote_sha: str
    status: ProjectStatus
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        for value, name in ((self.id, "id"), (self.name, "name"), (self.base_branch, "base_branch"), (self.remote_sha, "remote_sha")):
            _required(value, name)
        if self.github_repository_id <= 0:
            raise ValidationError("github_repository_id debe ser positivo")
        if len(self.canonical_name.split("/")) != 2 or any(not p.strip() for p in self.canonical_name.split("/")):
            raise ValidationError("canonical_name debe tener formato owner/repository")


@dataclass(frozen=True)
class ProjectAuthorization:
    project_id: str
    github_user_id: int
    login: str
    authorized_at: str
    authorized_by: str | None = None

    def __post_init__(self) -> None:
        _required(self.project_id, "project_id")
        _required(self.login, "login")
        if self.github_user_id <= 0:
            raise ValidationError("github_user_id debe ser positivo")


@dataclass(frozen=True)
class PluginDescriptor:
    plugin_id: str
    version: str
    provider: str
    supported_platforms: frozenset[str]
    authentication_methods: frozenset[AuthenticationMethod]
    capabilities: frozenset[PluginCapability]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in ((self.plugin_id, "plugin_id"), (self.version, "version"), (self.provider, "provider")):
            _required(value, name)
        if not self.supported_platforms:
            raise ValidationError("el plugin debe declarar plataformas")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True)
class Worker:
    id: str
    name: str
    plugin_id: str
    plugin_version: str
    status: WorkerStatus
    configuration: Mapping[str, Any]
    secret_references: tuple[str, ...]
    storage_path: str
    verified_capabilities: frozenset[PluginCapability]
    concurrency_limit: int
    active_executions: int = 0
    last_checked_at: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        for value, name in ((self.id, "id"), (self.name, "name"), (self.plugin_id, "plugin_id"), (self.plugin_version, "plugin_version"), (self.storage_path, "storage_path")):
            _required(value, name)
        if self.concurrency_limit < 1 or not 0 <= self.active_executions <= self.concurrency_limit:
            raise ValidationError("límites de concurrencia inválidos")
        object.__setattr__(self, "configuration", _mapping(self.configuration))

    @property
    def usable(self) -> bool:
        return self.status is WorkerStatus.READY and self.active_executions < self.concurrency_limit


@dataclass(frozen=True)
class Agent:
    id: str
    project_id: str
    name: str
    role: str
    responsibilities: tuple[str, ...]
    required_capabilities: frozenset[PluginCapability]
    restrictions: tuple[str, ...]
    instruction_revision: int
    status: AgentStatus
    pinned_worker_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in ((self.id, "id"), (self.project_id, "project_id"), (self.name, "name"), (self.role, "role")):
            _required(value, name)
        if self.instruction_revision < 1:
            raise ValidationError("instruction_revision debe ser positiva")


@dataclass(frozen=True)
class ProviderSession:
    id: str
    project_id: str
    agent_id: str
    purpose: str
    worker_id: str
    context_hash: str
    status: ProviderSessionStatus
    workspace_id: str | None = None
    provider_reference: str | None = None
    continuity_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in ((self.id, "id"), (self.project_id, "project_id"), (self.agent_id, "agent_id"), (self.purpose, "purpose"), (self.worker_id, "worker_id"), (self.context_hash, "context_hash")):
            _required(value, name)
        object.__setattr__(self, "continuity_metadata", _mapping(self.continuity_metadata))


@dataclass(frozen=True)
class ProviderExecution:
    id: str
    worker_id: str
    session_id: str
    state: ExecutionState
    idempotency_key: str
    outcome: ExecutionOutcome | None = None
    provider_reference: str | None = None
    error_category: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    reconciliation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in ((self.id, "id"), (self.worker_id, "worker_id"), (self.session_id, "session_id"), (self.idempotency_key, "idempotency_key")):
            _required(value, name)
        if (self.state is ExecutionState.FINISHED) != (self.outcome is not None):
            raise ValidationError("solo FINISHED puede tener un resultado terminal")
        object.__setattr__(self, "result", _mapping(self.result))
        object.__setattr__(self, "reconciliation", _mapping(self.reconciliation))

    @property
    def safe_to_retry_as_new(self) -> bool:
        return self.state is ExecutionState.FINISHED and self.outcome is not ExecutionOutcome.UNKNOWN


@dataclass(frozen=True)
class PlanRevision:
    id: str
    project_id: str
    issue_id: str
    revision: int
    status: PlanRevisionStatus
    canonical_hash: str
    payload: Mapping[str, Any]
    created_at: str

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValidationError("revision debe ser positiva")
        _required(self.canonical_hash, "canonical_hash")
        object.__setattr__(self, "payload", _mapping(self.payload))


@dataclass(frozen=True)
class Approval:
    id: str
    plan_revision_id: str
    plan_hash: str
    approver_github_id: int
    remote_event_id: str
    approved_at: str


@dataclass(frozen=True)
class Task:
    id: str
    plan_revision_id: str
    logical_id: str
    description: str
    status: TaskStatus
    assigned_agent_id: str | None = None
    dependencies: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.id in self.dependencies:
            raise ValidationError("una tarea no puede depender de sí misma")
        _required(self.description, "description")


@dataclass(frozen=True)
class Workspace:
    id: str
    project_id: str
    purpose: str
    path: str
    base_branch: str
    base_sha: str
    status: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    task_id: str
    github_pr_id: int
    number: int
    head_ref: str
    base_ref: str
    head_sha: str
    status: str
    merge_commit_sha: str | None = None


@dataclass(frozen=True)
class IssueWorkflow:
    id: str
    project_id: str
    github_issue_id: int
    issue_number: int
    status: str


@dataclass(frozen=True)
class AuthorizedMessage:
    id: str
    issue_id: str
    remote_kind: str
    remote_id: str
    author_github_id: int
    body: str
    created_at: str


@dataclass(frozen=True)
class ReviewEvent:
    id: str
    pull_request_id: str
    remote_kind: str
    remote_id: str
    author_github_id: int
    body: str
    state: str | None = None


@dataclass(frozen=True)
class Correction:
    id: str
    pull_request_id: str
    source_review_id: str
    scope: str
    status: str
    previous_sha: str
    new_sha: str | None = None


@dataclass(frozen=True)
class VerifiedMerge:
    id: str
    pull_request_id: str
    merge_commit_sha: str
    verified_at: str
    method: str | None = None


@dataclass(frozen=True)
class DiscardedRemoteEvent:
    id: str
    project_id: str
    remote_kind: str
    remote_id: str
    author_github_id: int | None
    remote_created_at: str
    discard_reason: str


@dataclass(frozen=True)
class RecoveryBarrier:
    id: str
    reason: str
    active: bool
    created_at: str
    cleared_at: str | None = None


WORKER_TRANSITIONS = {
    WorkerStatus.INSTALLING: frozenset({WorkerStatus.AUTHENTICATION_REQUIRED, WorkerStatus.VERIFYING, WorkerStatus.ERROR}),
    WorkerStatus.AUTHENTICATION_REQUIRED: frozenset({WorkerStatus.VERIFYING, WorkerStatus.DISABLED, WorkerStatus.ERROR}),
    WorkerStatus.VERIFYING: frozenset({WorkerStatus.READY, WorkerStatus.UNAVAILABLE, WorkerStatus.ERROR}),
    WorkerStatus.READY: frozenset({WorkerStatus.UNAVAILABLE, WorkerStatus.DISABLED, WorkerStatus.ERROR}),
    WorkerStatus.UNAVAILABLE: frozenset({WorkerStatus.VERIFYING, WorkerStatus.DISABLED, WorkerStatus.ERROR}),
    WorkerStatus.DISABLED: frozenset({WorkerStatus.VERIFYING}),
    WorkerStatus.ERROR: frozenset({WorkerStatus.VERIFYING, WorkerStatus.DISABLED}),
}


def worker_transition_allowed(current: WorkerStatus, requested: WorkerStatus) -> bool:
    return requested == current or requested in WORKER_TRANSITIONS[current]


@dataclass(frozen=True)
class ExternalOperation:
    id: str
    kind: str
    idempotency_key: str
    payload_hash: str
    status: ExternalOperationStatus
    remote_reference: str | None = None
    reconciliation_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reconciliation_data", _mapping(self.reconciliation_data))

    @property
    def may_repeat_blindly(self) -> bool:
        return self.status is ExternalOperationStatus.FAILED


@dataclass(frozen=True)
class OperationalSnapshot:
    github: GitHubConnectivity
    execution: ExecutionCapacity
    readiness: PublicReadiness
    online: bool
    administratively_paused: bool
    recovery_barrier: bool
    projects_available: bool
    eligible_work: bool
    work_allowed: bool
