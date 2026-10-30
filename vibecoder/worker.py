"""Worker local y secuencial de tareas Codex."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .codex_executor import (
    AccountRuntimeConfig,
    AuthenticationError,
    CodexAppServerExecutor,
    CodexExecutorError,
    ExecutionResult,
    ExecutionTimeout,
    QuotaError,
    SessionUnavailable,
    TransportError,
    classify_error,
)
from .domain import AgentStatus, AccountStatus, AttemptStatus, SessionPersistenceState, Task, TaskStatus
from .errors import ConflictError, ValidationError
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository


class Executor(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def ensure_thread(self, thread_id: str | None) -> tuple[str, bool]: ...
    async def start_turn(self, thread_id: str, prompt: str) -> str: ...
    async def wait_turn(self, thread_id: str, turn_id: str) -> ExecutionResult: ...
    async def reconcile_turn(self, thread_id: str, turn_id: str) -> ExecutionResult | None: ...


@dataclass(frozen=True)
class WorkerOutcome:
    task_id: str | None
    attempt_id: str | None
    status: str
    detail: str


class LocalWorker:
    def __init__(
        self,
        core: OrchestratorCore,
        account_configs: dict[str, AccountRuntimeConfig],
        *,
        executor_factory: Callable[[AccountRuntimeConfig], Executor] = CodexAppServerExecutor,
    ):
        self.core = core
        self.account_configs = account_configs
        self.executor_factory = executor_factory

    def select_task(self) -> Task | None:
        tasks = [
            task for task in self.core.list_pending_tasks()
            if task.status is TaskStatus.PENDING
            and self.core.get_agent(task.agent_id).status is AgentStatus.ACTIVE
            and not self.core.is_managed_specialist(task.agent_id)
        ]
        return tasks[0] if tasks else None

    async def run_once(self) -> WorkerOutcome:
        task = self.select_task()
        if task is None:
            return WorkerOutcome(None, None, "IDLE", "no hay tareas PENDING")
        return await self._run_task(task)

    async def run_task(
        self,
        task_id: str,
        *,
        session_id: str | None = None,
        runtime_config: AccountRuntimeConfig | None = None,
    ) -> WorkerOutcome:
        """Ejecuta una tarea concreta sin competir con otras colas de aplicación."""
        task = self.core.get_task(task_id)
        if task.status is not TaskStatus.PENDING:
            raise ConflictError("la ejecución dirigida requiere una tarea PENDING")
        return await self._run_task(task, session_id=session_id, runtime_config=runtime_config)

    async def _run_task(
        self,
        task: Task,
        *,
        session_id: str | None = None,
        runtime_config: AccountRuntimeConfig | None = None,
    ) -> WorkerOutcome:
        agent = self.core.get_agent(task.agent_id)
        if agent.status is not AgentStatus.ACTIVE:
            raise ConflictError("el agente no está activo")
        if self.core.is_managed_specialist(agent.id):
            raise ConflictError("la ejecución de especialistas queda fuera de M04.1")
        account = self.core.get_account(self.core.agent_account_id(agent.id))
        config = runtime_config or self.account_configs.get(account.id)
        if not task.prompt:
            raise ValidationError(f"la tarea {task.id} no tiene prompt")
        if config is None or config.account_id != account.id:
            raise ValidationError(f"falta configuración runtime para la cuenta {account.id}")

        attempt = self.core.start_attempt(task.id)
        session = (
            self.core.get_session(session_id)
            if session_id is not None
            else self.core.get_or_create_session(agent.id)
        )
        if session.agent_id != agent.id:
            self.core.finish_attempt(
                attempt.id,
                AttemptStatus.FAILED,
                error_code="SESSION_AGENT_MISMATCH",
                error_category="configuration",
                error_message="la sesión pertenece a otro agente",
            )
            raise ConflictError("la sesión pertenece a otro agente")
        if session.codex_account_id != account.id:
            self.core.finish_attempt(
                attempt.id,
                AttemptStatus.FAILED,
                error_code="SESSION_ACCOUNT_MISMATCH",
                error_category="configuration",
                error_message="la sesión pertenece a otra cuenta",
            )
            raise ConflictError("la sesión pertenece a otra cuenta")
        self.core.bind_attempt_session(attempt.id, session.id)
        if session.codex_thread_id:
            self.core.bind_attempt_thread(attempt.id, session.codex_thread_id)

        executor = self.executor_factory(config)
        try:
            await executor.start()
            thread_id, created = await executor.ensure_thread(session.codex_thread_id)
            if created:
                session = self.core.transition_session(
                    session.id,
                    SessionPersistenceState.THREAD_CREATED_UNCONFIRMED,
                    codex_thread_id=thread_id,
                )
            self.core.bind_attempt_thread(attempt.id, thread_id)
            turn_id = await executor.start_turn(thread_id, task.prompt)
            self.core.bind_attempt_turn(attempt.id, turn_id)
            result = await executor.wait_turn(thread_id, turn_id)
            return self._finish_result(task, attempt.id, session.id, result)
        except AuthenticationError as exc:
            self.core.set_account_status(account.id, AccountStatus.AUTH_REQUIRED)
            return self._finish_error(task, attempt.id, exc, TaskStatus.WAITING_AUTH)
        except QuotaError as exc:
            self.core.set_account_status(account.id, AccountStatus.QUOTA_EXHAUSTED)
            return self._finish_error(task, attempt.id, exc, TaskStatus.WAITING_QUOTA)
        except ExecutionTimeout as exc:
            return self._finish_unknown(task, attempt.id, exc)
        except SessionUnavailable as exc:
            current = self.core.get_session(session.id)
            if current.persistence_state is not SessionPersistenceState.LOST:
                self.core.transition_session(session.id, SessionPersistenceState.LOST)
            return self._finish_unknown(task, attempt.id, exc)
        except TransportError as exc:
            return self._finish_error(task, attempt.id, exc, TaskStatus.FAILED)
        except CodexExecutorError as exc:
            return self._finish_error(task, attempt.id, exc, TaskStatus.FAILED)
        except Exception as exc:
            wrapped = CodexExecutorError(str(exc), code=type(exc).__name__)
            return self._finish_error(task, attempt.id, wrapped, TaskStatus.FAILED)
        finally:
            await executor.close()

    async def reconcile_attempt(self, attempt_id: str) -> WorkerOutcome:
        attempt = self.core.get_attempt(attempt_id)
        if attempt.status is not AttemptStatus.UNKNOWN:
            raise ConflictError("solo se reconcilian intentos UNKNOWN")
        if not attempt.codex_thread_id or not attempt.codex_turn_id:
            return WorkerOutcome(attempt.task_id, attempt.id, TaskStatus.WAITING, "faltan IDs Codex")
        task = self.core.get_task(attempt.task_id)
        account_id = self.core.agent_account_id(task.agent_id)
        config = self.account_configs.get(account_id)
        if config is None:
            raise ValidationError(f"falta configuración runtime para la cuenta {account_id}")
        executor = self.executor_factory(config)
        try:
            await executor.start()
            result = await executor.reconcile_turn(
                attempt.codex_thread_id, attempt.codex_turn_id
            )
            if result is None or result.status not in {"completed", "failed", "interrupted"}:
                return WorkerOutcome(task.id, attempt.id, TaskStatus.WAITING, "resultado no concluyente")
            outcome = {
                "completed": AttemptStatus.SUCCEEDED,
                "failed": AttemptStatus.FAILED,
                "interrupted": AttemptStatus.CANCELLED,
            }[result.status]
            error = result.error or {}
            self.core.resolve_unknown_attempt(
                attempt.id,
                outcome,
                result_text=result.text,
                error_code=_structured_code(error),
                error_category=classify_error(error).category if error else None,
                error_message=str(error.get("message")) if error.get("message") else None,
            )
            status = self.core.get_task(task.id).status
            return WorkerOutcome(task.id, attempt.id, status, "resultado reconciliado")
        finally:
            await executor.close()

    def _finish_result(
        self, task: Task, attempt_id: str, session_id: str, result: ExecutionResult
    ) -> WorkerOutcome:
        if result.status == "completed":
            session = self.core.get_session(session_id)
            if session.persistence_state is SessionPersistenceState.THREAD_CREATED_UNCONFIRMED:
                self.core.transition_session(session_id, SessionPersistenceState.THREAD_PERSISTED)
                self.core.transition_session(session_id, SessionPersistenceState.RECOVERABLE)
            self.core.finish_attempt(attempt_id, AttemptStatus.SUCCEEDED, result_text=result.text)
            return WorkerOutcome(task.id, attempt_id, TaskStatus.COMPLETED, "turno completado")
        if result.status == "interrupted":
            self.core.finish_attempt(attempt_id, AttemptStatus.CANCELLED, result_text=result.text)
            return WorkerOutcome(task.id, attempt_id, TaskStatus.CANCELLED, "cancelación confirmada")
        if result.status == "failed":
            kind = classify_error(result.error)
            error = kind(
                str((result.error or {}).get("message", "turno fallido")),
                code=_structured_code(result.error),
                details=result.error,
            )
            if isinstance(error, AuthenticationError):
                account_id = self.core.get_project(task.project_id).codex_account_id
                self.core.set_account_status(account_id, AccountStatus.AUTH_REQUIRED)
                return self._finish_error(task, attempt_id, error, TaskStatus.WAITING_AUTH)
            if isinstance(error, QuotaError):
                account_id = self.core.get_project(task.project_id).codex_account_id
                self.core.set_account_status(account_id, AccountStatus.QUOTA_EXHAUSTED)
                return self._finish_error(task, attempt_id, error, TaskStatus.WAITING_QUOTA)
            return self._finish_error(task, attempt_id, error, TaskStatus.FAILED)
        return self._finish_unknown(
            task, attempt_id, CodexExecutorError("estado final desconocido", code=result.status)
        )

    def _finish_error(
        self, task: Task, attempt_id: str, error: CodexExecutorError, status: TaskStatus
    ) -> WorkerOutcome:
        self.core.finish_attempt(
            attempt_id,
            AttemptStatus.FAILED,
            task_status=status,
            error_code=error.code,
            error_category=error.category,
            error_message=str(error),
            error_details=error.details if isinstance(error.details, dict) else None,
        )
        return WorkerOutcome(task.id, attempt_id, status, str(error))

    def _finish_unknown(
        self, task: Task, attempt_id: str, error: CodexExecutorError
    ) -> WorkerOutcome:
        self.core.finish_attempt(
            attempt_id,
            AttemptStatus.UNKNOWN,
            error_code=error.code,
            error_category=error.category,
            error_message=str(error),
        )
        return WorkerOutcome(task.id, attempt_id, TaskStatus.WAITING, str(error))


def _structured_code(error: object) -> str | None:
    if not isinstance(error, dict):
        return None
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return info
    if isinstance(info, dict):
        for key in ("type", "code", "kind"):
            if info.get(key):
                return str(info[key])
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="ruta a SQLite")
    parser.add_argument("--accounts", help="JSON de runtimes por cuenta")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--recover", action="store_true")
    mode.add_argument("--reconcile", metavar="ATTEMPT_ID")
    return parser


def main() -> int:
    args = _parser().parse_args()
    with SQLiteRepository(Path(args.database).resolve()) as repository:
        core = OrchestratorCore(repository)
        if args.status:
            payload = {
                "pending": [asdict(task) for task in core.list_pending_tasks()],
                "running_attempts": [
                    asdict(item) for item in core.list_attempts_by_status(AttemptStatus.RUNNING)
                ],
                "unknown_attempts": [
                    asdict(item) for item in core.list_attempts_by_status(AttemptStatus.UNKNOWN)
                ],
            }
            print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
            return 0
        if args.recover:
            recovered = core.recover_interrupted_tasks()
            print(f"tareas recuperadas conservadoramente: {len(recovered)}")
            return 0
        if not args.accounts:
            raise SystemExit("--accounts es obligatorio con --once/--reconcile")
        worker = LocalWorker(core, load_account_configs(args.accounts))
        operation = worker.reconcile_attempt(args.reconcile) if args.reconcile else worker.run_once()
        outcome = asyncio.run(operation)
        print(json.dumps(asdict(outcome), default=str, ensure_ascii=False))
        return 0 if outcome.status in {"IDLE", TaskStatus.COMPLETED} else 1


if __name__ == "__main__":
    raise SystemExit(main())
