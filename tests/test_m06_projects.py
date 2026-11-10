import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import httpx

from vibecoder.control_service import AuthorizationError, ControlService, LocalAuthorizer, PeerIdentity
from vibecoder.errors import ConflictError, OperationError
from vibecoder.github_identity import (GitHubFailureCategory, GitHubIdentityService,
                                       GitHubVerification, GitHubVerificationError)
from vibecoder.projects import (HttpGitHubProjectAPI, ProjectService, RemoteRepository,
                                RemoteUser)
from vibecoder.secret_store import FileSecretStore
from vibecoder.sqlite import SQLiteRepository

NOW = "2026-11-09T10:00:00+00:00"
TOKEN = "github_pat_PROJECT_SENTINEL"


def git(cwd, *args):
    return subprocess.run(("git", *args), cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout.strip()


class IdentityVerifier:
    def verify(self, token): return GitHubVerification(42, "owner", NOW, {})


class FakeAPI:
    def __init__(self, sha, repository=None):
        self.sha = sha
        self.repo = repository or RemoteRepository(99, "R_node", "owner", "repo", "owner/repo", "main", "private")
        self.users = {"alice": RemoteUser(7, "alice"), "owner": RemoteUser(42, "owner")}
    def repository(self, token, canonical):
        self.last_token = token
        if canonical.casefold() != "owner/repo": raise OperationError("missing")
        return self.repo
    def branch_sha(self, token, canonical, branch):
        if branch not in {"main", "develop"}: raise OperationError("branch missing")
        return self.sha
    def user(self, token, login):
        if login not in self.users: raise OperationError("user missing")
        return self.users[login]


class BarePreparer:
    def __init__(self, bare): self.bare = bare; self.calls = 0; self.fail = False
    def prepare(self, canonical, branch, sha, destination, token):
        self.calls += 1
        if self.fail: raise OperationError("interrupted")
        git(destination.parent, "clone", "--no-checkout", str(self.bare), str(destination))
        git(destination, "checkout", "-B", branch, sha)


class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.source = root / "source"; git(root, "init", "-b", "main", str(self.source))
        git(self.source, "config", "user.name", "Test"); git(self.source, "config", "user.email", "x@y.invalid")
        (self.source / "README").write_text("initial\n"); git(self.source, "add", "README"); git(self.source, "commit", "-m", "initial")
        self.sha = git(self.source, "rev-parse", "HEAD")
        self.bare = root / "remote.git"; git(root, "clone", "--bare", str(self.source), str(self.bare))
        self.db = SQLiteRepository(root / "db")
        self.identity = GitHubIdentityService(self.db, FileSecretStore(root / "secrets"), lambda: IdentityVerifier())
        self.identity.login(TOKEN, "test")
        self.api = FakeAPI(self.sha); self.preparer = BarePreparer(self.bare)
        self.service = ProjectService(self.db, self.identity, lambda: self.api, root / "workspaces", self.preparer)
        self.observation = {"canonical_name": "owner/repo", "origin_url": "git@github.com:owner/repo.git",
            "branch": "main", "head_sha": self.sha, "upstream": "origin/main", "tracking_sha": self.sha,
            "staged": False, "unstaged": False, "untracked": False, "ahead": 0, "behind": 0, "dirty": False}
    def tearDown(self): self.db.close(); self.tmp.cleanup()

    def test_registration_workspace_initial_authorization_and_idempotency(self):
        result = self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertTrue(result["changed"]); self.assertEqual(result["remote_sha"], self.sha)
        self.assertEqual(git(Path(result["workspace_path"]), "rev-parse", "HEAD"), self.sha)
        self.assertNotIn(TOKEN, (Path(result["workspace_path"]) / ".git/config").read_text())
        users = self.service.users("repo")["users"]
        self.assertEqual(users[0]["github_user_id"], 42)
        again = self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertFalse(again["changed"]); self.assertEqual(self.preparer.calls, 1)

    def test_dirty_sha_branch_rules_and_ignore(self):
        dirty = dict(self.observation, dirty=True, untracked=True)
        with self.assertRaises(OperationError): self.service.register(dirty, branch=None, ignore_dirty=False, actor="root")
        result = self.service.register(dirty, branch=None, ignore_dirty=True, actor="root")
        self.assertEqual(result["remote_sha"], self.sha)
        # New isolated DB for branch checks.
        root = Path(self.tmp.name)
        db2 = SQLiteRepository(root / "db2"); identity2 = GitHubIdentityService(db2, FileSecretStore(root / "secrets2"), lambda: IdentityVerifier()); identity2.login(TOKEN, "x")
        service2 = ProjectService(db2, identity2, lambda: self.api, root / "ws2", BarePreparer(self.bare))
        feature = dict(self.observation, branch="develop")
        with self.assertRaises(OperationError): service2.register(feature, branch=None, ignore_dirty=True, actor="root")
        with self.assertRaises(OperationError): service2.register(feature, branch="other", ignore_dirty=True, actor="root")
        db2.close()

    def test_interruption_is_recorded_and_retry_recovers(self):
        self.preparer.fail = True
        with self.assertRaises(OperationError): self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertEqual(self.db.query_one("SELECT status FROM project_registration_operations")["status"], "FAILED")
        self.preparer.fail = False
        result = self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertTrue(result["changed"])

    def test_authorization_idempotency_last_user_and_resolution(self):
        item = self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertTrue(self.service.authorize("repo", "alice", "root")["changed"])
        self.assertFalse(self.service.authorize("owner/repo", "alice", "root")["changed"])
        self.assertTrue(self.service.deauthorize(item["id"], "alice", "root")["changed"])
        self.assertFalse(self.service.deauthorize("repo", "alice", "root")["changed"])
        with self.assertRaises(ConflictError): self.service.deauthorize("repo", "owner", "root")

    def test_empty_remote_message_and_workspace_symlink_rejected(self):
        class EmptyAPI(FakeAPI):
            def branch_sha(inner, token, canonical, branch):
                raise GitHubVerificationError(GitHubFailureCategory.NOT_FOUND, "missing", status_code=404)
        empty = ProjectService(self.db, self.identity, lambda: EmptyAPI(self.sha),
                               Path(self.tmp.name) / "empty-ws", self.preparer)
        with self.assertRaises(OperationError) as caught:
            empty.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        self.assertIn("commit inicial", str(caught.exception))
        unsafe = Path(self.tmp.name) / "unsafe"
        unsafe.symlink_to(Path(self.tmp.name) / "elsewhere")
        service = ProjectService(self.db, self.identity, lambda: self.api, unsafe, self.preparer)
        with self.assertRaises(OperationError):
            service.register(self.observation, branch=None, ignore_dirty=False, actor="root")

    def test_control_protocol_arguments_and_authorization(self):
        control = ControlService(self.db, LocalAuthorizer(9), self.identity, self.service)
        with self.assertRaises(AuthorizationError):
            control.dispatch("project.add", {"observation": self.observation, "branch": None, "ignore_dirty": False}, PeerIdentity(1, 100, 100))
        result = control.dispatch("project.add", {"observation": self.observation, "branch": None, "ignore_dirty": False}, PeerIdentity(1, 100, 9))
        self.assertEqual(result["canonical_name"], "owner/repo")
        self.assertEqual(len(control.dispatch("project.list", {}, PeerIdentity(1, 100, 100))["projects"]), 1)
        self.assertEqual(control.status()["operational_state"], "STANDBY/ONLINE")

    def test_same_name_in_different_owners_is_ambiguous(self):
        self.service.register(self.observation, branch=None, ignore_dirty=False, actor="root")
        now = NOW
        self.db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)",
                        ("repo-2", 100, "node-2", "other", "repo", "other/repo", "main", now, now))
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
                        ("project-2", "repo", "repo-2", "main", self.sha, "ENABLED", now, now))
        with self.assertRaises(ConflictError): self.service.get("repo")
        self.assertEqual(self.service.get("other/repo")["id"], "project-2")


