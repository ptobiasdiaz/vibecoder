from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.test_github import FakeGitHubClient
from vibecoder.agents import AgentManagementService
from vibecoder.codex_executor import AccountRuntimeConfig, ExecutionResult
from vibecoder.domain import (
    IssueCommentStatus,
    IssueWorkflowStatus,
    PlanRevisionStatus,
    ProjectStatus,
    SpecialistStatus,
    TaskStatus,
)
from vibecoder.errors import ConflictError, ValidationError
from vibecoder.github import GitHubTransientError
from vibecoder.github_service import GitHubRegistryService
from vibecoder.issues import IssueCoordinationService
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository
from vibecoder.worker import LocalWorker


def git(path: Path, *args: str) -> None:
    subprocess.run(
        ("git", *args), cwd=path, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"},
    )


class PlanningExecutor:
    def __init__(self, config: AccountRuntimeConfig, state: dict[str, Any]):
        self.state = state

    async def start(self): pass
    async def close(self): pass

    async def ensure_thread(self, thread_id):
        self.state.setdefault("threads", []).append(thread_id)
        return thread_id or f"thread-{len(self.state['threads'])}", thread_id is None

    async def start_turn(self, thread_id, prompt):
        self.state.setdefault("prompts", []).append(prompt)
        return f"turn-{len(self.state['prompts'])}"

    async def wait_turn(self, thread_id, turn_id):
        return ExecutionResult(thread_id, turn_id, "completed", self.state.get("reply", "Planning response"))

    async def reconcile_turn(self, thread_id, turn_id): return None


class IssueCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "state.sqlite3"
        self.workspace = root / "project"
        self.workspace.mkdir()
        git(self.workspace, "init", "-b", "main")
        (self.workspace / "AGENTS.md").write_text("# Rules\n\n- Rule: network = denied\n")
        (self.workspace / ".vibecoder/agents/main").mkdir(parents=True)
        (self.workspace / ".vibecoder/agents/main/AGENTS.md").write_text("# Main\n")
        git(self.workspace, "add", ".")
        git(self.workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial")
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("alpha", self.account.id, status=ProjectStatus.ACTIVE)
        self.main = self.core.register_agent(
            self.project.id, "main", "PRINCIPAL", "project://main",
            ".vibecoder/agents/main/AGENTS.md",
        )
        self.remote = FakeGitHubClient()
        self.github = GitHubRegistryService(self.core, self.remote)
        self.repo = self.github.register_repository(self.project.id, "acme", "alpha")
        self.runtime = AccountRuntimeConfig(
            account_id=self.account.id, codex_home=root.resolve(), cwd=self.workspace.resolve()
        )
        self.executor_state: dict[str, Any] = {}
        self.service = self.make_service()
        self.specialists = AgentManagementService(self.core)

    async def asyncTearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def make_service(self, users=("alice",)):
        def factory(config): return PlanningExecutor(config, self.executor_state)
        return IssueCoordinationService(
            self.core, self.github, {self.account.id: self.runtime}, self.workspace,
            authorized_users=users, executor_factory=factory,
        )

    def issue(self, number=2, *, state="open", title="Add search", pull=False):
        item = {
            "id": 1000 + number, "node_id": f"I_{number}", "number": number,
            "title": title, "body": "We need search", "state": state,
            "html_url": f"https://github.test/acme/alpha/issues/{number}",
            "labels": [], "user": {"login": "alice", "type": "User"},
        }
        if pull: item["pull_request"] = {"url": "pr"}
        self.remote.issues.append(item)
        return item

    def comment(self, workflow, body, *, login="alice", kind="User", cid=None, created=None):
        cid = cid or 5000 + len(self.remote.comments.get(workflow.issue_number, []))
        created = created or f"2026-10-30T10:00:{cid % 60:02d}Z"
        item = {"id": cid, "node_id": f"C_{cid}", "body": body,
                "html_url": f"https://github.test/c/{cid}",
                "user": {"login": login, "type": kind},
                "created_at": created, "updated_at": created}
        self.remote.comments.setdefault(workflow.issue_number, []).append(item)
        return item

    def add_specialist(self, logical="testing"):
        specialist, _ = self.specialists.propose(
            self.project.id, logical, logical.title(), "Testing", ("Review tests",),
            ("Inspect tests",), ("No implementation",),
            "# Testing\n\n- Rule: network = denied\n", self.account.id,
            self.workspace, actor="operator",
        )
        self.specialists.validate(specialist.agent_id, self.workspace, actor="operator")
        git(self.workspace, "add", ".vibecoder")
        git(self.workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", f"add {logical}")
        return self.specialists.activate(
            specialist.agent_id, self.workspace, actor="operator", authorized=True
        )

    def discover_one(self):
        self.issue()
        return self.service.discover(self.project.id)[0]

    def plan(self, agent="testing", suffix=""):
        return {
            "schema_version": 1, "objective": f"Implement search{suffix}",
            "scope": ["Search endpoint"], "exclusions": ["UI"],
            "acceptance_criteria": ["Search is tested"], "risks": ["Index size"],
            "assumptions": ["SQLite remains"],
            "tasks": [
                {"id": "design", "description": "Review design", "expected_result": "Design notes",
                 "assigned_agent": agent, "dependencies": [],
                 "acceptance_criteria": ["Risks recorded"], "restrictions": ["No code"]},
                {"id": "tests", "description": "Plan tests", "expected_result": "Test plan",
                 "assigned_agent": agent, "dependencies": ["design"],
                 "acceptance_criteria": ["Cases listed"], "restrictions": []},
            ],
        }

    def approve_event(self, workflow, event_id, created="2026-10-30T12:00:00Z", login="alice"):
        issue = self.remote.get_issue("acme", "alpha", workflow.issue_number)
        issue["labels"] = [self.remote.labels[workflow.approval_label]]
        self.remote.events.setdefault(workflow.issue_number, []).append(
            {"id": event_id, "node_id": f"E_{event_id}", "event": "labeled",
             "actor": {"login": login, "type": "User"},
             "label": {"name": workflow.approval_label}, "created_at": created}
        )

    async def test_discovers_idempotently_excludes_pr_and_tracks_close_reopen(self):
        self.issue(2); self.issue(3, pull=True)
        first = self.service.discover(self.project.id)
        second = self.service.discover(self.project.id)
        self.assertEqual(len(first), 1); self.assertEqual(first[0].id, second[0].id)
        self.remote.issues[0]["state"] = "closed"
        self.assertEqual(self.service.discover(self.project.id)[0].status, IssueWorkflowStatus.CLOSED)
        self.remote.issues[0]["state"] = "open"
        self.assertEqual(self.service.discover(self.project.id)[0].status, IssueWorkflowStatus.ACTIVE)

    async def test_inactive_project_is_rejected(self):
        self.repository.update("projects", self.project.id, {"status": "DISABLED", "updated_at": "now"})
        with self.assertRaises(ConflictError): self.service.discover(self.project.id)

    async def test_migration_preserves_m04_1_database(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        migration_dir = Path(__file__).parents[1] / "vibecoder" / "migrations"
        for version in range(1, 9):
            script = next(migration_dir.glob(f"{version:04d}_*.sql")).read_text()
            connection.executescript(script)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (version, "2026-10-30")
            )
        connection.execute(
            "INSERT INTO codex_accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("account", "legacy", "CHATGPT", "codex-home://legacy", "AVAILABLE", "now", "now"),
        )
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("project", "legacy", None, "account", "ACTIVE", "now", "now"),
        )
        connection.commit(); connection.close()
        with SQLiteRepository(legacy) as upgraded:
            self.assertEqual(upgraded.get("projects", "project")["name"], "legacy")
            self.assertEqual(
                upgraded.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                12,
            )

    async def test_completed_bootstrap_issue_is_excluded(self):
        bootstrap_issue = self.issue(1, title="Bootstrap")
        self.issue(2)
        self.repository.insert(
            "bootstrap_workflows",
            {"id": "bootstrap", "project_id": self.project.id, "repository_id": self.repo.id,
             "github_issue_id": bootstrap_issue["id"], "github_issue_node_id": bootstrap_issue["node_id"],
             "issue_number": 1, "issue_url": bootstrap_issue["html_url"],
             "principal_agent_id": self.main.id, "status": "APPROVED",
             "approval_label": "status:approved", "approved_by": "alice",
             "approved_event_id": 1, "approved_at": "2026-10-29T00:00:00Z",
             "frozen_specification": "approved", "last_polled_at": None,
             "created_at": "2026-10-29T00:00:00Z", "updated_at": "2026-10-29T00:00:00Z"},
        )
        workflows = self.service.discover(self.project.id)
        self.assertEqual([item.issue_number for item in workflows], [2])

    async def test_authorized_comment_routes_main_and_own_or_unauthorized_do_not_execute(self):
        workflow = self.discover_one()
        self.comment(workflow, "later", cid=5002, created="2026-10-30T10:00:02Z")
        self.comment(workflow, "/agent main\nfirst", cid=5001, created="2026-10-30T10:00:01Z")
        self.comment(workflow, "intrusion", login="mallory", cid=5003)
        outcome = await self.service.process_round(workflow.id)
        self.assertEqual(outcome.status, "COMPLETED")
        rows = self.service.list_comments(workflow.id)
        self.assertEqual([row["github_comment_id"] for row in rows[:2]], [5001, 5002])
        self.assertEqual(rows[-1]["status"], IssueCommentStatus.REJECTED.value)
        self.assertIn("first", self.executor_state["prompts"][0])
        self.assertNotIn("/agent main", self.executor_state["prompts"][0])
        self.assertIn("```vibecoder-plan", self.executor_state["prompts"][0])
        self.assertIn('"assigned_agent":"logical-agent-id"', self.executor_state["prompts"][0])

    async def test_specialist_consultation_reuses_issue_session_and_suspension_rejects(self):
        specialist = self.add_specialist()
        workflow = self.discover_one()
        self.comment(workflow, "/agent testing\nreview this", cid=5001)
        await self.service.process_round(workflow.id)
        self.comment(workflow, "/agent testing\ncontinue", cid=5002)
        await self.service.process_round(workflow.id)
        self.assertEqual(self.executor_state["threads"], [None, "thread-1"])
        self.specialists.suspend(specialist.agent_id, actor="operator", authorized=True)
        self.comment(workflow, "/agent testing\nnew", cid=5003)
        self.service.poll(workflow.id)
        self.assertEqual(self.service.list_comments(workflow.id)[-1]["status"], "REJECTED")

    async def test_publication_failure_retries_without_second_codex_turn(self):
        workflow = self.discover_one(); self.comment(workflow, "question", cid=5001)
        original = self.remote.create_comment
        calls = 0
        def fail_once(*args, **kwargs):
            nonlocal calls; calls += 1
            if calls == 1: raise GitHubTransientError("timeout")
            return original(*args, **kwargs)
        self.remote.create_comment = fail_once
        first = await self.service.process_round(workflow.id)
        second = await self.service.process_round(workflow.id)
        self.assertEqual(first.status, "RESPONSE_PENDING")
        self.assertEqual(second.status, "COMPLETED")
        self.assertEqual(len(self.executor_state["prompts"]), 1)

    async def test_recovery_publishes_persisted_completed_result_without_reexecution(self):
        workflow = self.discover_one(); self.comment(workflow, "question", cid=5001)
        original = self.remote.create_comment
        self.remote.create_comment = lambda *a, **k: (_ for _ in ()).throw(GitHubTransientError("timeout"))
        await self.service.process_round(workflow.id)
        self.remote.create_comment = original
        outcomes = self.service.recover(workflow.id)
        self.assertEqual(outcomes[0].status, "COMPLETED")
        self.assertEqual(len(self.executor_state["prompts"]), 1)

    async def test_main_structured_plan_block_becomes_validated_proposal(self):
        self.add_specialist(); workflow = self.discover_one()
        self.executor_state["reply"] = (
            "Propuesta legible\n\n```vibecoder-plan\n"
            + __import__("json").dumps(self.plan()) + "\n```"
        )
        self.comment(workflow, "prepare a plan", cid=5001)
        await self.service.process_round(workflow.id)
        plans = self.service.list_plans(workflow.id)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].status, PlanRevisionStatus.PROPOSED)

    async def test_plan_validation_hash_revision_and_cycles(self):
        self.add_specialist(); workflow = self.discover_one()
        first = self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id)
        same = self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id)
        self.assertEqual(first.id, same.id)
        second = self.service.propose_plan(workflow.id, self.plan(suffix=" v2"), proposed_by_agent_id=self.main.id)
        self.assertEqual(second.revision, 2)
        self.assertEqual(self.service.get_plan(first.id).status, PlanRevisionStatus.SUPERSEDED)
        cyclic = self.plan(); cyclic["tasks"][0]["dependencies"] = ["tests"]
        with self.assertRaises(ValidationError):
            self.service.propose_plan(workflow.id, cyclic, proposed_by_agent_id=self.main.id)
        missing = self.plan(agent="unknown")
        with self.assertRaises(Exception):
            self.service.propose_plan(workflow.id, missing, proposed_by_agent_id=self.main.id)

    async def test_old_label_does_not_approve_new_revision_and_event_is_not_reused(self):
        self.add_specialist(); workflow = self.discover_one()
        self.approve_event(workflow, 8000, created="2026-10-27T09:00:00Z")
        revision = self.service.publish_plan(
            self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id).id
        )
        with self.assertRaises(ConflictError): self.service.check_approval(revision.id)
        self.approve_event(workflow, 8001)
        approved = self.service.check_approval(revision.id)
        self.assertEqual(approved.status, PlanRevisionStatus.APPROVED)
        newer = self.service.publish_plan(
            self.service.propose_plan(workflow.id, self.plan(suffix=" v2"), proposed_by_agent_id=self.main.id).id
        )
        with self.assertRaises(ConflictError): self.service.check_approval(newer.id)
        self.approve_event(workflow, 8002, created="2026-10-30T13:00:00Z")
        self.assertEqual(self.service.check_approval(newer.id).approved_event_id, 8002)

    async def test_modified_plan_payload_cannot_use_original_hash_or_approval(self):
        self.add_specialist(); workflow = self.discover_one()
        revision = self.service.publish_plan(
            self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id).id
        )
        self.repository.update(
            "plan_revisions", revision.id,
            {"plan_json": __import__("json").dumps(self.plan(suffix=" tampered")),
             "updated_at": "2026-10-30T11:00:00Z"},
        )
        self.approve_event(workflow, 8100)
        with self.assertRaises(ConflictError): self.service.check_approval(revision.id)

    async def test_materialization_is_idempotent_persistent_and_worker_cannot_execute(self):
        self.add_specialist(); workflow = self.discover_one()
        revision = self.service.publish_plan(
            self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id).id
        )
        self.approve_event(workflow, 9001)
        approved = self.service.check_approval(revision.id)
        first = self.service.materialize(approved.id); second = self.service.materialize(approved.id)
        self.assertEqual([task.id for task in first], [task.id for task in second])
        self.assertTrue(all(task.status is TaskStatus.PENDING for task in first))
        self.assertEqual(self.repository.list("execution_attempts"), [])
        with self.assertRaises(ConflictError):
            self.core.start_attempt(first[0].id)
        with self.assertRaises(ConflictError):
            self.core.transition_task(first[0].id, TaskStatus.RUNNING)
        worker = LocalWorker(self.core, {self.account.id: self.runtime})
        self.assertIsNone(worker.select_task())
        with self.assertRaises(ConflictError):
            await worker.run_task(first[0].id)
        self.assertEqual(len(self.repository.list("planned_task_dependencies")), 1)
        self.repository.close(); self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository); self.github = GitHubRegistryService(self.core, self.remote)
        self.service = self.make_service()
        self.assertEqual(len(self.service.materialize(approved.id)), 2)

    async def test_agent_configuration_change_after_approval_blocks_materialization(self):
        specialist = self.add_specialist(); workflow = self.discover_one()
        revision = self.service.publish_plan(
            self.service.propose_plan(workflow.id, self.plan(), proposed_by_agent_id=self.main.id).id
        )
        self.approve_event(workflow, 9002); approved = self.service.check_approval(revision.id)
        self.specialists.suspend(specialist.agent_id, actor="operator", authorized=True)
        with self.assertRaises(ConflictError): self.service.materialize(approved.id)
        self.assertEqual(self.service.get_workflow(workflow.id).status, IssueWorkflowStatus.BLOCKED)


if __name__ == "__main__":
    unittest.main()
