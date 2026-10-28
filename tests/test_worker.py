from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vibecoder import OrchestratorCore, SQLiteRepository
from vibecoder.codex_executor import (
    AccountRuntimeConfig,
    AuthenticationError,
    CodexExecutorError,
    ExecutionResult,
    ExecutionTimeout,
    QuotaError,
    SessionUnavailable,
    TransportError,
    classify_error,
)
from vibecoder.domain import AccountStatus, AttemptStatus, SessionPersistenceState, TaskStatus
from vibecoder.worker import LocalWorker


class FakeExecutor:
    def __init__(self, config: AccountRuntimeConfig, scenario: dict[str, object]):
        self.config = config
        self.scenario = scenario
        self.closed = False
        self.received_thread: str | None = None

    async def start(self) -> None:
        if error := self.scenario.get("start_error"):
            raise error

    async def close(self) -> None:
        self.closed = True

    async def ensure_thread(self, thread_id: str | None) -> tuple[str, bool]:
        self.received_thread = thread_id
        if error := self.scenario.get("thread_error"):
            raise error
        return (thread_id or str(self.scenario.get("thread_id", "thr-new")), thread_id is None)

    async def start_turn(self, thread_id: str, prompt: str) -> str:
        if error := self.scenario.get("turn_error"):
            raise error
        return str(self.scenario.get("turn_id", "turn-1"))

    async def wait_turn(self, thread_id: str, turn_id: str) -> ExecutionResult:
        if error := self.scenario.get("wait_error"):
            raise error
        return self.scenario.get("result") or ExecutionResult(
            thread_id, turn_id, "completed", "done"
        )

    async def reconcile_turn(self, thread_id: str, turn_id: str) -> ExecutionResult | None:
        return self.scenario.get("reconcile_result")


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteRepository(Path(self.temp.name) / "worker.sqlite3")
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("account", "CHATGPT", "codex-home://a")
        self.project = self.core.register_project("project", self.account.id)
        self.agent = self.core.register_agent(
            self.project.id,
            "main",
            "PRINCIPAL",
            config_ref="agents#main",
            documentation_ref="README.md",
        )
        self.config = AccountRuntimeConfig(
            account_id=self.account.id,
            codex_home=Path(self.temp.name).resolve(),
            cwd=Path.cwd().resolve(),
        )
        self.executors: list[FakeExecutor] = []

    async def asyncTearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def task(self, prompt: str = "reply"):
        return self.core.create_task(
            self.project.id,
            self.agent.id,
            "CODEX",
            prompt=prompt,
            idempotency_key=f"task-{prompt}",
        )

    def worker(self, scenario: dict[str, object] | None = None) -> LocalWorker:
        scenario = scenario or {}

        def factory(config: AccountRuntimeConfig) -> FakeExecutor:
            executor = FakeExecutor(config, scenario)
            self.executors.append(executor)
            return executor

        return LocalWorker(self.core, {self.account.id: self.config}, executor_factory=factory)

    async def test_selects_oldest_pending_task_and_completes_attempt(self) -> None:
        task = self.task()
        outcome = await self.worker().run_once()
        self.assertEqual(outcome.task_id, task.id)
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.COMPLETED)
        attempt = self.core.list_attempts(task.id)[0]
        self.assertEqual(attempt.status, AttemptStatus.SUCCEEDED)
        self.assertEqual(attempt.codex_thread_id, "thr-new")
        self.assertEqual(attempt.codex_turn_id, "turn-1")
        self.assertEqual(attempt.result_text, "done")

    async def test_creates_persistent_session_then_resumes_it(self) -> None:
        self.task("first")
        await self.worker().run_once()
        session = self.core.get_or_create_session(self.agent.id)
        self.assertEqual(session.persistence_state, SessionPersistenceState.RECOVERABLE)
        self.task("second")
        second_worker = self.worker({"turn_id": "turn-2"})
        await second_worker.run_once()
        self.assertEqual(self.executors[-1].received_thread, session.codex_thread_id)

    async def test_authentication_error_waits_and_marks_account(self) -> None:
        task = self.task()
        outcome = await self.worker(
            {"start_error": AuthenticationError("unauthorized", code="Unauthorized")}
        ).run_once()
        self.assertEqual(outcome.status, TaskStatus.WAITING_AUTH)
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.WAITING_AUTH)
        self.assertEqual(self.core.get_account(self.account.id).status, AccountStatus.AUTH_REQUIRED)

    async def test_quota_error_waits_for_quota(self) -> None:
        task = self.task()
        await self.worker(
            {"wait_error": QuotaError("limit", code="UsageLimitExceeded")}
        ).run_once()
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.WAITING_QUOTA)

    async def test_transport_error_is_definitive_failure(self) -> None:
        task = self.task()
        await self.worker({"start_error": TransportError("cannot start")}).run_once()
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.FAILED)

    async def test_timeout_is_unknown_and_not_retried(self) -> None:
        task = self.task()
        await self.worker({"wait_error": ExecutionTimeout("late", code="TIMEOUT")}).run_once()
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.WAITING)
        self.assertEqual(self.core.list_attempts(task.id)[0].status, AttemptStatus.UNKNOWN)
        self.assertIsNone(self.worker().select_task())

    async def test_confirmed_cancellation(self) -> None:
        task = self.task()
        result = ExecutionResult("thr-new", "turn-1", "interrupted", "partial")
        await self.worker({"result": result}).run_once()
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.CANCELLED)
        self.assertEqual(self.core.list_attempts(task.id)[0].status, AttemptStatus.CANCELLED)

    async def test_structured_codes_only_classify_auth_and_quota(self) -> None:
        self.assertIs(classify_error({"message": "quota auth unauthorized"}), CodexExecutorError)
        self.assertIs(
            classify_error({"codexErrorInfo": "Unauthorized"}), AuthenticationError
        )
        self.assertIs(classify_error({"codexErrorInfo": "UsageLimitExceeded"}), QuotaError)

    async def test_missing_thread_is_marked_lost_without_replacement(self) -> None:
        session = self.core.create_session(self.agent.id)
        self.core.transition_session(
            session.id,
            SessionPersistenceState.THREAD_CREATED_UNCONFIRMED,
            codex_thread_id="empty-thread",
        )
        self.task()
        await self.worker({"thread_error": SessionUnavailable("missing")}).run_once()
        self.assertEqual(
            self.core.get_session(session.id).persistence_state, SessionPersistenceState.LOST
        )
        self.assertEqual(
            self.core.list_attempts_by_status(AttemptStatus.UNKNOWN)[0].codex_thread_id,
            "empty-thread",
        )

    async def test_executor_is_closed_on_success_and_failure(self) -> None:
        self.task()
        await self.worker().run_once()
        self.assertTrue(self.executors[-1].closed)
        failed = self.core.create_task(
            self.project.id, self.agent.id, "CODEX", prompt="failure", idempotency_key="failure"
        )
        await self.worker({"start_error": TransportError("down")}).run_once()
        self.assertTrue(self.executors[-1].closed)
        self.assertEqual(self.core.get_task(failed.id).status, TaskStatus.FAILED)

    async def test_second_run_does_not_duplicate_completed_task(self) -> None:
        self.task()
        worker = self.worker()
        await worker.run_once()
        outcome = await worker.run_once()
        self.assertEqual(outcome.status, "IDLE")

    async def test_recovery_after_restart_is_conservative(self) -> None:
        task = self.task()
        attempt = self.core.start_attempt(task.id)
        recovered = self.core.recover_interrupted_tasks()
        self.assertEqual([item.id for item in recovered], [task.id])
        self.assertEqual(self.core.get_attempt(attempt.id).status, AttemptStatus.UNKNOWN)
        self.assertIsNone(self.worker().select_task())

    async def test_unknown_attempt_can_be_reconciled_without_resending_prompt(self) -> None:
        task = self.task()
        timed_out = self.worker({"wait_error": ExecutionTimeout("late")})
        outcome = await timed_out.run_once()
        result = ExecutionResult("thr-new", "turn-1", "completed", "recovered")
        reconciled = await self.worker({"reconcile_result": result}).reconcile_attempt(
            outcome.attempt_id
        )
        self.assertEqual(reconciled.status, TaskStatus.COMPLETED)
        attempt = self.core.get_attempt(outcome.attempt_id)
        self.assertEqual(attempt.status, AttemptStatus.SUCCEEDED)
        self.assertEqual(attempt.result_text, "recovered")

    async def test_account_and_project_isolation(self) -> None:
        self.task("mine")
        other_account = self.core.register_account("other", "API_KEY", "codex-home://b")
        other_project = self.core.register_project("other-project", other_account.id)
        other_agent = self.core.register_agent(
            other_project.id,
            "main",
            "PRINCIPAL",
            config_ref="agents#main",
            documentation_ref="README.md",
        )
        self.core.create_task(other_project.id, other_agent.id, "CODEX", prompt="other")
        await self.worker().run_once()
        self.assertEqual(self.executors[-1].config.account_id, self.account.id)


if __name__ == "__main__":
    unittest.main()
