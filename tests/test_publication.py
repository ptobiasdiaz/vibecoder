from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from tests.test_generation import GeneratingExecutor, git
from tests.test_github import FakeGitHubClient
from vibecoder.bootstrap import BootstrapService
from vibecoder.codex_executor import AccountRuntimeConfig
from vibecoder.domain import ProjectStatus, PublicationStatus
from vibecoder.errors import ConflictError
from vibecoder.generation import GenerationService
from vibecoder.git import GitAdapter, GitError
from vibecoder.github import GitHubRepositoryNotFound, GitHubTransientError
from vibecoder.github_service import GitHubRegistryService
from vibecoder.publication import PublicationService
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


class PublicationRemote(FakeGitHubClient):
    def __init__(self) -> None:
        super().__init__()
        self.branches: dict[str, str] = {}
        self.pulls: list[dict[str, Any]] = []
        self.pr_writes = 0
        self.lose_pr_response = False

    def is_repository_empty(self, owner: str, name: str) -> bool:
        return not self.branches

    def get_branch(self, owner: str, name: str, branch: str) -> Mapping[str, Any]:
        if branch not in self.branches:
            raise GitHubRepositoryNotFound("missing", status_code=404)
        return {"name": branch, "commit": {"sha": self.branches[branch]}}

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "all",
        head: str | None = None, base: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        result = self.pulls
        if state != "all": result = [item for item in result if item["state"] == state]
        if head: result = [item for item in result if f"{owner}:{item['head']['ref']}" == head]
        if base: result = [item for item in result if item["base"]["ref"] == base]
        return result

    def get_pull_request(self, owner: str, name: str, number: int) -> Mapping[str, Any]:
        return next(item for item in self.pulls if item["number"] == number)

    def create_pull_request(
        self, owner: str, name: str, title: str, body: str, head: str, base: str,
    ) -> Mapping[str, Any]:
        self.pr_writes += 1
        item = {
            "id": 8000 + self.pr_writes, "node_id": f"PR_{self.pr_writes}",
            "number": self.pr_writes, "title": title, "body": body, "state": "open",
            "html_url": f"https://github.test/{owner}/{name}/pull/{self.pr_writes}",
            "draft": False, "merged": False, "merge_commit_sha": None, "mergeable": True,
            "head": {"ref": head, "sha": self.branches[head]},
            "base": {"ref": base, "sha": self.branches[base]},
        }
        self.pulls.append(item)
        if self.lose_pr_response:
            self.lose_pr_response = False
            raise GitHubTransientError("response lost")
        return item


