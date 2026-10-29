"""M03.3.1 live por etapas; desactivado por defecto y sin operaciones Git remotas."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from vibecoder.bootstrap import BootstrapService
from vibecoder.domain import BootstrapStatus
from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


@unittest.skipUnless(
    os.environ.get("M0331_LIVE") == "I_UNDERSTAND_THIS_MAY_WRITE_AN_ISSUE_COMMENT",
    "M03.3.1 live requires explicit opt-in",
)
class GitConfigurationLiveTests(unittest.TestCase):
    def service(self):
        repository = SQLiteRepository(Path(os.environ["M0331_DATABASE"]).resolve())
        client = GitHubHttpClient(GitHubConfig(token=os.environ["M0331_GITHUB_TOKEN"]))
        core = OrchestratorCore(repository)
        github = GitHubRegistryService(core, client)
        service = BootstrapService(
            core,
            github,
            {},
            authorized_users=tuple(os.environ["M0331_AUTHORIZED_USERS"].split(",")),
        )
        return repository, client, github, service

    def test_observe_real_repository_state(self) -> None:
        repository, client, github, service = self.service()
        try:
            workflow = service.get_project_workflow(os.environ["M0331_PROJECT_ID"])
            observed = github.observe_repository_state(workflow.repository_id)
            self.assertIn("is_empty", observed)
            self.assertIn("github_id", observed)
        finally:
            client.close()
            repository.close()

    @unittest.skipUnless(os.environ.get("M0331_STAGE") == "publish", "stage=publish")
    def test_publish_proposal_on_unapproved_test_workflow(self) -> None:
        repository, client, github, service = self.service()
        try:
            workflow = service.get_project_workflow(os.environ["M0331_PROJECT_ID"])
            self.assertNotEqual(workflow.status, BootstrapStatus.APPROVED)
            proposal = service.git_configurations.ensure_default_proposal(workflow.id)
            github.create_comment(
                workflow.repository_id,
                workflow.issue_number,
                "## Propuesta de configuración Git\n\n"
                + service.git_configurations.render(proposal, confirmation="proposed"),
                idempotency_key=f"git-config-proposal:{proposal.configuration_hash}",
            )
        finally:
            client.close()
            repository.close()

    @unittest.skipUnless(os.environ.get("M0331_STAGE") == "verify", "stage=verify")
    def test_verify_human_confirmation_and_approval(self) -> None:
        repository, client, github, service = self.service()
        try:
            workflow = service.get_project_workflow(os.environ["M0331_PROJECT_ID"])
            service.poll(workflow.id)
            approved = service.git_configurations.approved(workflow.id)
            self.assertIsNotNone(approved)
            self.assertEqual(service.get_workflow(workflow.id).status, BootstrapStatus.APPROVED)
            self.assertTrue(service.git_configurations.check_remote(approved.id)["compatible"])
        finally:
            client.close()
            repository.close()


if __name__ == "__main__":
    unittest.main()