class ProjectHTTPTests(unittest.TestCase):
    def test_repository_branch_user_and_classified_errors(self):
        def handler(request):
            if request.url.path == "/repos/o/r": return httpx.Response(200, json={"id": 9, "node_id": "n", "owner": {"login": "o"}, "name": "r", "full_name": "o/r", "default_branch": "main", "visibility": "private"})
            if request.url.path.endswith("/git/ref/heads/main"): return httpx.Response(200, json={"object": {"sha": "a" * 40}})
            if request.url.path == "/users/alice": return httpx.Response(200, json={"id": 8, "login": "alice"})
            return httpx.Response(404, json={"message": "Not Found"})
        api = HttpGitHubProjectAPI(transport=httpx.MockTransport(handler))
        self.assertEqual(api.repository(TOKEN, "o/r").github_id, 9)
        self.assertEqual(api.branch_sha(TOKEN, "o/r", "main"), "a" * 40)
        self.assertEqual(api.user(TOKEN, "alice").github_id, 8)
        with self.assertRaises(OperationError): api.user(TOKEN, "missing")

    def test_http_auth_permission_and_rate_errors(self):
        for status, headers, expected in (
            (401, {}, GitHubFailureCategory.TOKEN_REJECTED),
            (403, {"X-Accepted-GitHub-Permissions": "metadata=read"}, GitHubFailureCategory.ACCESS_DENIED),
            (403, {"X-RateLimit-Remaining": "0"}, GitHubFailureCategory.RATE_LIMIT),
        ):
            def handler(request, status=status, headers=headers):
                return httpx.Response(status, json={"message": "failure"}, headers=headers)
            api = HttpGitHubProjectAPI(transport=httpx.MockTransport(handler))
            with self.assertRaises(GitHubVerificationError) as caught:
                api.repository(TOKEN, "o/r")
            self.assertEqual(caught.exception.category, expected)
            self.assertNotIn(TOKEN, str(caught.exception))

