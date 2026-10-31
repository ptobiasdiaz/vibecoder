"""Prueba live M04.3 por etapas; nunca se activa por defecto."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.runtime_config import load_account_configs
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository
from vibecoder.tasks import TaskExecutionService


@unittest.skipUnless(
    os.environ.get("M043_LIVE") == "I_UNDERSTAND_M043_USES_LOCAL_WORKSPACES",
    "M04.3 live requiere opt-in explícito",
)
class TaskExecutionLiveTests(unittest.TestCase):
    def run_stage(self, expected: str):
        self.assertEqual(os.environ.get("M043_LIVE_STAGE"), expected)
        database = Path(os.environ["M043_DATABASE"]).resolve()
        project_workspace = Path(os.environ["M043_PROJECT_WORKSPACE"]).resolve()
        workspace_root = Path(os.environ["M043_WORKSPACE_ROOT"]).resolve()
        repository = SQLiteRepository(database)
        client = GitHubHttpClient(GitHubConfig(token=os.environ["VIBECODER_GITHUB_TOKEN"]))
        try:
            core = OrchestratorCore(repository)
            configs = load_account_configs(os.environ["M043_ACCOUNTS"])
            service = TaskExecutionService(
                core, GitHubRegistryService(core, client), configs, workspace_root,
                project_workspace=project_workspace,
                orchestrator_root=Path(__file__).parents[1],
            )
            task_id = os.environ["M043_TASK_ID"]
            if expected == "eligible": return service.eligible(task_id)
            if expected == "prepare":
                return service.prepare(task_id, allowed_paths=os.environ["M043_ALLOWED_PATHS"].split(","))
            execution_id = os.environ["M043_EXECUTION_ID"]
            if expected == "run":
                self.assertEqual(os.environ.get("M043_LIVE_CODEX"), "I_ACCEPT_CODEX_QUOTA_USAGE")
                return asyncio.run(service.run(execution_id))
            if expected == "validate": return service.validate(execution_id)
            if expected == "commit": return service.commit(execution_id)
            if expected in {"publish", "create-pr"}:
                self.assertEqual(os.environ.get("M043_LIVE_GITHUB_WRITE"), "I_AUTHORIZE_GITHUB_WRITES")
                if expected == "publish":
                    return service.publish(
                        execution_id, token=os.environ["VIBECODER_GITHUB_TOKEN"], authorized=True
                    )
                return service.create_pr(execution_id, authorized=True)
            if expected == "reconcile": return service.reconcile(execution_id)
            raise AssertionError(expected)
        finally:
            client.close()
            repository.close()

    def test_01_eligible(self):
        self.assertTrue(self.run_stage("eligible")["eligible"])

    def test_02_prepare_workspace(self):
        self.assertEqual(self.run_stage("prepare").status.value, "WORKSPACE_READY")

    def test_03_run_codex(self):
        self.assertEqual(self.run_stage("run").status.value, "GENERATED")

    def test_04_validate(self):
        self.assertEqual(self.run_stage("validate").status.value, "VALIDATED")

    def test_05_commit(self):
        self.assertEqual(self.run_stage("commit").status.value, "COMMITTED")

    def test_06_publish_branch(self):
        self.assertEqual(self.run_stage("publish").status.value, "BRANCH_PUBLISHED")

    def test_07_create_pr(self):
        self.assertEqual(self.run_stage("create-pr").status.value, "PR_OPEN")

    def test_08_reconcile_after_restart(self):
        self.assertIn(self.run_stage("reconcile").status.value, {"PR_OPEN", "MERGED"})


if __name__ == "__main__":
    unittest.main()
