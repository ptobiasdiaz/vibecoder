"""Pruebas live M04.2 por etapas; desactivadas por defecto."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.issues import IssueCoordinationService
from vibecoder.runtime_config import load_account_configs
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


def service():
    repository = SQLiteRepository(Path(os.environ["M042_DATABASE"]).resolve())
    client = GitHubHttpClient(GitHubConfig(token=os.environ["VIBECODER_GITHUB_TOKEN"]))
    core = OrchestratorCore(repository)
    configs = load_account_configs(os.environ["M042_ACCOUNTS"]) if os.environ.get("M042_ACCOUNTS") else {}
    coordination = IssueCoordinationService(
        core, GitHubRegistryService(core, client), configs,
        Path(os.environ["M042_WORKSPACE"]),
        authorized_users=[os.environ["M042_AUTHORIZED_USER"]],
    )
    return repository, client, coordination


@unittest.skipUnless(
    os.environ.get("M042_LIVE_WRITE") == "I_UNDERSTAND_THIS_WRITES_TO_GITHUB",
    "M04.2 live write requiere opt-in explícito",
)
class IssueCoordinationLiveTests(unittest.TestCase):
    def run_stage(self, expected: str):
        self.assertEqual(os.environ.get("M042_LIVE_STAGE"), expected)
        repository, client, coordination = service()
        try:
            project_id = os.environ["M042_PROJECT_ID"]
            issue_number = int(os.environ["M042_ISSUE_NUMBER"])
            if expected == "discover":
                workflows = coordination.discover(project_id)
                return coordination.get_by_issue(project_id, issue_number) if workflows else None
            workflow = coordination.get_by_issue(project_id, issue_number)
            if expected == "process":
                self.assertTrue(os.environ.get("M042_ACCOUNTS"), "process consume cuota y requiere accounts.json")
                return asyncio.run(coordination.process_round(workflow.id))
            if expected == "publish-plan":
                payload = json.loads(Path(os.environ["M042_PLAN_FILE"]).read_text())
                revision = coordination.propose_plan(
                    workflow.id, payload, proposed_by_agent_id=workflow.principal_agent_id
                )
                return coordination.publish_plan(revision.id)
            revision_id = os.environ["M042_PLAN_REVISION_ID"]
            if expected == "approve": return coordination.check_approval(revision_id)
            if expected == "materialize": return coordination.materialize(revision_id)
            if expected == "recover": return coordination.recover(workflow.id)
            raise AssertionError(expected)
        finally:
            client.close(); repository.close()

    def test_discover_issue(self):
        self.assertIsNotNone(self.run_stage("discover"))

    def test_process_one_comment(self):
        self.assertEqual(self.run_stage("process").status, "COMPLETED")

    def test_publish_structured_plan(self):
        self.assertEqual(self.run_stage("publish-plan").status.value, "CONFIRMED")

    def test_verify_approval(self):
        self.assertEqual(self.run_stage("approve").status.value, "APPROVED")

    def test_materialize_without_execution(self):
        tasks = self.run_stage("materialize")
        self.assertTrue(tasks)
        self.assertTrue(all(task.status.value == "PENDING" for task in tasks))

    def test_recover_after_restart(self):
        self.run_stage("recover")


if __name__ == "__main__":
    unittest.main()
