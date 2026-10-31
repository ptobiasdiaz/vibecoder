from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vibecoder.domain import AuthType, ProjectStatus, SupervisorJobState, WorkCategory
from vibecoder.errors import ConflictError
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository
from vibecoder.supervisor import (
    Dispatcher, JobSpec, RuntimeLimits, Scheduler, Supervisor, SupervisorConfig,
    SupervisorStore, classify_error,
)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "project"; self.workspace.mkdir()
        self.repository = SQLiteRepository(root / "state.sqlite3")
        core = OrchestratorCore(self.repository)
        account = core.register_account("test", AuthType.CHATGPT, "codex-home://test")
        self.project = core.register_project("project", account.id, status=ProjectStatus.ACTIVE)
        self.config = SupervisorConfig(
            database=root / "state.sqlite3", task_workspace_root=root / "tasks",
            projects={self.project.id: {"workspace": self.workspace}},
            inference_enabled=True, publication_enabled=True,
            limits=RuntimeLimits(lease_seconds=30, execution_timeout=10),
        )
        self.store = SupervisorStore(self.repository)

    async def asyncTearDown(self):
        self.repository.close(); self.temp.cleanup()

    def spec(self, suffix="1", **values):
        data = dict(job_type="TEST", category=WorkCategory.LOCAL_WRITE,
                    source_type="ENTITY", source_id=suffix, project_id=self.project.id)
        data.update(values); return JobSpec(**data)

    async def test_scheduler_discovers_active_project_without_writing_in_dry_run(self):
        supervisor = Supervisor(self.repository, self.config, Dispatcher({}))
        before = len(self.repository.list("supervisor_jobs"))
        plan = supervisor.dry_run()
        self.assertTrue(any(row["job_type"] == "ISSUE_POLL" for row in plan))
        self.assertEqual(len(self.repository.list("supervisor_jobs")), before)

    async def test_schedule_is_idempotent_and_priority_ordered(self):
        first = self.store.schedule(self.spec(priority=50), max_attempts=3)
        second = self.store.schedule(self.spec(priority=50), max_attempts=3)
        urgent = self.store.schedule(self.spec("2", priority=1), max_attempts=3)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.store.ready()[0].id, urgent.id)

    async def test_sync_cancels_pending_intention_when_workflow_advanced(self):
        stale = self.store.schedule(self.spec(job_type="TASK_EXECUTE"), max_attempts=3)
        Scheduler(self.repository, self.config).sync(self.store)
        cancelled = self.store.get(stale.id)
        self.assertEqual(cancelled.state, SupervisorJobState.CANCELLED)
        self.assertIn("obsoleta", cancelled.next_action)

    async def test_claim_renew_finish_and_capacity(self):
        first = self.store.schedule(self.spec("1", payload={"agent_id": "a"}), max_attempts=3)
        second = self.store.schedule(self.spec("2", payload={"agent_id": "a"}), max_attempts=3)
        claimed = self.store.claim(first.id, self.config)
        self.assertEqual(claimed.state, SupervisorJobState.RUNNING)
        self.assertIsNone(self.store.claim(second.id, self.config))
        renewed = self.store.renew(first.id, str(claimed.lease_token), 60)
        self.assertIsNotNone(renewed.lease_expires_at)
        done = self.store.finish(first.id, str(claimed.lease_token))
        self.assertEqual(done.state, SupervisorJobState.SUCCEEDED)
        self.assertIsNotNone(self.store.claim(second.id, self.config))

    async def test_expired_lease_becomes_unknown_not_pending(self):
        job = self.store.schedule(self.spec(), max_attempts=3)
        claimed = self.store.claim(job.id, self.config)
        future = datetime.now(UTC) + timedelta(seconds=31)
        recovered = self.store.recover_expired(future)
        self.assertEqual(recovered[0].state, SupervisorJobState.UNKNOWN)
        self.assertEqual(recovered[0].next_action, "reconciliar workflow original")

    async def test_unknown_creates_distinct_recovery_intention(self):
        job = self.store.schedule(self.spec(), max_attempts=3)
        claimed = self.store.claim(job.id, self.config)
        self.store.fail(job.id, str(claimed.lease_token), TimeoutError(), self.config.limits)
        recovery = self.store.schedule_recoveries(max_attempts=3)
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].job_type, "RECONCILE_JOB")
        self.assertEqual(recovery[0].source_id, job.id)

    async def test_pause_is_persistent_and_blocks_claim(self):
        job = self.store.schedule(self.spec(category=WorkCategory.CODEX), max_attempts=3)
        self.store.set_pause("GLOBAL", "global", all_work=False, inference=True,
                             publication=False, actor="operator")
        self.assertIsNone(self.store.claim(job.id, self.config))
        reopened = SQLiteRepository(self.config.database)
        try: self.assertEqual(reopened.list("supervisor_controls")[0]["pause_inference"], 1)
        finally: reopened.close()

    async def test_dry_run_reports_persistent_pause(self):
        self.store.set_pause("GLOBAL", "global", all_work=False, inference=False,
                             publication=True, actor="operator", reason="review")
        job = self.store.schedule(self.spec(category=WorkCategory.REMOTE_WRITE), max_attempts=3)
        supervisor = Supervisor(self.repository, self.config, Dispatcher())
        self.assertEqual(supervisor._runtime_block(job), "review")

    async def test_scheduler_ignores_non_active_approved_plan_revision(self):
        workflow = {"id": "workflow", "project_id": self.project.id,
                    "status": "PLANNING", "active_plan_revision_id": "current"}
        revisions = [
            {"id": "old", "workflow_id": "workflow", "status": "APPROVED"},
            {"id": "current", "workflow_id": "workflow", "status": "APPROVED"},
        ]
        original_list = self.repository.list
        def fake_list(table, **criteria):
            if table == "projects": return []
            if table == "issue_workflows": return [workflow]
            if table == "plan_revisions": return revisions
            if table == "planned_tasks":
                return [{"id": f"task-{criteria['plan_revision_id']}", "materialized_task_id": None}]
            return []
        def fake_get(table, entity_id):
            if table == "issue_workflows": return workflow
            raise AssertionError((table, entity_id))
        with patch.object(self.repository, "list", side_effect=fake_list), \
             patch.object(self.repository, "get", side_effect=fake_get):
            specs = Scheduler(self.repository, self.config).plan()
        materialize = [item.source_id for item in specs if item.job_type == "PLAN_MATERIALIZE"]
        self.assertEqual(materialize, ["current"])

    async def test_scheduler_dependency_requires_verified_merge(self):
        scheduler = Scheduler(self.repository, self.config)
        planned = {"id": "planned-b", "plan_revision_id": "plan", "materialized_task_id": "task-b"}
        dependency = {"task_id": "planned-b", "depends_on_task_id": "planned-a", "plan_revision_id": "plan"}
        source = {"id": "planned-a", "materialized_task_id": "task-a"}
        with patch.object(self.repository, "find_one", side_effect=[planned, None]):
            with patch.object(self.repository, "list", return_value=[dependency]), \
                 patch.object(self.repository, "get", return_value=source):
                self.assertFalse(scheduler._dependencies_satisfied("task-b"))
        with patch.object(self.repository, "find_one", side_effect=[planned, {"status": "MERGED"}]), \
             patch.object(self.repository, "list", return_value=[dependency]), \
             patch.object(self.repository, "get", return_value=source):
            self.assertTrue(scheduler._dependencies_satisfied("task-b"))

    async def test_once_dispatches_and_persists_activity(self):
        seen = []
        dispatcher = Dispatcher({"TEST": lambda job: seen.append(job.id)})
        job = self.store.schedule(self.spec(), max_attempts=3)
        supervisor = Supervisor(self.repository, self.config, dispatcher)
        supervisor.scheduler.plan = lambda: [self.spec()]
        result = await supervisor.once()
        self.assertIn(job.id, seen)
        self.assertEqual(self.store.get(job.id).state, SupervisorJobState.SUCCEEDED)
        self.assertEqual(result["completed"], 1)
        self.assertTrue(self.repository.list("supervisor_activity"))

    async def test_transient_backoff_and_timeout_unknown(self):
        from vibecoder.github import GitHubTransientError
        job = self.store.schedule(self.spec("transient"), max_attempts=3)
        claimed = self.store.claim(job.id, self.config)
        retried = self.store.fail(job.id, str(claimed.lease_token), GitHubTransientError("later"), self.config.limits)
        self.assertEqual(retried.state, SupervisorJobState.RETRY_WAIT)
        job2 = self.store.schedule(self.spec("timeout"), max_attempts=3)
        claimed2 = self.store.claim(job2.id, self.config)
        unknown = self.store.fail(job2.id, str(claimed2.lease_token), TimeoutError(), self.config.limits)
        self.assertEqual(unknown.state, SupervisorJobState.UNKNOWN)

    async def test_authorization_error_is_not_retried(self):
        from vibecoder.github import GitHubPermissionError
        self.assertEqual(classify_error(GitHubPermissionError("no"))[:3],
                         ("authorization", False, False))

    async def test_recurring_job_returns_to_pending(self):
        job = self.store.schedule(self.spec(recurring_seconds=5), max_attempts=3)
        claimed = self.store.claim(job.id, self.config)
        result = self.store.finish(job.id, str(claimed.lease_token), recurring=True)
        self.assertEqual(result.state, SupervisorJobState.PENDING)

    async def test_invalid_handler_blocks_job(self):
        job = self.store.schedule(self.spec(), max_attempts=3)
        supervisor = Supervisor(self.repository, self.config, Dispatcher())
        supervisor.scheduler.plan = lambda: [self.spec()]
        await supervisor.once()
        self.assertEqual(self.store.get(job.id).state, SupervisorJobState.BLOCKED)

    async def test_stop_continuous_loop_orderly(self):
        supervisor = Supervisor(self.repository, self.config, Dispatcher({}))
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(.01); supervisor.stop()
        await asyncio.wait_for(task, 1)

    async def test_publication_pause_in_config_appears_in_dry_run(self):
        config = self.config.model_copy(update={"publication_enabled": False})
        scheduler = Scheduler(self.repository, config)
        job = self.store.schedule(self.spec(category=WorkCategory.REMOTE_WRITE), max_attempts=3)
        supervisor = Supervisor(self.repository, config, Dispatcher())
        self.assertEqual(supervisor._runtime_block(job), "publicaciones remotas pausadas por configuración")

    async def test_task_prepare_requires_remote_reads(self):
        config = self.config.model_copy(update={"remote_reads_enabled": False})
        job = self.store.schedule(
            self.spec(job_type="TASK_PREPARE", category=WorkCategory.LOCAL_WRITE),
            max_attempts=3,
        )
        supervisor = Supervisor(self.repository, config, Dispatcher())
        self.assertEqual(
            supervisor._runtime_block(job),
            "lecturas remotas pausadas por configuración",
        )


class ConfigTests(unittest.TestCase):
    def test_unknown_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); workspace = root / "workspace"; workspace.mkdir()
            with self.assertRaises(Exception):
                SupervisorConfig.model_validate({"database": str(root / "db"),
                    "task_workspace_root": str(root / "tasks"), "unexpected": True})

    def test_config_load_does_not_create_workspace_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / "not-created"
            path = root / "config.json"
            path.write_text(json.dumps({"database": str(root / "db.sqlite3"),
                "task_workspace_root": str(target)}))
            SupervisorConfig.load(path)
            self.assertFalse(target.exists())


if __name__ == "__main__": unittest.main()
