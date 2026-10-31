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


class SpecialistStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class AgentRevisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class IssueWorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    MATERIALIZED = "MATERIALIZED"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"


class IssueCommentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RESPONSE_PENDING = "RESPONSE_PENDING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class PlanRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    BLOCKED = "BLOCKED"


class TaskExecutionStatus(StrEnum):
    READY = "READY"
    WAITING_DEPENDENCIES = "WAITING_DEPENDENCIES"
    PREPARING = "PREPARING"
    WORKSPACE_READY = "WORKSPACE_READY"
    RUNNING = "RUNNING"
    GENERATED = "GENERATED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    COMMITTED = "COMMITTED"
    PUSHING = "PUSHING"
    BRANCH_PUBLISHED = "BRANCH_PUBLISHED"
    CREATING_PR = "CREATING_PR"
    PR_OPEN = "PR_OPEN"
    MERGED = "MERGED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class PullRequestSupervisionStatus(StrEnum):
    MONITORING = "MONITORING"
    REVIEW_PENDING = "REVIEW_PENDING"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    CORRECTING = "CORRECTING"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    APPROVED = "APPROVED"
    MERGED = "MERGED"
    CLOSED_UNMERGED = "CLOSED_UNMERGED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class CorrectionScope(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class CorrectionStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    COMMITTED = "COMMITTED"
    PUSHING = "PUSHING"
    PUBLISHED = "PUBLISHED"
    REPLIED = "REPLIED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class SupervisorJobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class WorkCategory(StrEnum):
    REMOTE_READ = "REMOTE_READ"
    CODEX = "CODEX"
    LOCAL_WRITE = "LOCAL_WRITE"
    REMOTE_WRITE = "REMOTE_WRITE"
    RECOVERY = "RECOVERY"


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


class BootstrapStatus(StrEnum):
    ACTIVE = "ACTIVE"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class BootstrapCommentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RESPONSE_PENDING = "RESPONSE_PENDING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class GenerationStatus(StrEnum):
    PENDING = "PENDING"
    PREPARING_WORKSPACE = "PREPARING_WORKSPACE"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class GitConfigurationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"


class GitInitializationStrategy(StrEnum):
    EXISTING_BASE = "EXISTING_BASE"
    TECHNICAL_BASE_COMMIT = "TECHNICAL_BASE_COMMIT"


class TechnicalCommitPolicy(StrEnum):
    NONE = "NONE"
    CREATE_MINIMAL = "CREATE_MINIMAL"


class GitPublicationPolicy(StrEnum):
    PULL_REQUEST = "PULL_REQUEST"


class GitIntegrationPolicy(StrEnum):
    HUMAN_REVIEW_MERGE = "HUMAN_REVIEW_MERGE"


class PublicationStatus(StrEnum):
    READY = "READY"
    INITIALIZING_BASE = "INITIALIZING_BASE"
    BASE_READY = "BASE_READY"
    PUSHING_BRANCH = "PUSHING_BRANCH"
    BRANCH_PUBLISHED = "BRANCH_PUBLISHED"
    CREATING_PR = "CREATING_PR"
    WAITING_FOR_MERGE = "WAITING_FOR_MERGE"
    MERGED = "MERGED"
    ACTIVATED = "ACTIVATED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


SPECIALIST_TRANSITIONS: Mapping[SpecialistStatus, frozenset[SpecialistStatus]] = {
    SpecialistStatus.DRAFT: frozenset({SpecialistStatus.PROPOSED, SpecialistStatus.RETIRED}),
    SpecialistStatus.PROPOSED: frozenset({SpecialistStatus.ACTIVE, SpecialistStatus.RETIRED}),
    SpecialistStatus.ACTIVE: frozenset({SpecialistStatus.SUSPENDED, SpecialistStatus.RETIRED}),
    SpecialistStatus.SUSPENDED: frozenset({SpecialistStatus.ACTIVE, SpecialistStatus.RETIRED}),
    SpecialistStatus.RETIRED: frozenset(),
}


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


