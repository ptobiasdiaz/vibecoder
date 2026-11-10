import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from vibecoder.control_service import AuthorizationError, ControlService, LocalAuthorizer, PeerIdentity
from vibecoder.github_identity import (
    ConnectivityMonitor, GitHubFailureCategory, GitHubIdentityService,
    GitHubVerification, GitHubVerificationError, HttpGitHubVerifier,
    IdentityChangeBlocked,
)
from vibecoder.secret_store import FileSecretStore
from vibecoder.sqlite import SQLiteRepository


TOKEN = "github_pat_SENTINEL_NEVER_LEAK"
NOW = "2026-11-09T10:00:00+00:00"


class FakeVerifier:
    def __init__(self, result=None, error=None): self.result, self.error = result, error
    def verify(self, token):
        if self.error: raise self.error
        return self.result or GitHubVerification(42, "alice", NOW, {"api_status": 200})


def http_verifier(status=200, payload=None, headers=None):
    def handler(request):
        return httpx.Response(status, json=payload if payload is not None else {"id": 42, "login": "alice"},
                              headers=headers or {}, request=request)
    return HttpGitHubVerifier(transport=httpx.MockTransport(handler))


class GitHubHTTPTests(unittest.TestCase):
    def test_success_and_token_never_in_result(self):
        result = http_verifier().verify(TOKEN)
        self.assertEqual((result.github_user_id, result.login), (42, "alice"))
        self.assertNotIn(TOKEN, repr(result))

    def test_auth_permission_not_found_and_unexpected(self):
        cases = [
            (401, {}, {}, GitHubFailureCategory.TOKEN_REJECTED),
            (403, {"message": "forbidden"}, {"X-Accepted-GitHub-Permissions": "issues=write, contents=read"}, GitHubFailureCategory.ACCESS_DENIED),
            (404, {}, {}, GitHubFailureCategory.NOT_FOUND),
            (200, {"login": "missing-id"}, {}, GitHubFailureCategory.UNEXPECTED_RESPONSE),
            (503, {}, {}, GitHubFailureCategory.GITHUB_ERROR),
        ]
        for status, payload, headers, category in cases:
            with self.subTest(status=status, category=category):
                with self.assertRaises(GitHubVerificationError) as caught:
                    http_verifier(status, payload, headers).verify(TOKEN)
                self.assertEqual(caught.exception.category, category)
                self.assertNotIn(TOKEN, str(caught.exception))
        denied = None
        try: http_verifier(403, {}, {"X-Accepted-GitHub-Permissions": "issues=write, contents=read"}).verify(TOKEN)
        except GitHubVerificationError as exc: denied = exc
        self.assertEqual(denied.accepted_permissions, ("issues=write", "contents=read"))

    def test_primary_secondary_rate_limit_and_timeout(self):
        with self.assertRaises(GitHubVerificationError) as primary:
            http_verifier(403, {}, {"X-RateLimit-Remaining": "0"}).verify(TOKEN)
        self.assertEqual(primary.exception.category, GitHubFailureCategory.RATE_LIMIT)
        with self.assertRaises(GitHubVerificationError) as secondary:
            http_verifier(429, {"message": "secondary rate limit"}, {"Retry-After": "60"}).verify(TOKEN)
        self.assertEqual(secondary.exception.category, GitHubFailureCategory.SECONDARY_RATE_LIMIT)
        self.assertEqual(secondary.exception.retry_after, 60)
        with self.assertRaises(GitHubVerificationError) as secondary_default:
            http_verifier(429, {"message": "secondary rate limit"}).verify(TOKEN)
        self.assertEqual(secondary_default.exception.retry_after, 60)
        def timeout(request): raise httpx.ReadTimeout("late", request=request)
        with self.assertRaises(GitHubVerificationError) as caught:
            HttpGitHubVerifier(transport=httpx.MockTransport(timeout)).verify(TOKEN)
        self.assertEqual(caught.exception.category, GitHubFailureCategory.TIMEOUT)


class GitHubIdentityServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = SQLiteRepository(root / "db")
        self.store = FileSecretStore(root / "secrets")
        self.verifier = FakeVerifier()
        self.service = GitHubIdentityService(self.db, self.store, lambda: self.verifier)
    def tearDown(self): self.db.close(); self.tmp.cleanup()

    def test_login_persists_identity_not_token_and_replaces_same_identity(self):
        result = self.service.login(TOKEN, "actor")
        self.assertEqual(result["github_user_id"], 42)
        row = self.db.query_one("SELECT credential_ref FROM github_identity")
        first = self.db.query_one("SELECT * FROM secret_metadata WHERE id=?", (row["credential_ref"],))
        self.assertEqual(self.store.read(first["storage_ref"]), TOKEN)
        for path in Path(self.tmp.name).glob("db*"):
            self.assertNotIn(TOKEN.encode(), path.read_bytes())
        self.service.login("github_pat_REPLACEMENT", "actor")
        self.assertFalse(self.store.exists(first["storage_ref"]))
        self.assertEqual(self.db.query_one("SELECT status FROM secret_metadata WHERE id=?", (first["id"],))["status"], "RETIRED")
        dump = json.dumps(self.service.status()) + json.dumps(self.db.query_all("SELECT * FROM audit_events"))
        self.assertNotIn(TOKEN, dump)

    def test_failed_validation_preserves_existing_and_cleans_new_file(self):
        self.service.login(TOKEN, "actor")
        original = self.db.query_one("SELECT credential_ref FROM github_identity")["credential_ref"]
        self.verifier.error = GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED, "rejected")
        with self.assertRaises(GitHubVerificationError): self.service.login("github_pat_BAD", "actor")
        self.assertEqual(self.db.query_one("SELECT credential_ref FROM github_identity")["credential_ref"], original)
        self.assertEqual(len(list((Path(self.tmp.name) / "secrets").glob("*.secret"))), 1)

    def test_identity_change_blocked_when_projects_exist(self):
        self.service.login(TOKEN, "actor")
        now = NOW
        self.db.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)", ("r", 1, None, "o", "r", "o/r", "main", now, now))
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)", ("p", "r", "r", "main", "sha", "ENABLED", now, now))
        self.verifier.result = GitHubVerification(99, "bob", NOW, {})
        with self.assertRaises(IdentityChangeBlocked): self.service.login("github_pat_BOB", "actor")
        self.assertEqual(self.service.status()["login"], "alice")

    def test_offline_auth_failed_backoff_and_recovery(self):
        self.service.login(TOKEN, "actor")
        self.verifier.error = GitHubVerificationError(GitHubFailureCategory.NETWORK, "offline")
        self.assertEqual(self.service.verify_current()["connectivity"], "OFFLINE")
        row = self.db.query_one("SELECT consecutive_failures,next_check_at FROM github_identity")
        self.assertEqual(row["consecutive_failures"], 1); self.assertIsNotNone(row["next_check_at"])
        self.verifier.error = GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED, "revoked")
        self.assertEqual(self.service.verify_current()["connectivity"], "AUTH_FAILED")
        self.verifier.error = None
        self.assertEqual(self.service.verify_current()["connectivity"], "ONLINE")
        self.assertEqual(self.db.query_one("SELECT consecutive_failures FROM github_identity")["consecutive_failures"], 0)

    def test_status_integrates_standby_online_without_worker(self):
        self.service.login(TOKEN, "actor")
        control = ControlService(self.db, LocalAuthorizer(1), self.service)
        self.assertEqual(control.status()["operational_state"], "STANDBY/ONLINE")
        public = control.dispatch("github.status", {}, PeerIdentity(1, 100, 100))
        self.assertNotIn("credential_ref", public); self.assertNotIn(TOKEN, json.dumps(public))

    def test_control_operations_authorize_login_and_validate_arguments(self):
        control = ControlService(self.db, LocalAuthorizer(7), self.service)
        with self.assertRaises(AuthorizationError):
            control.dispatch("github.login", {"token": TOKEN}, PeerIdentity(1, 100, 100))
        result = control.dispatch("github.login", {"token": TOKEN}, PeerIdentity(1, 100, 7))
        self.assertEqual(result["login"], "alice")
        self.assertNotIn(TOKEN, json.dumps(result))
        with self.assertRaises(ValueError):
            control.dispatch("github.login", {"token": TOKEN, "uid": 0}, PeerIdentity(1, 100, 7))

    def test_monitor_is_non_blocking_and_applies_result(self):
        self.service.login(TOKEN, "actor")
        self.db.execute("UPDATE github_identity SET next_check_at='2000-01-01T00:00:00+00:00'")
        gate = threading.Event()
        class Blocking:
            def verify(inner, token):
                gate.wait(1); return GitHubVerification(42, "alice2", NOW, {})
        self.service.verifier_factory = Blocking
        monitor = ConnectivityMonitor(self.service)
        started = time.monotonic(); monitor.cycle()
        self.assertLess(time.monotonic() - started, 0.1)
        gate.set()
        for _ in range(50):
            monitor.cycle()
            if self.service.status()["login"] == "alice2": break
            time.sleep(0.005)
        self.assertEqual(self.service.status()["login"], "alice2")

    def test_restart_cleans_orphan_and_retires_superseded_pat(self):
        self.service.login(TOKEN, "actor")
        orphan = self.store.write("orphan")
        old_ref = self.store.write("old")
        self.db.execute("INSERT INTO secret_metadata VALUES(?,?,?,?,?,?)",
                        ("old-id", "GITHUB_PAT", old_ref, "ACTIVE", NOW, NOW))
        GitHubIdentityService(self.db, self.store, lambda: self.verifier)
        self.assertFalse(self.store.exists(orphan)); self.assertFalse(self.store.exists(old_ref))
        self.assertEqual(self.db.query_one("SELECT status FROM secret_metadata WHERE id='old-id'")["status"], "RETIRED")

