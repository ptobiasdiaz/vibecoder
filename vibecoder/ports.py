"""Provider-neutral plugin ports; no GitHub secret or database is exposed."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .domain import AuthenticationMethod, ExecutionOutcome, PluginDescriptor, ProviderSession, Worker


class CapabilityError(RuntimeError):
    def __init__(self, capability: str):
        self.capability = capability
        super().__init__(f"capacidad no soportada: {capability}")


@dataclass(frozen=True)
class InstallationPlan:
    plugin_id: str
    version: str
    platform: str
    source: str
    expected_digest: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthenticationChallenge:
    method: AuthenticationMethod
    opaque_reference: str
    verification_uri: str | None = None
    user_code: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class SessionRequest:
    project_id: str
    agent_id: str
    worker_id: str
    purpose: str
    context_hash: str
    workspace: Path | None = None
    correlation_id: str = ""


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    worker_id: str
    session_id: str
    idempotency_key: str
    agent_context: Mapping[str, Any]
    instructions: str
    workspace: Path | None = None
    limits: Mapping[str, Any] = field(default_factory=dict)
    expected_output_schema: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionResult:
    outcome: ExecutionOutcome
    provider_execution_reference: str | None = None
    provider_session_reference: str | None = None
    structured_result: Mapping[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    error_message: str | None = None
    reconciliation: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PluginInstallationManager(Protocol):
    def installation_status(self) -> Mapping[str, Any]: ...
    def plan_installation(self, *, platform: str) -> InstallationPlan: ...
    def install(self, plan: InstallationPlan) -> Mapping[str, Any]: ...
    def verify_installation(self) -> PluginDescriptor: ...


@runtime_checkable
class WorkerAuthenticator(Protocol):
    def begin_authentication(self, worker: Worker, method: AuthenticationMethod) -> AuthenticationChallenge: ...
    def continue_authentication(self, challenge: AuthenticationChallenge) -> Mapping[str, Any]: ...
    def verify_authentication(self, worker: Worker) -> bool: ...


@runtime_checkable
class WorkerProbe(Protocol):
    async def probe_worker(self, worker: Worker) -> Mapping[str, Any]: ...


@runtime_checkable
class WorkerRuntime(Protocol):
    async def create_session(self, request: SessionRequest) -> ProviderSession: ...
    async def continue_session(self, session: ProviderSession) -> ProviderSession: ...
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


@runtime_checkable
class ExecutionObserver(Protocol):
    async def observe_execution(self, provider_reference: str) -> ExecutionResult: ...


@runtime_checkable
class ExecutionCanceller(Protocol):
    async def cancel_execution(self, provider_reference: str) -> ExecutionResult: ...


@runtime_checkable
class Repository(Protocol):
    def close(self) -> None: ...
    def transaction(self): ...
    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None: ...
    def query_one(self, sql: str, parameters: Sequence[object] = ()) -> Mapping[str, Any] | None: ...
    def query_all(self, sql: str, parameters: Sequence[object] = ()) -> list[Mapping[str, Any]]: ...
