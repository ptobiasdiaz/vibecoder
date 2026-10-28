"""Puertos que separan dominio, persistencia y futura ejecución Codex."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .domain import AgentSession, Task


@runtime_checkable
class CodexExecutor(Protocol):
    """Contrato futuro; M01 no realiza inferencia ni autenticación."""

    def ensure_thread(self, session: AgentSession) -> str: ...

    def execute(self, session: AgentSession, task: Task) -> object: ...

    def cancel(self, session: AgentSession, execution_id: str) -> None: ...


@runtime_checkable
class Repository(Protocol):
    """Unidad de persistencia consumida por el servicio de aplicación."""

    def close(self) -> None: ...

    def transaction(self): ...

    def insert(self, table: str, values: dict[str, object]) -> None: ...

    def update(self, table: str, entity_id: str, values: dict[str, object]) -> None: ...

    def get(self, table: str, entity_id: str) -> dict[str, object]: ...

    def find_one(self, table: str, **criteria: object) -> dict[str, object] | None: ...

    def list(self, table: str, **criteria: object) -> list[dict[str, object]]: ...

    def next_attempt_number(self, task_id: str) -> int: ...

