from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.test_publication import PublicationRemote, PublishingGit
from vibecoder.agents import AgentManagementService
from vibecoder.codex_executor import AccountRuntimeConfig, ExecutionResult
from vibecoder.domain import ProjectStatus, TaskExecutionStatus
from vibecoder.errors import ConflictError
from vibecoder.github_service import GitHubRegistryService
from vibecoder.issues import IssueCoordinationService
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository
from vibecoder.tasks import TaskExecutionService


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout.strip()


class ImplementingExecutor:
    calls = 0

    def __init__(self, config: AccountRuntimeConfig):
        self.config = config

    async def start(self): pass
    async def close(self): pass
    async def ensure_thread(self, thread_id): return (thread_id or "implementation-thread", thread_id is None)
    async def start_turn(self, thread_id, prompt):
        type(self).calls += 1
        return "implementation-turn"
    async def wait_turn(self, thread_id, turn_id):
        target = self.config.cwd / "src" / "feature.py"
        target.parent.mkdir(exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return ExecutionResult(thread_id, turn_id, "completed", "implemented")
    async def reconcile_turn(self, thread_id, turn_id): return None


class TaskExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.checkout = self.root / "project"
        self.checkout.mkdir()
        git(self.checkout, "init", "-b", "main")
        (self.checkout / "AGENTS.md").write_text("# Rules\n\n- Rule: network = denied\n")
        (self.checkout / ".vibecoder/agents/main").mkdir(parents=True)
        (self.checkout / ".vibecoder/agents/main/AGENTS.md").write_text("# Main\n")
        git(self.checkout, "add", ".")
        git(self.checkout, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial")
        self.repository = SQLiteRepository(self.root / "state.sqlite3")
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("alpha", self.account.id, status=ProjectStatus.ACTIVE)
        self.main = self.core.register_agent(
            self.project.id, "main", "PRINCIPAL", "project://main",
            ".vibecoder/agents/main/AGENTS.md",
        )
        self.remote = PublicationRemote()
        self.github = GitHubRegistryService(self.core, self.remote)
        self.repo = self.github.register_repository(self.project.id, "acme", "alpha")
        git(self.checkout, "remote", "add", "origin", "https://github.test/acme/alpha.git")
        agents = AgentManagementService(self.core)
        specialist, _ = agents.propose(
            self.project.id, "backend", "Backend", "Backend", ("Implement",),
            ("Edit source",), ("No network",),
            "# Backend\n\n- Rule: network = denied\n", self.account.id,
            self.checkout, actor="operator",
        )
        agents.validate(specialist.agent_id, self.checkout, actor="operator")
        git(self.checkout, "add", ".vibecoder")
        git(self.checkout, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "add backend")
        self.specialist = agents.activate(
            specialist.agent_id, self.checkout, actor="operator", authorized=True
        )
        self.remote.issues.append({
            "id": 1002, "node_id": "I_2", "number": 2, "title": "Feature",
            "body": "Implement feature", "state": "open",
            "html_url": "https://github.test/acme/alpha/issues/2", "labels": [],
            "user": {"login": "alice", "type": "User"},
        })
        coordination = IssueCoordinationService(
            self.core, self.github, {}, self.checkout, authorized_users=("alice",)
        )
        workflow = coordination.discover(self.project.id)[0]
        payload = {
            "schema_version": 1, "objective": "Feature", "scope": ["src"],
            "exclusions": [], "acceptance_criteria": ["implemented"],
            "risks": [], "assumptions": [], "tasks": [{
                "id": "backend", "description": "Implement feature",
                "expected_result": "source", "assigned_agent": "backend",
                "dependencies": [], "acceptance_criteria": ["file exists"],
                "restrictions": ["src only"],
            }, {
                "id": "followup", "description": "Integrate follow-up",
                "expected_result": "more source", "assigned_agent": "backend",
                "dependencies": ["backend"], "acceptance_criteria": ["integrated"],
                "restrictions": ["src only"],
            }],
        }
        revision = coordination.publish_plan(
            coordination.propose_plan(workflow.id, payload, proposed_by_agent_id=self.main.id).id
        )
        issue = self.remote.get_issue("acme", "alpha", 2)
        issue["labels"] = [self.remote.labels[workflow.approval_label]]
        self.remote.events.setdefault(2, []).append({
            "id": 9200, "event": "labeled", "actor": {"login": "alice", "type": "User"},
            "label": {"name": workflow.approval_label}, "created_at": "2026-10-31T12:00:00Z",
        })
        approved = coordination.check_approval(revision.id)
        materialized = {task.id: task for task in coordination.materialize(approved.id)}
        planned = coordination.planned_tasks(approved.id)
        by_logical = {
            str(row["logical_id"]): materialized[str(row["materialized_task_id"])]
            for row in planned
        }
        self.task = by_logical["backend"]
        self.dependent_task = by_logical["followup"]
        self._git_configuration()
        self.bare = self.root / "remote.git"
        subprocess.run(("git", "init", "--bare", str(self.bare)), check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git(self.checkout, "push", str(self.bare), "main:main")
        subprocess.run(("git", "symbolic-ref", "HEAD", "refs/heads/main"), cwd=self.bare,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.base_sha = git(self.checkout, "rev-parse", "HEAD")
        self.remote.branches["main"] = self.base_sha
        self.remote.repository["default_branch"] = "main"
        self.remote.empty = False
        self.runtime = AccountRuntimeConfig(
            account_id=self.account.id, codex_home=self.root, cwd=self.checkout
        )
        ImplementingExecutor.calls = 0
        self.git_adapter = PublishingGit(self.remote)
        self.service = TaskExecutionService(
            self.core, self.github, {self.account.id: self.runtime},
            self.root / "tasks", project_workspace=self.checkout,
            git=self.git_adapter, executor_factory=ImplementingExecutor,
        )

    async def asyncTearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def _git_configuration(self):
        now = "2026-10-31T12:00:00Z"
        bootstrap = str(uuid4())
        self.repository.insert("bootstrap_workflows", {
            "id": bootstrap, "project_id": self.project.id, "repository_id": self.repo.id,
            "github_issue_id": 1001, "github_issue_node_id": "I_1", "issue_number": 1,
            "issue_url": "https://github.test/acme/alpha/issues/1",
            "principal_agent_id": self.main.id, "status": "APPROVED",
            "approval_label": "status:approved", "approved_by": "alice",
            "approved_event_id": 9100, "approved_at": now,
            "frozen_specification": "approved", "last_polled_at": None,
            "created_at": now, "updated_at": now,
        })
        payload = {
            "schema_version": 1, "repository_id": self.repo.id, "main_branch": "main",
            "base_branch_exists": True, "initialization_strategy": "EXISTING_BASE",
            "technical_commit_policy": "NONE", "publication_policy": "PULL_REQUEST",
            "integration_policy": "HUMAN_REVIEW_MERGE",
            "bootstrap_branch": "vibecoder/bootstrap", "authorized_operations": [
                "CREATE_PULL_REQUEST", "PUBLISH_BOOTSTRAP_BRANCH"
            ],
        }
        self.repository.insert("git_configurations", {
            "id": str(uuid4()), "workflow_id": bootstrap, "repository_id": self.repo.id,
            "revision": 1, "status": "APPLIED", "schema_version": 1,
            "config_json": json.dumps(payload, sort_keys=True),
            "configuration_hash": "config-hash", "source_comment_id": None,
            "observed_github_id": self.repo.github_id, "observed_is_empty": 0,
            "observed_default_branch": "main", "observed_head_sha": None,
            "specification_hash": "spec-hash", "approved_by": "alice",
            "approved_event_id": 9100, "applied_at": now,
            "created_at": now, "updated_at": now,
        })

    def prepare(self):
        return self.service.prepare(
            self.task.id, allowed_paths=("src",), remote_url=str(self.bare)
        )

    async def generated(self):
        return await self.service.run(self.prepare().id)

    async def committed(self):
        generated = await self.generated()
        validated = self.service.validate(generated.id)
        return self.service.commit(validated.id)

    async def test_only_materialized_approved_specialist_task_is_eligible(self):
        self.assertTrue(self.service.eligible(self.task.id)["eligible"])
        self.repository.update("projects", self.project.id, {"status": "DISABLED", "updated_at": "now"})
        self.assertFalse(self.service.eligible(self.task.id)["eligible"])

    async def test_prepare_isolated_workspace_branch_and_restart(self):
        execution = self.prepare()
        self.assertEqual(execution.status, TaskExecutionStatus.WORKSPACE_READY)
        self.assertEqual(execution.base_sha, self.base_sha)
        self.assertEqual(git(Path(execution.workspace_path), "branch", "--show-current"), execution.work_branch)
        repeated = self.prepare()
        self.assertEqual(repeated.id, execution.id)

    async def test_prepare_recovers_from_remote_read_failure_before_workspace(self):
        original = self.remote.get_repository
        calls = 0

        def fail_once(owner, name):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConflictError("remote temporarily unavailable")
            return original(owner, name)

        self.remote.get_repository = fail_once
        with self.assertRaises(ConflictError):
            self.prepare()
        execution = self.repository.find_one(
            "task_execution_workflows", task_id=self.task.id
        )
        self.assertEqual(execution["status"], TaskExecutionStatus.READY.value)
        recovered = self.prepare()
        self.assertEqual(recovered.status, TaskExecutionStatus.WORKSPACE_READY)

    async def test_dependency_waits_for_verified_merge(self):
        execution = self.service.prepare(
            self.dependent_task.id, allowed_paths=("src",), remote_url=str(self.bare)
        )
        self.assertEqual(execution.status, TaskExecutionStatus.WAITING_DEPENDENCIES)
        self.assertFalse(Path(execution.workspace_path).exists())

    async def test_run_uses_dedicated_persistent_session_and_does_not_repeat(self):
        execution = await self.generated()
        self.assertEqual(execution.status, TaskExecutionStatus.GENERATED)
        self.assertEqual(ImplementingExecutor.calls, 1)
        self.assertIsNotNone(execution.session_id)
        repeated = await self.service.run(execution.id)
        self.assertEqual(repeated.id, execution.id)
        self.assertEqual(ImplementingExecutor.calls, 1)

    async def test_validation_blocks_sensitive_and_out_of_scope_changes(self):
        execution = await self.generated()
        (Path(execution.workspace_path) / "AGENTS.md").write_text("changed")
        blocked = self.service.validate(execution.id)
        self.assertEqual(blocked.status, TaskExecutionStatus.BLOCKED)
        self.assertIn("sensible", blocked.blocked_reason)

    async def test_commit_is_idempotent_and_records_exact_manifest(self):
        committed = await self.committed()
        self.assertEqual(committed.status, TaskExecutionStatus.COMMITTED)
        self.assertEqual(self.service.commit(committed.id).commit_sha, committed.commit_sha)

    async def test_push_and_pr_are_idempotent_without_merge(self):
        committed = await self.committed()
        published = self.service.publish(committed.id, token="secret", authorized=True)
        self.assertEqual(published.status, TaskExecutionStatus.BRANCH_PUBLISHED)
        self.assertEqual(self.remote.branches[published.work_branch], published.commit_sha)
        pr = self.service.create_pr(published.id, authorized=True)
        self.assertEqual(pr.status, TaskExecutionStatus.PR_OPEN)
        self.assertEqual(self.service.create_pr(pr.id, authorized=True).pull_request_number,
                         pr.pull_request_number)
        self.assertFalse(self.remote.pulls[0]["merged"])

    async def test_uncertain_push_reconciles_without_second_push(self):
        committed = await self.committed()
        self.git_adapter.fail_after_push = True
        unknown = self.service.publish(committed.id, token="secret", authorized=True)
        self.assertEqual(unknown.status, TaskExecutionStatus.UNKNOWN)
        recovered = self.service.reconcile(unknown.id)
        self.assertEqual(recovered.status, TaskExecutionStatus.BRANCH_PUBLISHED)
        self.assertEqual(len(self.git_adapter.pushes), 1)

    async def test_lost_pr_response_is_reconciled_without_duplicate(self):
        committed = await self.committed()
        published = self.service.publish(committed.id, token="secret", authorized=True)
        self.remote.lose_pr_response = True
        unknown = self.service.create_pr(published.id, authorized=True)
        self.assertEqual(unknown.status, TaskExecutionStatus.UNKNOWN)
        recovered = self.service.reconcile(unknown.id)
        self.assertEqual(recovered.status, TaskExecutionStatus.PR_OPEN)
        self.assertEqual(self.remote.pr_writes, 1)

if __name__ == "__main__":
    unittest.main()
