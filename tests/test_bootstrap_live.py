"""M03.2 live por etapas; siempre desactivado y con escritura explícita."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

from vibecoder.bootstrap import BootstrapService
from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.runtime_config import load_account_configs
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


def _service(*, accounts: bool):
    repository = SQLiteRepository(Path(os.environ["M032_DATABASE"]).resolve())
    client = GitHubHttpClient(GitHubConfig(token=os.environ["M032_GITHUB_TOKEN"]))
    core = OrchestratorCore(repository)
    github = GitHubRegistryService(core, client)
    configs = load_account_configs(os.environ["M032_ACCOUNTS"]) if accounts else {}
    users = [value for value in os.environ["M032_AUTHORIZED_USERS"].split(",") if value]
    return repository, client, BootstrapService(
        core, github, configs, authorized_users=users
    )


@unittest.skipUnless(
    os.environ.get("M032_LIVE_WRITE") == "I_UNDERSTAND_THIS_WRITES_TO_GITHUB",
    "M03.2 live write requires explicit opt-in",
)
class BootstrapLiveTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("M032_LIVE_STAGE") == "start", "live stage=start")
    def test_start_bootstrap_issue(self) -> None:
        repository, client, service = _service(accounts=False)
        try:
            workflow = service.start(os.environ["M032_PROJECT_ID"])
            self.assertTrue(workflow.issue_url)
        finally:
            client.close()
            repository.close()

    @unittest.skipUnless(os.environ.get("M032_LIVE_STAGE") == "process", "live stage=process")
    def test_process_one_human_intervention(self) -> None:
        repository, client, service = _service(accounts=True)
        try:
            workflow = service.get_project_workflow(os.environ["M032_PROJECT_ID"])
            outcome = asyncio.run(service.process_round(workflow.id))
            self.assertEqual(outcome.status, "COMPLETED", outcome.detail)
        finally:
            client.close()
            repository.close()
