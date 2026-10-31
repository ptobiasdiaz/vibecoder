from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from vibecoder import ConflictError, GitHubRegistryService, OrchestratorCore, SQLiteRepository
from vibecoder.github import (
    GitHubAuthenticationError,
    GitHubConfig,
    GitHubHttpClient,
    GitHubPermissionError,
    GitHubProtocolError,
    GitHubRateLimitError,
    GitHubRepositoryNotFound,
    GitHubTransientError,
)


def repository_payload(*, github_id: int = 101, owner: str = "acme", name: str = "alpha") -> dict[str, Any]:
    return {
        "id": github_id,
        "node_id": f"R_{github_id}",
        "name": name,
        "full_name": f"{owner}/{name}",
        "html_url": f"https://github.test/{owner}/{name}",
        "url": f"https://api.github.test/repos/{owner}/{name}",
        "default_branch": None,
        "visibility": "private",
        "archived": False,
        "owner": {"login": owner},
        "permissions": {"pull": True, "push": True},
    }


class FakeGitHubClient:
    def __init__(self) -> None:
        self.repository = repository_payload()
        self.empty = True
        self.issues: list[dict[str, Any]] = []
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.labels: dict[str, dict[str, Any]] = {}
        self.events: dict[int, list[dict[str, Any]]] = {}
        self.issue_writes = 0
        self.comment_writes = 0

    def authenticate(self) -> Mapping[str, Any]:
        return {"id": 1, "login": "bot", "node_id": "U_1", "html_url": "https://github.test/bot"}

    def get_repository(self, owner: str, name: str) -> Mapping[str, Any]:
        return self.repository

    def is_repository_empty(self, owner: str, name: str) -> bool:
        return self.empty

    def get_branch(self, owner: str, name: str, branch: str) -> Mapping[str, Any]:
        return {"name": branch, "commit": {"sha": "a" * 40}}

    def list_issues(self, owner: str, name: str, *, state: str = "open") -> Sequence[Mapping[str, Any]]:
        return [item for item in self.issues if state == "all" or item["state"] == state]

    def get_issue(self, owner: str, name: str, number: int) -> Mapping[str, Any]:
        return next(item for item in self.issues if item["number"] == number)

    def create_issue(self, owner: str, name: str, title: str, body: str) -> Mapping[str, Any]:
        self.issue_writes += 1
        number = len(self.issues) + 1
        issue = {
            "id": 1000 + number, "node_id": f"I_{number}", "number": number,
            "title": title, "body": body, "state": "open",
            "html_url": f"https://github.test/{owner}/{name}/issues/{number}", "labels": [],
        }
        self.issues.append(issue)
        return issue

    def list_comments(self, owner: str, name: str, issue_number: int) -> Sequence[Mapping[str, Any]]:
        return self.comments.get(issue_number, [])

    def create_comment(self, owner: str, name: str, issue_number: int, body: str) -> Mapping[str, Any]:
        self.comment_writes += 1
        comment = {
            "id": 2000 + self.comment_writes, "node_id": f"C_{self.comment_writes}",
            "body": body,
            "html_url": f"https://github.test/{owner}/{name}/issues/{issue_number}#comment",
            "user": {"login": "bot", "type": "Bot"},
            "created_at": f"2026-10-28T00:00:{self.comment_writes:02d}Z",
            "updated_at": f"2026-10-28T00:00:{self.comment_writes:02d}Z",
        }
        self.comments.setdefault(issue_number, []).append(comment)
        return comment

    def list_issue_events(self, owner: str, name: str, issue_number: int) -> Sequence[Mapping[str, Any]]:
        return self.events.get(issue_number, [])

    def list_labels(self, owner: str, name: str) -> Sequence[Mapping[str, Any]]:
        return list(self.labels.values())

    def get_label(self, owner: str, name: str, label: str) -> Mapping[str, Any] | None:
        return self.labels.get(label)

    def create_label(self, owner: str, name: str, label: str, color: str, description: str | None) -> Mapping[str, Any]:
        result = {"id": 3000 + len(self.labels), "node_id": f"L_{label}", "name": label, "color": color, "description": description}
        self.labels[label] = result
        return result

    def set_issue_labels(self, owner: str, name: str, issue_number: int, labels: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        issue = self.get_issue(owner, name, issue_number)
        issue["labels"] = [self.labels[label] for label in labels]
        return issue["labels"]

    def list_pull_requests(self, owner: str, name: str, *, state: str = "all", head: str | None = None, base: str | None = None) -> Sequence[Mapping[str, Any]]:
        return []

    def get_pull_request(self, owner: str, name: str, number: int) -> Mapping[str, Any]:
        raise GitHubRepositoryNotFound("missing")

    def create_pull_request(self, owner: str, name: str, title: str, body: str, head: str, base: str) -> Mapping[str, Any]:
        raise NotImplementedError


class GitHubRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "core.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("main", "CHATGPT", "codex-home://main")
        self.project = self.core.register_project("alpha", self.account.id)
        self.remote = FakeGitHubClient()
        self.service = GitHubRegistryService(self.core, self.remote)

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def register(self):
        return self.service.register_repository(self.project.id, "acme", "alpha")

    def test_registers_empty_repository_and_identity(self) -> None:
        repo = self.register()
        self.assertEqual(repo.github_id, 101)
        self.assertTrue(repo.is_empty)
        self.assertIsNone(repo.default_branch)
        self.assertEqual(self.core.get_project(self.project.id).github_repository, "acme/alpha")
        self.assertEqual(self.service.validate_identity()["login"], "bot")
        self.assertTrue(self.service.validate_repository_access(repo.id, require_write=True)["push"])

    def test_insufficient_reported_repository_permissions_are_rejected(self) -> None:
        repo = self.register()
        self.remote.repository["permissions"] = {"pull": True, "push": False}
        with self.assertRaises(GitHubPermissionError):
            self.service.validate_repository_access(repo.id, require_write=True)

    def test_registration_is_idempotent_and_tracks_rename(self) -> None:
        first = self.register()
        second = self.register()
        self.assertEqual(first.id, second.id)
        self.remote.repository = repository_payload(owner="renamed", name="new-name")
        renamed = self.service.register_repository(self.project.id, "renamed", "new-name")
        self.assertEqual(renamed.id, first.id)
        self.assertEqual(renamed.full_name, "renamed/new-name")

    def test_duplicate_repository_across_projects_is_rejected(self) -> None:
        self.register()
        other = self.core.register_project("beta", self.account.id)
        with self.assertRaises(ConflictError):
            self.service.register_repository(other.id, "acme", "alpha")

    def test_different_repository_cannot_replace_project_registration(self) -> None:
        self.register()
        self.remote.repository = repository_payload(github_id=202, name="other")
        with self.assertRaises(ConflictError):
            self.service.register_repository(self.project.id, "acme", "other")

    def test_issue_and_comment_creation_are_idempotent(self) -> None:
        repo = self.register()
        issue = self.service.create_issue(repo.id, "Bootstrap", "Body", idempotency_key="issue-1")
        repeated = self.service.create_issue(repo.id, "Bootstrap", "Body", idempotency_key="issue-1")
        self.assertEqual(issue["id"], repeated["id"])
        self.assertEqual(self.remote.issue_writes, 1)
        first = self.service.create_comment(repo.id, 1, "Hello", idempotency_key="comment-1")
        second = self.service.create_comment(repo.id, 1, "Hello", idempotency_key="comment-1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.remote.comment_writes, 1)

    def test_idempotency_key_rejects_changed_payload(self) -> None:
        repo = self.register()
        self.service.create_issue(repo.id, "One", "Body", idempotency_key="same")
        with self.assertRaises(ConflictError):
            self.service.create_issue(repo.id, "Changed", "Body", idempotency_key="same")

    def test_labels_are_created_and_assigned_idempotently(self) -> None:
        repo = self.register()
        issue = self.service.create_issue(repo.id, "One", "", idempotency_key="issue")
        first = self.service.ensure_label(repo.id, "vibecoder", "1f883d", idempotency_key="label")
        second = self.service.ensure_label(repo.id, "vibecoder", "1f883d", idempotency_key="label")
        self.assertEqual(first["id"], second["id"])
        assigned = self.service.assign_labels(repo.id, issue["number"], ["vibecoder"], idempotency_key="assign")
        repeated = self.service.assign_labels(repo.id, issue["number"], ["vibecoder"], idempotency_key="assign")
        self.assertEqual(assigned, repeated)

    def test_operations_and_registration_survive_restart(self) -> None:
        repo = self.register()
        issue = self.service.create_issue(repo.id, "One", "", idempotency_key="persist")
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.service = GitHubRegistryService(self.core, self.remote)
        recovered = self.service.get_project_repository(self.project.id)
        repeated = self.service.create_issue(recovered.id, "One", "", idempotency_key="persist")
        self.assertEqual(issue["id"], repeated["id"])
        self.assertEqual(self.remote.issue_writes, 1)

    def test_uncertain_write_is_persisted_for_reconciliation(self) -> None:
        repo = self.register()

        def fail(*args: object, **kwargs: object) -> Mapping[str, Any]:
            raise GitHubTransientError("connection lost")

        self.remote.create_issue = fail  # type: ignore[method-assign]
        with self.assertRaises(GitHubTransientError):
            self.service.create_issue(repo.id, "One", "", idempotency_key="uncertain")
        row = self.repository.find_one(
            "github_operations", repository_id=repo.id, idempotency_key="uncertain"
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "UNKNOWN")

    def test_project_lookups_are_isolated(self) -> None:
        own = self.register()
        other = self.core.register_project("beta", self.account.id)
        self.remote.repository = repository_payload(github_id=202, name="beta")
        other_repo = self.service.register_repository(other.id, "acme", "beta")
        self.assertEqual(self.service.get_project_repository(self.project.id), own)
        self.assertEqual(self.service.get_project_repository(other.id), other_repo)


class GitHubHttpClientTests(unittest.TestCase):
    def client(self, handler) -> GitHubHttpClient:
        return GitHubHttpClient(
            GitHubConfig(token="test-token", api_url="https://api.github.test", max_read_retries=0),
            transport=httpx.MockTransport(handler), sleep=lambda _: None,
        )

    def test_classifies_auth_permission_and_rate_limit(self) -> None:
        cases = [
            (401, {}, GitHubAuthenticationError),
            (403, {}, GitHubPermissionError),
            (403, {"x-ratelimit-remaining": "0"}, GitHubRateLimitError),
            (429, {}, GitHubRateLimitError),
        ]
        for status, headers, expected in cases:
            with self.subTest(status=status, headers=headers):
                client = self.client(lambda request: httpx.Response(status, headers=headers, json={"message": "error"}))
                with client, self.assertRaises(expected):
                    client.authenticate()

    def test_rejects_incomplete_repository_response(self) -> None:
        client = self.client(lambda request: httpx.Response(200, json={"id": 1}))
        with client, self.assertRaises(GitHubProtocolError):
            client.get_repository("acme", "alpha")

    def test_classifies_repository_not_found_and_transient_failure(self) -> None:
        not_found = self.client(lambda request: httpx.Response(404, json={"message": "Not Found"}))
        with not_found, self.assertRaises(GitHubRepositoryNotFound):
            not_found.get_repository("acme", "missing")
        transient = self.client(lambda request: httpx.Response(503, json={"message": "down"}))
        with transient, self.assertRaises(GitHubTransientError):
            transient.authenticate()

    def test_detects_empty_repository_from_official_409_response(self) -> None:
        client = self.client(lambda request: httpx.Response(409, json={"message": "Git Repository is empty."}))
        with client:
            self.assertTrue(client.is_repository_empty("acme", "empty"))

    def test_reads_default_branch_head_with_validated_response(self) -> None:
        client = self.client(
            lambda request: httpx.Response(
                200, json={"name": "main", "commit": {"sha": "a" * 40}}
            )
        )
        with client:
            branch = client.get_branch("acme", "alpha", "main")
        self.assertEqual(branch["commit"]["sha"], "a" * 40)

    def test_creates_pull_request_with_validated_refs(self) -> None:
        payload = {
            "id": 10, "node_id": "PR_10", "number": 3, "title": "Bootstrap",
            "body": "Review", "state": "open", "html_url": "https://github.test/pr/3",
            "draft": False, "merged": False, "merge_commit_sha": None,
            "head": {"ref": "vibecoder/bootstrap", "sha": "b" * 40},
            "base": {"ref": "main", "sha": "a" * 40},
        }
        client = self.client(lambda request: httpx.Response(201, json=payload))
        with client:
            result = client.create_pull_request(
                "acme", "alpha", "Bootstrap", "Review", "vibecoder/bootstrap", "main"
            )
        self.assertEqual(result["number"], 3)
        self.assertEqual(result["head"]["sha"], "b" * 40)

    def test_reads_distinct_review_endpoints_with_validated_models(self) -> None:
        def handler(request):
            path = request.url.path
            if path.endswith("/reviews"):
                payload = [{"id": 1, "node_id": "R_1", "body": "fix",
                            "state": "CHANGES_REQUESTED", "user": {"id": 2, "login": "alice"},
                            "commit_id": "a" * 40, "submitted_at": "2026-10-31T00:00:00Z"}]
            elif path.endswith("/comments"):
                payload = [{"id": 2, "node_id": "RC_2", "body": "line", "html_url": "https://x",
                            "user": {"id": 2, "login": "alice"}, "created_at": "2026-10-31T00:00:00Z",
                            "updated_at": "2026-10-31T00:00:00Z", "path": "src/a.py"}]
            else:
                payload = [{"sha": "a" * 40}]
            return httpx.Response(200, json=payload)
        client = self.client(handler)
        with client:
            self.assertEqual(client.list_pull_request_reviews("acme", "alpha", 1)[0]["id"], 1)
            self.assertEqual(client.list_pull_request_review_comments("acme", "alpha", 1)[0]["path"], "src/a.py")
            self.assertEqual(client.list_pull_request_commits("acme", "alpha", 1)[0]["sha"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