PROJECT_TRANSITIONS: Mapping[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.UNINITIALIZED: frozenset(
        {ProjectStatus.BOOTSTRAPPING, ProjectStatus.DISABLED}
    ),
    ProjectStatus.BOOTSTRAPPING: frozenset(
        {ProjectStatus.DESIGNING, ProjectStatus.DISABLED}
    ),
    ProjectStatus.DESIGNING: frozenset({ProjectStatus.READY, ProjectStatus.DISABLED}),
    ProjectStatus.READY: frozenset({ProjectStatus.ACTIVE, ProjectStatus.DISABLED}),
    ProjectStatus.ACTIVE: frozenset({ProjectStatus.DISABLED}),
    ProjectStatus.DISABLED: frozenset(),
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


@dataclass(frozen=True)
class GitHubRepository:
    id: str
    project_id: str
    github_id: int
    node_id: str
    owner_login: str
    name: str
    full_name: str
    html_url: str
    api_url: str
    default_branch: str | None
    visibility: str
    archived: bool
    is_empty: bool
    permissions: Mapping[str, bool]
    last_synced_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitHubOperation:
    id: str
    repository_id: str
    operation_type: str
    idempotency_key: str
    payload_hash: str
    status: str
    remote_id: str | None
    remote_url: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BootstrapWorkflow:
    id: str
    project_id: str
    repository_id: str
    github_issue_id: int
    github_issue_node_id: str
    issue_number: int
    issue_url: str
    principal_agent_id: str
    status: BootstrapStatus
    approval_label: str
    approved_by: str | None
    approved_event_id: int | None
    approved_at: str | None
    frozen_specification: str | None
    last_polled_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BootstrapComment:
    id: str
    workflow_id: str
    github_comment_id: int
    github_node_id: str
    author_login: str
    author_type: str
    body: str
    remote_created_at: str
    status: BootstrapCommentStatus
    rejection_reason: str | None
    task_id: str | None
    response_text: str | None
    response_github_comment_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GenerationWorkflow:
    id: str
    project_id: str
    bootstrap_workflow_id: str
    repository_id: str
    approved_by: str
    approved_event_id: int
    specification_hash: str
    workspace_path: str
    remote_url: str
    base_branch: str | None
    base_commit: str | None
    bootstrap_branch: str
    git_configuration_id: str | None
    git_configuration_hash: str | None
    task_id: str | None
    session_id: str | None
    status: GenerationStatus
    instruction_versions: Mapping[str, str]
    generated_instruction_versions: Mapping[str, str]
    validation_result: Mapping[str, Any] | None
    commit_hash: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitConfiguration:
    id: str
    workflow_id: str
    repository_id: str
    revision: int
    status: GitConfigurationStatus
    schema_version: int
    main_branch: str
    base_branch_exists: bool
    initialization_strategy: GitInitializationStrategy
    technical_commit_policy: TechnicalCommitPolicy
    publication_policy: GitPublicationPolicy
    integration_policy: GitIntegrationPolicy
    bootstrap_branch: str
    authorized_operations: tuple[str, ...]
    configuration_hash: str
    source_comment_id: int | None
    observed_github_id: int
    observed_is_empty: bool
    observed_default_branch: str | None
    observed_head_sha: str | None
    specification_hash: str | None
    approved_by: str | None
    approved_event_id: int | None
    applied_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PublicationWorkflow:
    id: str
    project_id: str
    bootstrap_workflow_id: str
    git_configuration_id: str
    generation_id: str
    repository_id: str
    status: PublicationStatus
    base_branch: str
    base_sha: str | None
    published_branch: str
    published_sha: str
    pull_request_id: int | None
    pull_request_number: int | None
    pull_request_url: str | None
    merge_commit_sha: str | None
    remote_state_json: str | None
    blocked_reason: str | None
    activated_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SpecialistAgent:
    agent_id: str
    project_id: str
    logical_id: str
    display_name: str
    status: SpecialistStatus
    assigned_account_id: str
    active_revision_id: str | None
    created_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentConfigurationRevision:
    id: str
    agent_id: str
    version: int
    status: AgentRevisionStatus
    role: str
    responsibilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    restrictions: tuple[str, ...]
    instructions_path: str
    instructions_hash: str
    configuration_hash: str
    source_commit: str | None
    created_by: str
    authorized_by: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentAuditEvent:
    id: str
    agent_id: str
    project_id: str
    operation: str
    actor: str
    previous_status: str | None
    new_status: str | None
    previous_version: int | None
    new_version: int | None
    result: str
    detail: str | None
    created_at: str


@dataclass(frozen=True)
class IssueWorkflow:
    id: str
    project_id: str
    repository_id: str
    github_issue_id: int
    github_issue_node_id: str
    issue_number: int
    issue_url: str
    title: str
    body: str
    author_login: str
    github_state: str
    labels: tuple[str, ...]
    principal_agent_id: str
    status: IssueWorkflowStatus
    approval_label: str
    active_plan_revision_id: str | None
    last_polled_at: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlanRevision:
    id: str
    workflow_id: str
    revision: int
    status: PlanRevisionStatus
    plan_hash: str
    payload: Mapping[str, Any]
    proposed_by_agent_id: str
    published_comment_id: int | None
    published_at: str | None
    approved_by: str | None
    approved_event_id: int | None
    approved_at: str | None
    specialist_snapshot: Mapping[str, Any] | None
    frozen_context: Mapping[str, Any] | None
    blocked_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskExecutionWorkflow:
    id: str
    project_id: str
    issue_workflow_id: str
    plan_revision_id: str
    planned_task_id: str
    task_id: str
    repository_id: str
    agent_id: str
    agent_revision_id: str
    agent_configuration_hash: str
    instructions_hash: str
    account_id: str
    status: TaskExecutionStatus
    workspace_path: str
    remote_url: str
    base_branch: str
    base_sha: str
    work_branch: str
    session_id: str | None
    instruction_versions: Mapping[str, str]
    authorization_snapshot: Mapping[str, Any]
    context_hash: str | None
    allowed_paths: tuple[str, ...]
    validation_commands: tuple[tuple[str, ...], ...]
    validation_authorized_by: str | None
    result_text: str | None
    validation_result: Mapping[str, Any] | None
    commit_sha: str | None
    published_sha: str | None
    pull_request_id: int | None
    pull_request_number: int | None
    pull_request_url: str | None
    merge_commit_sha: str | None
    remote_state: Mapping[str, Any] | None
    blocked_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PullRequestSupervision:
    id: str
    execution_id: str
    project_id: str
    repository_id: str
    pull_request_number: int
    status: PullRequestSupervisionStatus
    review_state: str
    observed_head_sha: str
    merge_commit_sha: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SupervisorJob:
    id: str
    project_id: str | None
    job_type: str
    category: WorkCategory
    source_type: str
    source_id: str
    state: SupervisorJobState
    priority: int
    eligible_at: str
    attempts: int
    max_attempts: int
    operation_key: str
    payload: Mapping[str, Any]
    last_error_category: str | None
    last_error_message: str | None
    next_action: str | None
    runner_id: str | None
    lease_token: str | None
    lease_expires_at: str | None
    created_at: str
    updated_at: str