class PublishingGit(GitAdapter):
    def __init__(self, remote: PublicationRemote):
        super().__init__()
        self.remote = remote
        self.pushes: list[tuple[str, str]] = []
        self.fail_after_push = False

    def push_branch(self, workspace: Path, local_ref: str, remote_branch: str, *, token: str | None) -> None:
        self.pushes.append((local_ref, remote_branch))
        super().push_branch(workspace, local_ref, remote_branch, token=None)
        sha = git("rev-parse", local_ref, cwd=workspace)
        self.remote.branches[remote_branch] = sha
        self.remote.empty = False
        if self.remote.repository["default_branch"] is None:
            self.remote.repository["default_branch"] = remote_branch
        if self.fail_after_push:
            self.fail_after_push = False
            raise GitError("connection lost")


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = SQLiteRepository(self.root / "state.sqlite3")
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("project", self.account.id)
        self.remote = PublicationRemote()
        self.github = GitHubRegistryService(self.core, self.remote)
        self.github.register_repository(self.project.id, "acme", "alpha")
        self.bootstrap = BootstrapService(self.core, self.github, {}, authorized_users=("alice",))
        self.workflow = self.bootstrap.start(self.project.id)
        self.bare = self.root / "remote.git"
        git("init", "--bare", str(self.bare))
        proposal = self.bootstrap.git_configurations.latest(self.workflow.id)
        self.remote.comments.setdefault(self.workflow.issue_number, []).append({
            "id": 4800, "node_id": "GC_4800",
            "body": self.bootstrap.git_configurations.render(proposal, confirmation="confirmed"),
            "html_url": "https://github.test/config", "user": {"login": "alice", "type": "User"},
            "created_at": "2026-10-29T11:59:00Z", "updated_at": "2026-10-29T11:59:00Z",
        })
        self.bootstrap.poll(self.workflow.id)
        issue = self.remote.get_issue("acme", "alpha", self.workflow.issue_number)
        issue["labels"] = [self.remote.labels[self.workflow.approval_label]]
        self.remote.events.setdefault(self.workflow.issue_number, []).append({
            "id": 9001, "event": "labeled", "actor": {"login": "alice", "type": "User"},
            "label": {"name": self.workflow.approval_label}, "created_at": "2026-10-29T12:00:00Z",
        })
        self.bootstrap.poll(self.workflow.id)
        state: dict[str, Any] = {}
        runtime = AccountRuntimeConfig(account_id=self.account.id, codex_home=self.root, cwd=self.root)
        generation_service = GenerationService(
            self.core, {self.account.id: runtime}, self.root / "workspaces",
            executor_factory=lambda config: GeneratingExecutor(config, state),
        )
        prepared = generation_service.prepare(self.project.id, remote_url=str(self.bare))
        self.generation = await generation_service.generate(prepared.id)
        self.git_adapter = PublishingGit(self.remote)
        self.service = PublicationService(self.core, self.github, git=self.git_adapter)

    async def asyncTearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def prepared(self):
        return self.service.prepare(self.project.id)

    def advance_to_pr(self):
        publication = self.prepared()
        for _ in range(3): publication = self.service.next(publication.id, token="secret-token")
        return publication

    async def test_empty_repository_initializes_approved_base_and_publishes_exact_sha(self) -> None:
        publication = self.prepared()
        self.assertEqual(publication.base_branch, "main")
        publication = self.service.next(publication.id, token="secret-token")
        self.assertEqual(publication.status, PublicationStatus.BASE_READY)
        self.assertEqual(self.remote.branches["main"], publication.base_sha)
        publication = self.service.next(publication.id, token="secret-token")
        self.assertEqual(publication.status, PublicationStatus.BRANCH_PUBLISHED)
        self.assertEqual(self.remote.branches[publication.published_branch], publication.published_sha)
        self.assertNotEqual(publication.base_sha, publication.published_sha)

    async def test_prepare_rejects_modified_workspace_and_wrong_hash(self) -> None:
        (Path(self.generation.workspace_path) / "dirty").write_text("x")
        with self.assertRaises(ConflictError): self.prepared()

    async def test_prepare_rejects_incomplete_generation_and_unapproved_config(self) -> None:
        self.repository.update("generation_workflows", self.generation.id,
                               {"status": "BLOCKED", "updated_at": self.generation.updated_at})
        with self.assertRaises(ConflictError): self.prepared()
        self.repository.update("generation_workflows", self.generation.id,
                               {"status": "READY_TO_PUBLISH", "updated_at": self.generation.updated_at})
        self.repository.update("git_configurations", self.generation.git_configuration_id,
                               {"status": "CONFIRMED", "updated_at": self.generation.updated_at})
        with self.assertRaises(ConflictError): self.prepared()

    async def test_push_uncertain_reconciles_without_second_push(self) -> None:
        publication = self.prepared()
        self.git_adapter.fail_after_push = True
        unknown = self.service.next(publication.id, token="secret-token")
        self.assertEqual(unknown.status, PublicationStatus.UNKNOWN)
        recovered = self.service.reconcile(unknown.id)
        self.assertEqual(recovered.status, PublicationStatus.BASE_READY)
        self.assertEqual(len(self.git_adapter.pushes), 1)

    async def test_existing_incompatible_branch_blocks_without_force(self) -> None:
        publication = self.prepared()
        self.remote.branches["main"] = "f" * 40
        blocked = self.service.next(publication.id, token="secret-token")
        self.assertEqual(blocked.status, PublicationStatus.BLOCKED)
        self.assertEqual(self.git_adapter.pushes, [])

    async def test_pull_request_is_idempotent_and_contains_traceability(self) -> None:
        publication = self.advance_to_pr()
        self.assertEqual(publication.status, PublicationStatus.WAITING_FOR_MERGE)
        self.assertEqual(self.remote.pr_writes, 1)
        body = self.remote.pulls[0]["body"]
        self.assertIn(self.generation.id, body)
        self.assertIn(self.generation.specification_hash, body)
        repeated = self.service.reconcile(publication.id)
        self.assertEqual(repeated.pull_request_number, publication.pull_request_number)
        self.assertEqual(self.remote.pr_writes, 1)

    async def test_lost_pr_response_is_reconciled_without_duplicate(self) -> None:
        publication = self.prepared()
        publication = self.service.next(publication.id, token="secret-token")
        publication = self.service.next(publication.id, token="secret-token")
        self.remote.lose_pr_response = True
        unknown = self.service.next(publication.id, token="secret-token")
        self.assertEqual(unknown.status, PublicationStatus.UNKNOWN)
        recovered = self.service.reconcile(unknown.id)
        self.assertEqual(recovered.status, PublicationStatus.WAITING_FOR_MERGE)
        self.assertEqual(self.remote.pr_writes, 1)

    async def test_closed_without_merge_blocks(self) -> None:
        publication = self.advance_to_pr()
        self.remote.pulls[0]["state"] = "closed"
        blocked = self.service.check_merge(publication.id)
        self.assertEqual(blocked.status, PublicationStatus.BLOCKED)

    async def test_incompatible_existing_pr_blocks(self) -> None:
        publication = self.prepared()
        publication = self.service.next(publication.id, token="secret-token")
        publication = self.service.next(publication.id, token="secret-token")
        self.remote.pulls.append({
            "id": 999, "node_id": "PR_foreign", "number": 99, "title": "Foreign",
            "body": "not vibecoder", "state": "open", "html_url": "https://github.test/pr/99",
            "draft": False, "merged": False, "merge_commit_sha": None,
            "head": {"ref": publication.published_branch, "sha": publication.published_sha},
            "base": {"ref": publication.base_branch, "sha": publication.base_sha},
        })
        blocked = self.service.next(publication.id, token="secret-token")
        self.assertEqual(blocked.status, PublicationStatus.BLOCKED)
        self.assertEqual(self.remote.pr_writes, 0)

    async def test_merge_without_verifiable_sha_blocks_activation(self) -> None:
        publication = self.advance_to_pr()
        self.remote.pulls[0]["state"] = "closed"
        self.remote.pulls[0]["merged"] = True
        self.remote.pulls[0]["merge_commit_sha"] = None
        blocked = self.service.check_merge(publication.id)
        self.assertEqual(blocked.status, PublicationStatus.BLOCKED)
        self.assertEqual(self.core.get_project(self.project.id).status, ProjectStatus.DESIGNING)

    async def test_verified_merge_activates_idempotently_and_survives_restart(self) -> None:
        publication = self.advance_to_pr()
        self.remote.pulls[0].update({"state": "closed", "merged": True, "merge_commit_sha": "c" * 40})
        self.remote.branches["main"] = "c" * 40
        merged = self.service.check_merge(publication.id)
        self.assertEqual(merged.status, PublicationStatus.MERGED)
        activated = self.service.activate(merged.id)
        self.assertEqual(activated.status, PublicationStatus.ACTIVATED)
        self.assertEqual(self.core.get_project(self.project.id).status, ProjectStatus.ACTIVE)
        self.assertEqual(self.service.activate(activated.id).id, activated.id)
        self.repository.close()
        self.repository = SQLiteRepository(self.root / "state.sqlite3")
        self.core = OrchestratorCore(self.repository)
        self.service = PublicationService(self.core, GitHubRegistryService(self.core, self.remote), git=self.git_adapter)
        self.assertEqual(self.service.get(activated.id).status, PublicationStatus.ACTIVATED)

    async def test_operation_not_authorized_is_rejected(self) -> None:
        config = self.repository.get("git_configurations", self.generation.git_configuration_id)
        payload = __import__("json").loads(config["config_json"])
        payload["authorized_operations"].remove("CREATE_PULL_REQUEST")
        self.repository.update("git_configurations", str(config["id"]),
                               {"config_json": __import__("json").dumps(payload), "updated_at": config["updated_at"]})
        with self.assertRaises(ConflictError): self.prepared()


if __name__ == "__main__":
    unittest.main()
