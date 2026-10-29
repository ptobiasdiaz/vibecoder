from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from tests.test_github import FakeGitHubClient
from vibecoder import GitHubRegistryService, OrchestratorCore, SQLiteRepository
from vibecoder.bootstrap import BootstrapService
from vibecoder.codex_executor import AccountRuntimeConfig, CodexExecutorError, ExecutionResult
from vibecoder.domain import (
    BootstrapCommentStatus,
    BootstrapStatus,
    ProjectStatus,
    SessionPersistenceState,
)
from vibecoder.github import GitHubTransientError


class BootstrapExecutor:
    def __init__(self, config: AccountRuntimeConfig, state: dict[str, Any]):
        self.state = state
        self.received_thread: str | None = None

    async def start(self) -> None:
        error = self.state.get("error")
        if error:
            raise error

    async def close(self) -> None:
        return None

    async def ensure_thread(self, thread_id: str | None) -> tuple[str, bool]:
        self.received_thread = thread_id
        self.state.setdefault("threads", []).append(thread_id)
        return thread_id or "thread-bootstrap", thread_id is None

    async def start_turn(self, thread_id: str, prompt: str) -> str:
        self.state.setdefault("prompts", []).append(prompt)
        return f"turn-{len(self.state['prompts'])}"

    async def wait_turn(self, thread_id: str, turn_id: str) -> ExecutionResult:
        return ExecutionResult(
            thread_id, turn_id, "completed", str(self.state.get("reply", "Respuesta agente"))
        )

    async def reconcile_turn(self, thread_id: str, turn_id: str) -> ExecutionResult | None:
        return None


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "bootstrap.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("alpha", self.account.id)
        self.remote = FakeGitHubClient()
        self.github = GitHubRegistryService(self.core, self.remote)
        self.repo = self.github.register_repository(self.project.id, "acme", "alpha")
        self.config = AccountRuntimeConfig(
            account_id=self.account.id,
            codex_home=Path(self.temp.name).resolve(),
            cwd=Path.cwd().resolve(),
        )
        self.executor_state: dict[str, Any] = {}
        self.service = self.make_service()

    async def asyncTearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def make_service(self, users: tuple[str, ...] = ("alice",)) -> BootstrapService:
        def factory(config: AccountRuntimeConfig) -> BootstrapExecutor:
            return BootstrapExecutor(config, self.executor_state)

        return BootstrapService(
            self.core,
            self.github,
            {self.account.id: self.config},
            authorized_users=users,
            executor_factory=factory,
        )

    def start(self):
        return self.service.start(self.project.id, description="Un proyecto nuevo")

    def human_comment(
        self, workflow, body: str, *, login: str = "alice", comment_id: int | None = None,
        author_type: str = "User", created_at: str | None = None,
    ) -> dict[str, Any]:
        comment_id = comment_id or (5000 + len(self.remote.comments.get(workflow.issue_number, [])))
        created_at = created_at or f"2026-10-28T01:00:{comment_id % 60:02d}Z"
        item = {
            "id": comment_id,
            "node_id": f"HC_{comment_id}",
            "body": body,
            "html_url": f"https://github.test/acme/alpha/issues/{workflow.issue_number}#c{comment_id}",
            "user": {"login": login, "type": author_type},
            "created_at": created_at,
            "updated_at": created_at,
        }
        self.remote.comments.setdefault(workflow.issue_number, []).append(item)
        return item

    def approve(self, workflow, *, login: str = "alice", event_id: int = 9001) -> None:
        issue = self.remote.get_issue("acme", "alpha", workflow.issue_number)
        issue["labels"] = [self.remote.labels[workflow.approval_label]]
        self.remote.events.setdefault(workflow.issue_number, []).append(
            {
                "id": event_id,
                "node_id": f"EV_{event_id}",
                "event": "labeled",
                "actor": {"login": login, "type": "User"},
                "label": {"name": workflow.approval_label, "color": "0e8a16"},
                "created_at": "2026-10-28T02:00:00Z",
            }
        )

    async def test_start_is_idempotent_and_registers_main_agent(self) -> None:
        first = self.start()
        second = self.start()
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.remote.issue_writes, 1)
        self.assertEqual(self.core.get_project(self.project.id).status, ProjectStatus.BOOTSTRAPPING)
        agents = self.core.list_agents(self.project.id)
        self.assertEqual([(item.name, item.agent_type.value) for item in agents], [("main", "PRINCIPAL")])

    async def test_processes_authorized_comment_and_routes_agent_main(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "/agent main\n\nNecesito una herramienta local")
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "COMPLETED")
        comment = self.service.list_comments(workflow.id)[0]
        self.assertEqual(comment.status, BootstrapCommentStatus.COMPLETED)
        self.assertIn("Necesito una herramienta local", self.executor_state["prompts"][0])
        self.assertNotIn("/agent main", self.executor_state["prompts"][0])

    async def test_personal_token_can_process_comment_from_same_user(self) -> None:
        workflow = self.start()
        self.remote.authenticate = lambda: {  # type: ignore[method-assign]
            "id": 1,
            "login": "alice",
            "node_id": "U_1",
            "html_url": "https://github.test/alice",
        }
        self.human_comment(workflow, "/agent main\n\nMi objetivo", login="alice")
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "COMPLETED")
        self.assertEqual(len(self.executor_state["prompts"]), 1)

    async def test_personal_token_ignores_only_persisted_remote_responses(self) -> None:
        workflow = self.start()
        self.remote.authenticate = lambda: {  # type: ignore[method-assign]
            "id": 1,
            "login": "alice",
            "node_id": "U_1",
            "html_url": "https://github.test/alice",
        }

        def create_as_alice(*args: object, **kwargs: object) -> Mapping[str, Any]:
            result = dict(FakeGitHubClient.create_comment(self.remote, *args, **kwargs))
            result["user"] = {"login": "alice", "type": "User"}
            self.remote.comments[workflow.issue_number][-1] = result
            return result

        self.remote.create_comment = create_as_alice  # type: ignore[method-assign]
        self.human_comment(workflow, "Mi objetivo", login="alice", comment_id=5001)
        first = await self.service.process_round(workflow.id)
        second = await self.service.process_round(workflow.id)
        self.assertEqual(first.status, "COMPLETED")
        self.assertEqual(second.status, "IDLE")
        self.assertEqual(len(self.executor_state["prompts"]), 1)
        comments = self.service.list_comments(workflow.id)
        own_response = next(item for item in comments if item.github_comment_id == 2001)
        self.assertEqual(own_response.status, BootstrapCommentStatus.IGNORED)

    async def test_reclassifies_same_user_comment_ignored_by_previous_version(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "Comentario recuperable", login="alice", comment_id=5001)
        comment = self.service.poll(workflow.id)[0]
        self.repository.update(
            "bootstrap_comments",
            comment.id,
            {
                "status": BootstrapCommentStatus.IGNORED.value,
                "rejection_reason": "comentario del bot",
                "updated_at": comment.updated_at,
            },
        )
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "COMPLETED")
        self.assertIn("Comentario recuperable", self.executor_state["prompts"][0])

    async def test_comments_are_processed_in_chronological_order_without_duplicates(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "segundo", comment_id=5002, created_at="2026-10-28T01:00:02Z")
        self.human_comment(workflow, "primero", comment_id=5001, created_at="2026-10-28T01:00:01Z")
        await self.service.process_round(workflow.id)
        comments = self.service.list_comments(workflow.id)
        self.assertEqual([item.github_comment_id for item in comments], [5001, 5002])
        self.assertEqual(comments[0].status, BootstrapCommentStatus.COMPLETED)
        self.assertEqual(comments[1].status, BootstrapCommentStatus.RECEIVED)
        await self.service.process_round(workflow.id)
        await self.service.process_round(workflow.id)
        self.assertEqual(len(self.executor_state["prompts"]), 2)

    async def test_ignores_bot_and_rejects_unauthorized_or_unknown_directive(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "eco", login="bot", author_type="Bot", comment_id=5001)
        self.human_comment(workflow, "intruso", login="mallory", comment_id=5002)
        self.human_comment(workflow, "/agent admin\nhaz algo", comment_id=5003)
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "IDLE")
        comments = self.service.list_comments(workflow.id)
        self.assertEqual(comments[0].status, BootstrapCommentStatus.IGNORED)
        self.assertEqual(len(self.service.errors(workflow.id)), 2)
        self.assertNotIn("prompts", self.executor_state)

    async def test_transient_poll_failure_can_be_retried_without_duplicates(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "mensaje", comment_id=5001)
        original = self.remote.list_comments
        calls = 0

        def fail_once(*args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise GitHubTransientError("temporary")
            return original(*args, **kwargs)

        self.remote.list_comments = fail_once  # type: ignore[method-assign]
        with self.assertRaises(GitHubTransientError):
            self.service.poll(workflow.id)
        self.service.poll(workflow.id)
        self.service.poll(workflow.id)
        self.assertEqual(len(self.service.list_comments(workflow.id)), 1)

    async def test_reuses_persistent_session_across_comments_and_restart(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "uno", comment_id=5001)
        await self.service.process_round(workflow.id)
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.github = GitHubRegistryService(self.core, self.remote)
        self.service = self.make_service()
        self.human_comment(workflow, "dos", comment_id=5002)
        await self.service.process_round(workflow.id)
        self.assertEqual(self.executor_state["threads"], [None, "thread-bootstrap"])
        session = self.core.get_or_create_session(workflow.principal_agent_id)
        self.assertEqual(session.persistence_state, SessionPersistenceState.RECOVERABLE)

    async def test_codex_failure_blocks_without_duplicate_execution(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "fallará")
        self.executor_state["error"] = CodexExecutorError("boom", code="FAILED")
        first = await self.service.process_round(workflow.id)
        second = await self.service.process_round(workflow.id)
        self.assertEqual(first.status, "BLOCKED")
        self.assertEqual(second.status, "BLOCKED")
        self.assertNotIn("prompts", self.executor_state)

    async def test_running_comment_is_not_claimed_twice(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "ocupado")
        comment = self.service.poll(workflow.id)[0]
        task = self.core.create_task(
            workflow.project_id,
            workflow.principal_agent_id,
            "BOOTSTRAP_COMMENT",
            idempotency_key=f"bootstrap-comment:{comment.github_comment_id}",
            prompt="prompt",
        )
        self.repository.update(
            "bootstrap_comments",
            comment.id,
            {"status": "RUNNING", "task_id": task.id, "updated_at": comment.updated_at},
        )
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "BUSY")
        self.assertNotIn("prompts", self.executor_state)

    async def test_recovery_marks_uncertain_running_task_blocked(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "interrumpido")
        comment = self.service.poll(workflow.id)[0]
        task = self.core.create_task(
            workflow.project_id,
            workflow.principal_agent_id,
            "BOOTSTRAP_COMMENT",
            idempotency_key=f"bootstrap-comment:{comment.github_comment_id}",
            prompt="prompt",
        )
        self.repository.update(
            "bootstrap_comments",
            comment.id,
            {"status": "RUNNING", "task_id": task.id, "updated_at": comment.updated_at},
        )
        self.core.start_attempt(task.id)
        recovered = self.service.recover(workflow.id)
        self.assertEqual(recovered[0].status, "BLOCKED")
        current = self.service.list_comments(workflow.id)[0]
        self.assertEqual(current.status, BootstrapCommentStatus.BLOCKED)
        self.assertEqual(self.core.list_attempts(task.id)[0].status.value, "UNKNOWN")

    async def test_publication_retry_does_not_repeat_codex(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "publica")
        original = self.remote.create_comment
        calls = 0

        def fail_once(*args: object, **kwargs: object) -> Mapping[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise GitHubTransientError("network")
            return original(*args, **kwargs)

        self.remote.create_comment = fail_once  # type: ignore[method-assign]
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "RESPONSE_PENDING")
        self.assertEqual(len(self.executor_state["prompts"]), 1)
        retried = self.service.retry_publications(workflow.id)
        self.assertEqual(retried[0].status, "COMPLETED")
        self.assertEqual(len(self.executor_state["prompts"]), 1)

    async def test_verified_approval_freezes_scope(self) -> None:
        workflow = self.start()
        self.human_comment(workflow, "objetivo", comment_id=5001)
        await self.service.process_round(workflow.id)
        self.approve(workflow)
        self.service.poll(workflow.id)
        approved = self.service.get_workflow(workflow.id)
        self.assertEqual(approved.status, BootstrapStatus.APPROVED)
        self.assertEqual(approved.approved_by, "alice")
        self.assertIn("objetivo", approved.frozen_specification)
        self.assertEqual(self.core.get_project(self.project.id).status, ProjectStatus.DESIGNING)
        self.human_comment(workflow, "cambio silencioso", comment_id=5002)
        await self.service.process_round(workflow.id)
        last = self.service.list_comments(workflow.id)[-1]
        self.assertEqual(last.status, BootstrapCommentStatus.REJECTED)
        self.assertEqual(last.rejection_reason, "alcance aprobado y congelado")
        self.assertEqual(len(self.executor_state["prompts"]), 1)

    async def test_unattributed_or_unauthorized_label_is_not_approval(self) -> None:
        workflow = self.start()
        issue = self.remote.get_issue("acme", "alpha", workflow.issue_number)
        issue["labels"] = [self.remote.labels[workflow.approval_label]]
        self.service.poll(workflow.id)
        self.assertEqual(
            self.service.get_workflow(workflow.id).status, BootstrapStatus.APPROVAL_PENDING
        )

    async def test_latest_label_event_must_be_authorized(self) -> None:
        workflow = self.start()
        self.approve(workflow, login="alice", event_id=9001)
        self.approve(workflow, login="mallory", event_id=9002)
        self.remote.events[workflow.issue_number][-1]["created_at"] = "2026-10-28T03:00:00Z"
        self.service.poll(workflow.id)
        self.assertEqual(
            self.service.get_workflow(workflow.id).status,
            BootstrapStatus.APPROVAL_PENDING,
        )
        self.approve(workflow, login="mallory")
        self.service.poll(workflow.id)
        self.assertEqual(
            self.service.get_workflow(workflow.id).status, BootstrapStatus.APPROVAL_PENDING
        )

    async def test_projects_are_isolated(self) -> None:
        first = self.start()
        second_project = self.core.register_project("beta", self.account.id)
        self.remote.repository = {
            **self.remote.repository,
            "id": 202,
            "node_id": "R_202",
            "name": "beta",
            "full_name": "acme/beta",
            "html_url": "https://github.test/acme/beta",
            "url": "https://api.github.test/repos/acme/beta",
        }
        self.github.register_repository(second_project.id, "acme", "beta")
        second = self.service.start(second_project.id)
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.github_issue_id, second.github_issue_id)
        self.human_comment(first, "solo alpha", comment_id=5101)
        await self.service.process_round(first.id)
        self.assertEqual(self.service.list_comments(second.id), [])


if __name__ == "__main__":
    unittest.main()
