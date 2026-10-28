"""Adaptador de producción para Codex App Server sobre el transporte validado en M00."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from m00_codex.client import AppServerClient, ProtocolError, RpcError
from m00_codex.protocol import ApprovalPolicy, SandboxMode


@dataclass(frozen=True)
class AccountRuntimeConfig:
    account_id: str
    codex_home: Path
    codex_executable: str = "codex"
    cwd: Path = field(default_factory=Path.cwd)
    extra_args: tuple[str, ...] = ()
    timeout_seconds: float = 600.0

    def validate(self) -> None:
        if not self.codex_home.is_absolute():
            raise ValueError("codex_home debe ser absoluto")
        if not self.cwd.is_absolute():
            raise ValueError("cwd debe ser absoluto")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds debe ser positivo")


@dataclass(frozen=True)
class ExecutionResult:
    thread_id: str
    turn_id: str
    status: str
    text: str
    error: Mapping[str, Any] | None = None


class CodexExecutorError(RuntimeError):
    category = "execution"

    def __init__(self, message: str, *, code: str | None = None, details: object = None):
        self.code = code
        self.details = details
        super().__init__(message)


class AuthenticationError(CodexExecutorError):
    category = "authentication"


class QuotaError(CodexExecutorError):
    category = "quota"


class TransportError(CodexExecutorError):
    category = "transport"


class ExecutionTimeout(CodexExecutorError):
    category = "timeout"


class SessionUnavailable(CodexExecutorError):
    category = "session"


def _error_code(error: Mapping[str, Any] | None) -> str | None:
    if not error:
        return None
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return info
    if isinstance(info, Mapping):
        for key in ("type", "code", "kind"):
            if info.get(key):
                return str(info[key])
        if len(info) == 1:
            return str(next(iter(info)))
    return None


def classify_error(error: Mapping[str, Any] | None) -> type[CodexExecutorError]:
    """Clasifica solo códigos estructurados oficiales; nunca texto libre."""
    code = _error_code(error)
    if code == "Unauthorized":
        return AuthenticationError
    if code == "UsageLimitExceeded":
        return QuotaError
    if code in {
        "HttpConnectionFailed",
        "ResponseStreamConnectionFailed",
        "ResponseStreamDisconnected",
        "ResponseTooManyFailedAttempts",
    }:
        return TransportError
    return CodexExecutorError


class CodexAppServerExecutor:
    def __init__(self, config: AccountRuntimeConfig):
        config.validate()
        self.config = config
        self.client: AppServerClient | None = None

    async def start(self) -> None:
        self.client = AppServerClient(
            self.config.codex_executable,
            codex_home=self.config.codex_home,
            cwd=self.config.cwd,
            extra_args=self.config.extra_args,
            request_timeout=min(self.config.timeout_seconds, 30.0),
        )
        try:
            await self.client.start()
        except RpcError as exc:
            self.client = None
            error = exc.error if isinstance(exc.error, Mapping) else {}
            kind = classify_error(error)
            raise kind(str(exc), code=_error_code(error), details=error) from exc
        except (OSError, ProtocolError) as exc:
            self.client = None
            raise TransportError(str(exc)) from exc

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await client.close()

    async def ensure_thread(self, thread_id: str | None) -> tuple[str, bool]:
        client = self._client()
        try:
            if thread_id:
                await client.resume_thread(thread_id)
                return thread_id, False
            result = await client.start_thread(
                cwd=str(self.config.cwd),
                approvalPolicy=ApprovalPolicy.NEVER.value,
                sandbox=SandboxMode.WORKSPACE_WRITE.value,
            )
            return str(result["thread"]["id"]), True
        except RpcError as exc:
            if thread_id and (exc.error.get("code") == -32600 or exc.error.get("code") == -32602):
                raise SessionUnavailable(str(exc), code=str(exc.error.get("code"))) from exc
            kind = classify_error(exc.error)
            raise kind(str(exc), code=_error_code(exc.error), details=exc.error) from exc

    async def start_turn(self, thread_id: str, prompt: str) -> str:
        try:
            result = await self._client().start_turn(thread_id, prompt)
            return str(result["turn"]["id"])
        except RpcError as exc:
            kind = classify_error(exc.error)
            raise kind(str(exc), code=_error_code(exc.error), details=exc.error) from exc

    async def wait_turn(self, thread_id: str, turn_id: str) -> ExecutionResult:
        try:
            result = await self._client().wait_turn(
                thread_id, turn_id, timeout=self.config.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise ExecutionTimeout("timeout esperando turn/completed", code="TIMEOUT") from exc
        except ProtocolError as exc:
            raise TransportError(str(exc)) from exc
        error = result.turn.get("error") if isinstance(result.turn, Mapping) else None
        return ExecutionResult(
            thread_id=thread_id,
            turn_id=turn_id,
            status=result.status,
            text=result.text,
            error=error if isinstance(error, Mapping) else None,
        )

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        await self._client().interrupt_turn(thread_id, turn_id)

    async def reconcile_turn(self, thread_id: str, turn_id: str) -> ExecutionResult | None:
        result = await self._client().list_turns(thread_id)
        for turn in result.get("data", []):
            if turn.get("id") == turn_id:
                status = str(turn.get("status", "unknown"))
                error = turn.get("error")
                return ExecutionResult(
                    thread_id, turn_id, status, "", error if isinstance(error, Mapping) else None
                )
        return None

    def _client(self) -> AppServerClient:
        if self.client is None:
            raise TransportError("App Server no está iniciado")
        return self.client
