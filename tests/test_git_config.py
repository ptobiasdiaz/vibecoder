from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.test_github import FakeGitHubClient
from vibecoder import ConflictError, GitHubRegistryService, OrchestratorCore, SQLiteRepository
from vibecoder.bootstrap import BootstrapService
from vibecoder.domain import (
    BootstrapStatus,
    GitConfigurationStatus,
    GitInitializationStrategy,
    TechnicalCommitPolicy,
)
from vibecoder.errors import ValidationError
from vibecoder.git_config import GitConfigPayload, GitConfigurationService


class GitConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "state.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("alpha", self.account.id)
        self.remote = FakeGitHubClient()
        self.github = GitHubRegistryService(self.core, self.remote)
        self.repo = self.github.register_repository(self.project.id, "acme", "alpha")
        self.bootstrap = BootstrapService(
            self.core, self.github, {}, authorized_users=("alice",)
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def start(self):
        return self.bootstrap.start(self.project.id)

    def add_comment(self, workflow, body: str, *, comment_id: int = 7001, login: str = "alice"):
        item = {
            "id": comment_id,
            "node_id": f"GC_{comment_id}",
            "body": body,
            "html_url": f"https://github.test/comment/{comment_id}",
            "user": {"login": login, "type": "User"},
            "created_at": f"2026-10-29T10:00:{comment_id % 60:02d}Z",
            "updated_at": f"2026-10-29T10:00:{comment_id % 60:02d}Z",
        }
        self.remote.comments.setdefault(workflow.issue_number, []).append(item)
        return item

    def confirm(self, workflow, *, main_branch: str | None = None, comment_id: int = 7001):
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        payload = self.bootstrap.git_configurations._payload(proposal).model_dump(mode="json")
        if main_branch:
            payload["main_branch"] = main_branch
        payload["confirmation"] = "confirmed"
        body = "```vibecoder-git-config\n" + json.dumps(payload) + "\n```"
        self.add_comment(workflow, body, comment_id=comment_id)
        self.bootstrap.poll(workflow.id)
        return self.bootstrap.git_configurations.latest(
            workflow.id, GitConfigurationStatus.CONFIRMED
        )

    def label(self, workflow, *, login: str = "alice", event_id: int = 9001) -> None:
        issue = self.remote.get_issue("acme", "alpha", workflow.issue_number)
        issue["labels"] = [self.remote.labels[workflow.approval_label]]
        self.remote.events.setdefault(workflow.issue_number, []).append(
            {
                "id": event_id,
                "node_id": f"EV_{event_id}",
                "event": "labeled",
                "actor": {"login": login, "type": "User"},
                "label": {"name": workflow.approval_label},
                "created_at": "2026-10-29T11:00:00Z",
            }
        )
        self.bootstrap.poll(workflow.id)

    def test_empty_repository_gets_explicit_main_technical_commit_and_pr(self) -> None:
        workflow = self.start()
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        self.assertEqual(proposal.status, GitConfigurationStatus.PROPOSED)
        self.assertEqual(proposal.main_branch, "main")
        self.assertFalse(proposal.base_branch_exists)
        self.assertEqual(
            proposal.initialization_strategy, GitInitializationStrategy.TECHNICAL_BASE_COMMIT
        )
        self.assertEqual(proposal.technical_commit_policy, TechnicalCommitPolicy.CREATE_MINIMAL)
        self.assertEqual(set(proposal.authorized_operations), {
            "CREATE_TECHNICAL_BASE", "PUBLISH_BOOTSTRAP_BRANCH", "CREATE_PULL_REQUEST"
        })

    def test_existing_repository_preserves_default_branch_and_head(self) -> None:
        self.remote.empty = False
        self.remote.repository["default_branch"] = "trunk"
        workflow = self.start()
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        self.assertEqual(proposal.main_branch, "trunk")
        self.assertTrue(proposal.base_branch_exists)
        self.assertEqual(proposal.observed_head_sha, "a" * 40)
        self.assertEqual(proposal.initialization_strategy, GitInitializationStrategy.EXISTING_BASE)
        self.assertNotIn("CREATE_TECHNICAL_BASE", proposal.authorized_operations)

    def test_authorized_human_can_modify_branch_and_confirm(self) -> None:
        workflow = self.start()
        confirmed = self.confirm(workflow, main_branch="stable")
        self.assertEqual(confirmed.main_branch, "stable")
        self.assertEqual(confirmed.status, GitConfigurationStatus.CONFIRMED)
        self.assertEqual(confirmed.source_comment_id, 7001)

    def test_agent_prompt_contains_current_proposal_and_forbids_self_confirmation(self) -> None:
        workflow = self.start()
        self.add_comment(workflow, "Explícame alternativas", comment_id=7100)
        comment = self.bootstrap.poll(workflow.id)[0]
        prompt = self.bootstrap._prompt(comment)
        self.assertIn("vibecoder-git-config", prompt)
        self.assertIn("no la declares", prompt)

    def test_proposal_is_not_approval_and_label_without_confirmation_stays_pending(self) -> None:
        workflow = self.start()
        self.label(workflow)
        self.assertEqual(
            self.bootstrap.get_workflow(workflow.id).status,
            BootstrapStatus.APPROVAL_PENDING,
        )
        self.assertIsNone(self.bootstrap.git_configurations.approved(workflow.id))

    def test_valid_attributed_approval_freezes_config_with_specification(self) -> None:
        workflow = self.start()
        confirmed = self.confirm(workflow)
        self.label(workflow)
        approved = self.bootstrap.git_configurations.approved(workflow.id)
        frozen = self.bootstrap.get_workflow(workflow.id)
        self.assertEqual(frozen.status, BootstrapStatus.APPROVED)
        self.assertEqual(approved.id, confirmed.id)
        self.assertEqual(approved.approved_by, "alice")
        self.assertEqual(approved.approved_event_id, 9001)
        self.assertEqual(len(approved.specification_hash), 64)
        self.assertIn("vibecoder-git-config", frozen.frozen_specification)

    def test_unauthorized_label_cannot_approve_confirmed_config(self) -> None:
        workflow = self.start()
        self.confirm(workflow)
        self.label(workflow, login="mallory")
        self.assertIsNone(self.bootstrap.git_configurations.approved(workflow.id))

    def test_invalid_or_ambiguous_blocks_are_rejected(self) -> None:
        workflow = self.start()
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        block = self.bootstrap.git_configurations.render(proposal, confirmation="confirmed")
        self.add_comment(workflow, block + "\n\n" + block)
        comments = self.bootstrap.poll(workflow.id)
        self.assertEqual(comments[-1].status.value, "REJECTED")
        self.assertIn("ambiguo", comments[-1].rejection_reason)

    def test_inconsistent_empty_repository_configuration_is_rejected(self) -> None:
        workflow = self.start()
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        payload = self.bootstrap.git_configurations._payload(proposal).model_dump(mode="json")
        payload["technical_commit_policy"] = "NONE"
        payload["confirmation"] = "confirmed"
        self.add_comment(
            workflow,
            "```vibecoder-git-config\n" + json.dumps(payload) + "\n```",
        )
        comment = self.bootstrap.poll(workflow.id)[-1]
        self.assertEqual(comment.status.value, "REJECTED")

    def test_hash_is_canonical_reproducible_and_confirmation_is_not_hashed(self) -> None:
        workflow = self.start()
        proposal = self.bootstrap.git_configurations.latest(workflow.id)
        payload = self.bootstrap.git_configurations._payload(proposal)
        first = GitConfigurationService.hash_payload(payload)
        second = GitConfigurationService.hash_payload(
            GitConfigPayload.model_validate(json.loads(GitConfigurationService.canonical_json(payload)))
        )
        self.assertEqual(first, second)
        reordered = payload.model_copy(
            update={"authorized_operations": list(reversed(payload.authorized_operations))}
        )
        self.assertEqual(first, GitConfigurationService.hash_payload(reordered))

    def test_approved_configuration_cannot_be_changed_after_approval(self) -> None:
        workflow = self.start()
        self.confirm(workflow)
        self.label(workflow)
        with self.assertRaises(ConflictError):
            self.bootstrap.git_configurations.create_revision(
                workflow.id,
                self.bootstrap.git_configurations._payload(
                    self.bootstrap.git_configurations.approved(workflow.id)
                ),
                GitConfigurationStatus.PROPOSED,
            )

    def test_configuration_survives_restart_and_confirmation_is_idempotent(self) -> None:
        workflow = self.start()
        confirmed = self.confirm(workflow)
        repeated = self.bootstrap.git_configurations.confirm_block(
            workflow.id,
            self.bootstrap.git_configurations.render(confirmed, confirmation="confirmed"),
            author="alice",
            comment_id=7001,
        )
        self.assertEqual(confirmed.id, repeated.id)
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        recovered = GitConfigurationService._from_row(
            self.repository.get("git_configurations", confirmed.id)
        )
        self.assertEqual(recovered.configuration_hash, confirmed.configuration_hash)

    def test_remote_drift_detects_new_commits_and_default_branch_change(self) -> None:
        workflow = self.start()
        confirmed = self.confirm(workflow)
        self.remote.empty = False
        self.remote.repository["default_branch"] = "main"
        check = self.bootstrap.git_configurations.check_remote(confirmed.id)
        self.assertFalse(check["compatible"])
        self.assertIn("estado vacío/con commits", check["mismatches"])
        self.assertIn("rama por defecto", check["mismatches"])

    def test_remote_drift_detects_repository_identity_and_head_change(self) -> None:
        self.remote.empty = False
        self.remote.repository["default_branch"] = "main"
        workflow = self.start()
        confirmed = self.confirm(workflow)
        self.remote.repository["id"] = 999
        self.remote.get_branch = lambda owner, name, branch: {  # type: ignore[method-assign]
            "name": branch, "commit": {"sha": "b" * 40}
        }
        check = self.bootstrap.git_configurations.check_remote(confirmed.id)
        self.assertIn("identidad del repositorio", check["mismatches"])
        self.assertIn("HEAD remoto", check["mismatches"])

    def test_operations_are_exactly_the_structured_authorization(self) -> None:
        workflow = self.start()
        self.confirm(workflow)
        self.label(workflow)
        approved = self.bootstrap.git_configurations.approved(workflow.id)
        self.assertEqual(set(approved.authorized_operations), {
            "CREATE_TECHNICAL_BASE", "PUBLISH_BOOTSTRAP_BRANCH", "CREATE_PULL_REQUEST"
        })

    def test_configurations_are_isolated_between_projects(self) -> None:
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
        second_repo = self.github.register_repository(second_project.id, "acme", "beta")
        second = self.bootstrap.start(second_project.id)
        first_config = self.bootstrap.git_configurations.latest(first.id)
        second_config = self.bootstrap.git_configurations.latest(second.id)
        self.assertNotEqual(first_config.workflow_id, second_config.workflow_id)
        self.assertEqual(second_config.repository_id, second_repo.id)


if __name__ == "__main__":
    unittest.main()
