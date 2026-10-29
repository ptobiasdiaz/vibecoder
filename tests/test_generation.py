from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.test_github import FakeGitHubClient
from vibecoder import ConflictError, GitHubRegistryService, OrchestratorCore, SQLiteRepository
from vibecoder.bootstrap import BootstrapService
from vibecoder.codex_executor import AccountRuntimeConfig, ExecutionResult
from vibecoder.domain import AttemptStatus, BootstrapStatus, GenerationStatus, TaskStatus
from vibecoder.generation import GeneratedFilesValidator, GenerationService
from vibecoder.git import GitAdapter


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


class GeneratingExecutor:
    def __init__(self, config: AccountRuntimeConfig, state: dict[str, Any]):
        self.config = config
        self.state = state

    async def start(self) -> None:
        self.state["starts"] = self.state.get("starts", 0) + 1

    async def close(self) -> None:
        self.state["closes"] = self.state.get("closes", 0) + 1

    async def ensure_thread(self, thread_id: str | None) -> tuple[str, bool]:
        return thread_id or "generation-thread", thread_id is None

    async def start_turn(self, thread_id: str, prompt: str) -> str:
        self.state.setdefault("prompts", []).append(prompt)
        return "generation-turn"

    async def wait_turn(self, thread_id: str, turn_id: str) -> ExecutionResult:
        workspace = self.config.cwd
        (workspace / ".vibecoder/agents/main").mkdir(parents=True, exist_ok=True)
        (workspace / "doc").mkdir(exist_ok=True)
        (workspace / "AGENTS.md").write_text(
            "# Project rules\n\n- Rule: language = python\n", encoding="utf-8"
        )
        (workspace / ".vibecoder/agents/main/AGENTS.md").write_text(
            "# Main agent\n\nMaintain approved scope.\n", encoding="utf-8"
        )
        (workspace / ".vibecoder/project.md").write_text(
            "# Context\n\nApproved bootstrap.\n", encoding="utf-8"
        )
        (workspace / "doc/architecture.md").write_text(
            "# Architecture\n", encoding="utf-8"
        )
        (workspace / "README.md").write_text("# Generated project\n", encoding="utf-8")
        if self.state.get("sensitive"):
            (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
        return ExecutionResult(thread_id, turn_id, "completed", "generated")

    async def reconcile_turn(self, thread_id: str, turn_id: str) -> ExecutionResult | None:
        return None


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "state.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("project", self.account.id)
        self.remote_api = FakeGitHubClient()
        self.github = GitHubRegistryService(self.core, self.remote_api)
        self.github.register_repository(self.project.id, "acme", "alpha")
        bootstrap = BootstrapService(
            self.core, self.github, {}, authorized_users=("alice",)
        )
        self.workflow = bootstrap.start(self.project.id)
        self.remote = self.root / "remote.git"
        git("init", "--bare", str(self.remote))
        self.config = AccountRuntimeConfig(
            account_id=self.account.id,
            codex_home=self.root,
            cwd=self.root,
        )
        self.state: dict[str, Any] = {}
        self.bootstrap = bootstrap

    async def asyncTearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def approve(self) -> None:
        proposal = self.bootstrap.git_configurations.latest(self.workflow.id)
        self.remote_api.comments.setdefault(self.workflow.issue_number, []).append(
            {
                "id": 4800,
                "node_id": "GC_4800",
                "body": self.bootstrap.git_configurations.render(
                    proposal, confirmation="confirmed"
                ),
                "html_url": "https://github.test/config-confirmation",
                "user": {"login": "alice", "type": "User"},
                "created_at": "2026-10-29T11:59:00Z",
                "updated_at": "2026-10-29T11:59:00Z",
            }
        )
        self.bootstrap.poll(self.workflow.id)
        issue = self.remote_api.get_issue("acme", "alpha", self.workflow.issue_number)
        issue["labels"] = [self.remote_api.labels[self.workflow.approval_label]]
        self.remote_api.events.setdefault(self.workflow.issue_number, []).append(
            {
                "id": 9001,
                "node_id": "EV_9001",
                "event": "labeled",
                "actor": {"login": "alice", "type": "User"},
                "label": {"name": self.workflow.approval_label, "color": "0e8a16"},
                "created_at": "2026-10-29T12:00:00Z",
            }
        )
        self.bootstrap.poll(self.workflow.id)
        self.assertEqual(self.bootstrap.get_workflow(self.workflow.id).status, BootstrapStatus.APPROVED)

    def service(self) -> GenerationService:
        def factory(config: AccountRuntimeConfig) -> GeneratingExecutor:
            return GeneratingExecutor(config, self.state)

        return GenerationService(
            self.core,
            {self.account.id: self.config},
            self.root / "workspaces",
            executor_factory=factory,
        )

    def prepare(self):
        return self.service().prepare(self.project.id, remote_url=str(self.remote))

    async def test_rejects_generation_without_verified_approval(self) -> None:
        with self.assertRaises(ConflictError):
            self.prepare()

    async def test_prepare_is_idempotent_and_records_authorization_and_hash(self) -> None:
        self.approve()
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.approved_by, "alice")
        self.assertEqual(first.approved_event_id, 9001)
        self.assertEqual(len(first.specification_hash), 64)
        self.assertEqual(first.status, GenerationStatus.PENDING)
        self.assertIsNone(first.base_branch)
        applied = self.repository.get("git_configurations", first.git_configuration_id)
        self.assertEqual(applied["status"], "APPLIED")

    async def test_generation_uses_m02_separate_session_validates_and_commits(self) -> None:
        self.approve()
        conversation_session = self.core.create_session(self.workflow.principal_agent_id)
        prepared = self.prepare()
        completed = await self.service().generate(prepared.id)
        self.assertEqual(completed.status, GenerationStatus.READY_TO_PUBLISH)
        self.assertIsNotNone(completed.commit_hash)
        self.assertNotEqual(completed.session_id, conversation_session.id)
        self.assertEqual(self.core.get_task(completed.task_id).status, TaskStatus.COMPLETED)
        self.assertEqual(self.core.list_attempts(completed.task_id)[0].status, AttemptStatus.SUCCEEDED)
        self.assertIn("AGENTS.md", completed.generated_instruction_versions)
        self.assertIn(".vibecoder/agents/main/AGENTS.md", completed.generated_instruction_versions)
        self.assertTrue(completed.validation_result["valid"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=Path(completed.workspace_path)), completed.commit_hash)

    async def test_generation_uses_only_frozen_specification_and_records_prompt(self) -> None:
        self.approve()
        prepared = self.prepare()
        await self.service().generate(prepared.id)
        prompt = self.state["prompts"][0]
        frozen = self.bootstrap.get_workflow(self.workflow.id).frozen_specification
        self.assertIn(frozen, prompt)
        self.assertIn(prepared.specification_hash, prompt)
        self.assertIn("AGENTS.md", prompt)
        self.assertIn(".vibecoder/agents/main/AGENTS.md", prompt)

    async def test_preexisting_local_changes_are_not_absorbed_by_generation(self) -> None:
        self.approve()
        prepared = self.prepare()
        (Path(prepared.workspace_path) / "unrelated.txt").write_text(
            "human work", encoding="utf-8"
        )
        blocked = await self.service().generate(prepared.id)
        self.assertEqual(blocked.status, GenerationStatus.BLOCKED)
        self.assertIsNone(blocked.task_id)
        self.assertNotIn("starts", self.state)

    async def test_configuration_change_after_prepare_blocks_generation(self) -> None:
        self.approve()
        prepared = self.prepare()
        row = self.repository.get("git_configurations", prepared.git_configuration_id)
        payload = json.loads(row["config_json"])
        payload["main_branch"] = "tampered"
        self.repository.update(
            "git_configurations",
            prepared.git_configuration_id,
            {"config_json": json.dumps(payload), "updated_at": prepared.updated_at},
        )
        blocked = await self.service().generate(prepared.id)
        self.assertEqual(blocked.status, GenerationStatus.BLOCKED)
        self.assertIn("configuración Git", blocked.blocked_reason)

    async def test_legacy_generation_is_preserved_and_requires_new_remote_approval(self) -> None:
        self.approve()
        completed = await self.service().generate(self.prepare().id)
        self.repository.update(
            "generation_workflows",
            completed.id,
            {
                "git_configuration_id": None,
                "git_configuration_hash": None,
                "updated_at": completed.updated_at,
            },
        )
        legacy = self.service().get(completed.id)
        self.assertEqual(legacy.commit_hash, completed.commit_hash)
        diagnosis = self.service().legacy_compatibility(completed.id)
        self.assertTrue(diagnosis["preserved"])
        self.assertTrue(diagnosis["requires_new_approval_for_remote_operations"])

    async def test_validation_blocks_sensitive_file_and_preserves_workspace(self) -> None:
        self.approve()
        self.state["sensitive"] = True
        prepared = self.prepare()
        blocked = await self.service().generate(prepared.id)
        self.assertEqual(blocked.status, GenerationStatus.BLOCKED)
        self.assertIsNone(blocked.commit_hash)
        self.assertTrue((Path(blocked.workspace_path) / ".env").exists())
        self.assertIn("archivo sensible", blocked.blocked_reason)

    async def test_blocked_validation_can_be_reconciled_without_reexecuting_codex(self) -> None:
        self.approve()
        self.state["sensitive"] = True
        blocked = await self.service().generate(self.prepare().id)
        (Path(blocked.workspace_path) / ".env").unlink()
        reconciled = self.service().recover(blocked.id)
        self.assertEqual(reconciled.status, GenerationStatus.READY_TO_PUBLISH)
        self.assertEqual(self.state["starts"], 1)

    async def test_ready_generation_is_idempotent_without_second_execution_or_commit(self) -> None:
        self.approve()
        prepared = self.prepare()
        first = await self.service().generate(prepared.id)
        second = await self.service().generate(prepared.id)
        self.assertEqual(first.commit_hash, second.commit_hash)
        self.assertEqual(self.state["starts"], 1)
        self.assertEqual(len(self.core.list_attempts(first.task_id)), 1)

    async def test_persistence_and_inspection_after_restart(self) -> None:
        self.approve()
        completed = await self.service().generate(self.prepare().id)
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        recovered = self.service().get(completed.id)
        inspected = self.service().inspect(completed.id)
        self.assertEqual(recovered.commit_hash, completed.commit_hash)
        self.assertEqual(inspected["git"]["changes"], ())

    async def test_interrupted_generation_is_blocked_without_resending(self) -> None:
        self.approve()
        prepared = self.prepare()
        task = self.core.create_task(
            self.project.id,
            self.workflow.principal_agent_id,
            "INITIAL_PROJECT_GENERATION",
            idempotency_key=f"initial-generation:{prepared.specification_hash}",
            prompt="prompt",
        )
        session = self.core.create_session(self.workflow.principal_agent_id)
        self.core.start_attempt(task.id)
        self.repository.update(
            "generation_workflows",
            prepared.id,
            {
                "task_id": task.id,
                "session_id": session.id,
                "status": GenerationStatus.GENERATING.value,
                "updated_at": prepared.updated_at,
            },
        )
        recovered = self.service().recover(prepared.id)
        self.assertEqual(recovered.status, GenerationStatus.BLOCKED)
        self.assertEqual(self.core.get_task(task.id).status, TaskStatus.WAITING)
        self.assertEqual(self.core.list_attempts(task.id)[0].status, AttemptStatus.UNKNOWN)
        self.assertNotIn("starts", self.state)

    async def test_workspace_isolation_between_projects(self) -> None:
        self.approve()
        first = self.prepare()
        other_id = str(__import__("uuid").uuid4())
        other_path = self.service().workspaces.path_for(other_id)
        self.assertNotEqual(Path(first.workspace_path), other_path)

    async def test_archive_requires_terminal_state_and_keeps_files(self) -> None:
        self.approve()
        prepared = self.prepare()
        with self.assertRaises(ConflictError):
            self.service().archive(prepared.id)
        completed = await self.service().generate(prepared.id)
        archived = self.service().archive(completed.id)
        self.assertTrue((archived / "AGENTS.md").exists())
        self.assertEqual(self.service().get(completed.id).status, GenerationStatus.ARCHIVED)


class GeneratedFilesValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        git("init", "--bare", str(self.remote))
        self.workspace = self.root / "workspace"
        self.adapter = GitAdapter()
        self.adapter.clone(str(self.remote), self.workspace)
        self.adapter.create_branch(self.workspace, "vibecoder/bootstrap-test", None)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def required(self) -> None:
        (self.workspace / ".vibecoder/agents/main").mkdir(parents=True, exist_ok=True)
        (self.workspace / "AGENTS.md").write_text("# Global\n", encoding="utf-8")
        (self.workspace / ".vibecoder/agents/main/AGENTS.md").write_text(
            "# Main\n", encoding="utf-8"
        )

    def test_rejects_no_changes_and_missing_instruction_files(self) -> None:
        report = GeneratedFilesValidator(self.adapter).validate(
            self.workspace, "vibecoder/bootstrap-test"
        )
        self.assertFalse(report.valid)
        self.assertIn("la generación no produjo cambios", report.errors)

    def test_rejects_large_file(self) -> None:
        self.required()
        (self.workspace / "large.bin").write_bytes(b"x" * 11)
        report = GeneratedFilesValidator(self.adapter, max_file_size=10).validate(
            self.workspace, "vibecoder/bootstrap-test"
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("excesivamente grande" in item for item in report.errors))

    def test_rejects_dangerous_symlink(self) -> None:
        self.required()
        (self.workspace / "escape").symlink_to(self.root / "outside")
        report = GeneratedFilesValidator(self.adapter).validate(
            self.workspace, "vibecoder/bootstrap-test"
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("enlace simbólico" in item for item in report.errors))

    def test_rejects_private_key_content(self) -> None:
        self.required()
        (self.workspace / "notes.txt").write_text(
            "-----BEGIN PRIVATE KEY-----\nsecret", encoding="utf-8"
        )
        report = GeneratedFilesValidator(self.adapter).validate(
            self.workspace, "vibecoder/bootstrap-test"
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("credencial" in item for item in report.errors))


if __name__ == "__main__":
    unittest.main()
