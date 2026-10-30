"""M03.4 live por operaciones; nunca fusiona Pull Requests."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from vibecoder.domain import PublicationStatus
from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.publication import PublicationService
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


def _service():
    repository = SQLiteRepository(Path(os.environ["M034_DATABASE"]).resolve())
    client = GitHubHttpClient(GitHubConfig(token=os.environ["M034_GITHUB_TOKEN"]))
    core = OrchestratorCore(repository)
    return repository, client, PublicationService(core, GitHubRegistryService(core, client))


@unittest.skipUnless(
    os.environ.get("M034_LIVE_READ") == "I_UNDERSTAND_THIS_READS_GITHUB",
    "M03.4 live read opt-in",
)
class PublicationReadLiveTests(unittest.TestCase):
    def test_remote_status(self) -> None:
        repository, client, service = _service()
        try:
            registered = service.github.get_project_repository(os.environ["M034_PROJECT_ID"])
            identity = service.github.observe_repository_identity(registered.id)
            self.assertGreater(identity["github_id"], 0)
        finally:
            client.close()
            repository.close()


@unittest.skipUnless(
    os.environ.get("M034_LIVE_WRITE") == "I_UNDERSTAND_THIS_PUSHES_AND_CREATES_A_PR",
    "M03.4 live write opt-in",
)
class PublicationWriteLiveTests(unittest.TestCase):
    def run_stage(self, stage: str):
        self.assertEqual(os.environ.get("M034_STAGE"), stage)
        repository, client, service = _service()
        try:
            project_id = os.environ["M034_PROJECT_ID"]
            if stage == "prepare":
                return service.prepare(project_id)
            publication = service.get_project(project_id)
            if stage in {"base", "branch", "pr"}:
                return service.next(publication.id, token=os.environ["M034_GITHUB_TOKEN"])
            if stage == "merge":
                return service.check_merge(publication.id)
            if stage == "activate":
                return service.activate(publication.id)
            raise AssertionError(stage)
        finally:
            client.close()
            repository.close()

    def assert_stage(self, stage: str, expected: PublicationStatus) -> None:
        result = self.run_stage(stage)
        self.assertEqual(result.status, expected, result.blocked_reason)

    def test_prepare(self): self.assert_stage("prepare", PublicationStatus.READY)
    def test_initialize_base(self): self.assert_stage("base", PublicationStatus.BASE_READY)
    def test_publish_branch(self): self.assert_stage("branch", PublicationStatus.BRANCH_PUBLISHED)
    def test_create_pr(self): self.assert_stage("pr", PublicationStatus.WAITING_FOR_MERGE)
    def test_detect_manual_merge(self): self.assert_stage("merge", PublicationStatus.MERGED)
    def test_activate_after_manual_merge(self): self.assert_stage("activate", PublicationStatus.ACTIVATED)


if __name__ == "__main__":
    unittest.main()
