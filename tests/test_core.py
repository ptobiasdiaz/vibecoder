from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vibecoder import (
    ConflictError,
    InvalidTransition,
    OrchestratorCore,
    SessionPersistenceState,
    SQLiteRepository,
    TaskStatus,
)
from vibecoder.domain import AttemptStatus


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "core.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account(
            "cuenta principal", "CHATGPT", "codex-home://account-main"
        )
        self.project = self.core.register_project("alpha", self.account.id)
        self.agent = self.core.register_agent(
            self.project.id,
            "main",
            "PRINCIPAL",
            config_ref="config/agents.yaml#main",
            documentation_ref="docs/architecture/overview.md",
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def test_create_and_query_accounts_projects_and_agents(self) -> None:
        self.assertEqual(self.core.get_account(self.account.id), self.account)
        self.assertEqual(self.core.get_project(self.project.id), self.project)
        self.assertEqual(self.core.get_agent(self.agent.id), self.agent)
        self.assertEqual(self.core.list_projects(), [self.project])
        self.assertEqual(self.core.list_agents(self.project.id), [self.agent])

    def test_assign_project_account_without_sessions(self) -> None:
        second = self.core.register_account("segunda", "API_KEY", "secret://codex/second")
        updated = self.core.assign_project_account(self.project.id, second.id)
        self.assertEqual(updated.codex_account_id, second.id)

    def test_account_change_is_rejected_after_session_exists(self) -> None:
        self.core.create_session(self.agent.id)
        second = self.core.register_account("segunda", "API_KEY", "secret://codex/second")
        with self.assertRaises(ConflictError):
            self.core.assign_project_account(self.project.id, second.id)

    def test_session_lifecycle_does_not_assume_empty_thread_is_persistent(self) -> None:
        session = self.core.create_session(self.agent.id)
        self.assertEqual(session.persistence_state, SessionPersistenceState.NOT_CREATED)
        session = self.core.transition_session(
            session.id,
            SessionPersistenceState.THREAD_CREATED_UNCONFIRMED,
            codex_thread_id="thread-1",
        )
        self.assertEqual(
            session.persistence_state, SessionPersistenceState.THREAD_CREATED_UNCONFIRMED
        )
        with self.assertRaises(InvalidTransition):
            self.core.transition_session(session.id, SessionPersistenceState.RECOVERABLE)
        session = self.core.transition_session(
            session.id, SessionPersistenceState.THREAD_PERSISTED
        )
        session = self.core.transition_session(session.id, SessionPersistenceState.RECOVERABLE)
        self.assertEqual(session.persistence_state, SessionPersistenceState.RECOVERABLE)

    def test_create_and_transition_task(self) -> None:
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        self.assertEqual(task.status, TaskStatus.PENDING)
        attempt = self.core.start_attempt(task.id)
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.RUNNING)
        self.core.finish_attempt(attempt.id, AttemptStatus.SUCCEEDED)
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.COMPLETED)

    def test_invalid_task_transition_is_rejected(self) -> None:
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        with self.assertRaises(InvalidTransition):
            self.core.transition_task(task.id, TaskStatus.COMPLETED)

    def test_waiting_states_can_resume_but_terminal_tasks_cannot(self) -> None:
        for waiting in (
            TaskStatus.WAITING,
            TaskStatus.WAITING_QUOTA,
            TaskStatus.WAITING_AUTH,
        ):
            task = self.core.create_task(self.project.id, self.agent.id, waiting.value)
            self.core.transition_task(task.id, TaskStatus.RUNNING)
            self.core.transition_task(task.id, waiting)
            resumed = self.core.transition_task(task.id, TaskStatus.RUNNING)
            self.assertEqual(resumed.status, TaskStatus.RUNNING)

    def test_task_assignment_is_limited_to_same_project_and_idle_task(self) -> None:
        specialist = self.core.register_agent(
            self.project.id,
            "specialist",
            "SPECIALIZED",
            config_ref="config/agents.yaml#specialist",
            documentation_ref="docs/specialist.md",
        )
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        self.assertEqual(self.core.assign_task(task.id, specialist.id).agent_id, specialist.id)
        self.core.transition_task(task.id, TaskStatus.RUNNING)
        with self.assertRaises(ConflictError):
            self.core.assign_task(task.id, self.agent.id)

    def test_structured_error_is_recorded(self) -> None:
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        attempt = self.core.start_attempt(task.id)
        completed = self.core.finish_attempt(
            attempt.id,
            AttemptStatus.FAILED,
            error_code="AUTH_EXPIRED",
            error_category="authentication",
            error_message="credential expired",
            error_details={"recoverable": True},
        )
        self.assertEqual(completed.error_code, "AUTH_EXPIRED")
        self.assertEqual(completed.error_details, {"recoverable": True})
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.FAILED)

    def test_idempotent_task_creation_returns_same_task(self) -> None:
        first = self.core.create_task(
            self.project.id, self.agent.id, "ANALYZE", idempotency_key="event-17"
        )
        second = self.core.create_task(
            self.project.id, self.agent.id, "ANALYZE", idempotency_key="event-17"
        )
        self.assertEqual(first.id, second.id)
        with self.assertRaises(ConflictError):
            self.core.create_task(
                self.project.id, self.agent.id, "DIFFERENT", idempotency_key="event-17"
            )

    def test_referential_integrity_rejects_cross_project_task(self) -> None:
        other = self.core.register_project("beta", self.account.id)
        with self.assertRaises(ConflictError):
            self.core.create_task(other.id, self.agent.id, "ANALYZE")

    def test_storage_foreign_key_rejects_unknown_project(self) -> None:
        with self.assertRaises(ConflictError):
            self.repository.insert(
                "agents",
                {
                    "id": "invalid-agent",
                    "project_id": "missing-project",
                    "name": "invalid",
                    "agent_type": "SPECIALIZED",
                    "config_ref": "config/missing",
                    "documentation_ref": "docs/missing",
                    "status": "ACTIVE",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
            )

    def test_projects_are_isolated(self) -> None:
        other = self.core.register_project("beta", self.account.id)
        other_agent = self.core.register_agent(
            other.id,
            "main",
            "PRINCIPAL",
            config_ref="config/agents.yaml#main",
            documentation_ref="docs/architecture/overview.md",
        )
        own_task = self.core.create_task(self.project.id, self.agent.id, "ONE")
        self.core.create_task(other.id, other_agent.id, "TWO")
        self.assertEqual(self.core.list_pending_tasks(self.project.id), [own_task])

    def test_records_survive_repository_restart(self) -> None:
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.assertEqual(self.core.get_project(self.project.id), self.project)
        self.assertEqual(self.core.get_task(task.id), task)

    def test_recovery_marks_in_flight_work_unknown_without_retrying(self) -> None:
        task = self.core.create_task(self.project.id, self.agent.id, "ANALYZE")
        attempt = self.core.start_attempt(task.id)
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        recovered = self.core.recover_interrupted_tasks()
        self.assertEqual([item.id for item in recovered], [task.id])
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.WAITING)
        recovered_attempt = self.core.get_attempt(attempt.id)
        self.assertEqual(recovered_attempt.status, AttemptStatus.UNKNOWN)
        self.assertEqual(recovered_attempt.error_code, "PROCESS_RESTARTED")
        self.assertEqual(len(self.core.list_attempts(task.id)), 1)


if __name__ == "__main__":
    unittest.main()

