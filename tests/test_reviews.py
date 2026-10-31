from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from tests import test_tasks as task_tests
from vibecoder.codex_executor import ExecutionResult
from vibecoder.domain import CorrectionScope, PullRequestSupervisionStatus, TaskExecutionStatus
from vibecoder.errors import ConflictError
from vibecoder.github import GitHubTransientError
from vibecoder.reviews import PullRequestReviewService


class ReviewExecutor(task_tests.ImplementingExecutor):
    async def ensure_thread(self, thread_id):
        if thread_id:
            return thread_id, False
        return ("review-thread" if self.config.cwd.name == "project" else "implementation-thread"), True

    async def wait_turn(self, thread_id, turn_id):
        if self.config.cwd.name == "project":
            return ExecutionResult(thread_id, turn_id, "completed", "review response")
        target = self.config.cwd / "src" / "feature.py"
        target.write_text("VALUE = 2\n", encoding="utf-8")
        return ExecutionResult(thread_id, turn_id, "completed", "correction applied")


class PullRequestReviewTests(task_tests.TaskExecutionTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        if not self._testMethodName.startswith("test_review_"):
            return
        committed = await self.committed()
        published = self.service.publish(committed.id, token="secret", authorized=True)
        execution = self.service.create_pr(published.id, authorized=True)
        self.execution = execution
        self.remote.reviews: dict[int, list[dict[str, Any]]] = {}
        self.remote.review_comments: dict[int, list[dict[str, Any]]] = {}
        self.remote.pr_commits: dict[int, list[dict[str, Any]]] = {
            execution.pull_request_number: [{"sha": execution.published_sha}]
        }
        self.remote.list_pull_request_reviews = lambda owner, name, number: self.remote.reviews.get(number, [])
        self.remote.list_pull_request_review_comments = lambda owner, name, number: self.remote.review_comments.get(number, [])
        self.remote.list_pull_request_commits = lambda owner, name, number: self.remote.pr_commits.get(number, [])
        self.review_service = PullRequestReviewService(
            self.core, self.github, {self.account.id: self.runtime}, self.checkout,
            authorized_users=("alice",), git=self.git_adapter,
            executor_factory=ReviewExecutor,
        )
        self.supervision = self.review_service.start(execution.id)

    def add_review(self, state="CHANGES_REQUESTED", body="Please fix this", *,
                   rid=7001, login="alice", commit=None):
        item = {
            "id": rid, "node_id": f"R_{rid}", "body": body, "state": state,
            "user": {"id": 42, "login": login, "type": "User"},
            "commit_id": commit or self.execution.published_sha,
            "submitted_at": "2026-10-31T13:00:00Z", "html_url": "https://github.test/review",
        }
        self.remote.reviews.setdefault(self.execution.pull_request_number, []).append(item)
        return item

    def add_line_comment(self, body="line issue", *, cid=7101, login="alice"):
        item = {
            "id": cid, "node_id": f"RC_{cid}", "body": body,
            "user": {"id": 42, "login": login, "type": "User"},
            "commit_id": self.execution.published_sha, "path": "src/feature.py", "line": 1,
            "created_at": "2026-10-31T13:00:00Z", "updated_at": "2026-10-31T13:00:00Z",
            "html_url": "https://github.test/comment",
        }
        self.remote.review_comments.setdefault(self.execution.pull_request_number, []).append(item)
        return item

    def correction_event(self):
        self.add_review()
        supervision = self.review_service.poll(self.supervision.id)
        self.assertEqual(supervision.status, PullRequestSupervisionStatus.CHANGES_REQUESTED)
        return next(row for row in self.review_service.events(supervision.id)
                    if row["remote_kind"] == "REVIEW")

    async def test_review_poll_distinguishes_sources_routes_and_is_idempotent(self):
        self.remote.comments[self.execution.pull_request_number] = [{
            "id": 7201, "node_id": "IC_7201", "body": "/agent main\nScope question",
            "user": {"id": 42, "login": "alice", "type": "User"},
            "created_at": "2026-10-31T13:00:00Z", "updated_at": "2026-10-31T13:00:00Z",
            "html_url": "https://github.test/issue-comment",
        }]
        self.add_review(); self.add_line_comment()
        first = self.review_service.poll(self.supervision.id)
        second = self.review_service.poll(self.supervision.id)
        events = self.review_service.events(first.id)
        self.assertEqual(len(events), 3)
        self.assertEqual(len(self.review_service.events(second.id)), 3)
        main_event = next(row for row in events if row["remote_kind"] == "ISSUE_COMMENT")
        self.assertEqual(main_event["recipient_agent_id"], self.main.id)
        self.assertTrue(all(row["remote_id"] for row in events))

    async def test_review_unauthorized_and_stale_review_do_not_control_state(self):
        self.add_review(login="mallory", rid=7002)
        self.add_review(state="APPROVED", rid=7003, commit="f" * 40)
        supervision = self.review_service.poll(self.supervision.id)
        self.assertEqual(supervision.review_state, "PENDING")
        self.assertTrue(all(row["status"] in {"REJECTED", "IGNORED", "RECEIVED"}
                            for row in self.review_service.events(supervision.id)))

    async def test_review_conversation_reuses_session_and_publication_retry(self):
        self.add_line_comment("explain")
        self.review_service.poll(self.supervision.id)
        event = self.review_service.events(self.supervision.id)[0]
        original = self.remote.create_comment
        self.remote.create_comment = lambda *a, **k: (_ for _ in ()).throw(GitHubTransientError("timeout"))
        pending = await self.review_service.process_event(str(event["id"]))
        self.assertEqual(pending["status"], "RESPONSE_PENDING", pending)
        self.remote.create_comment = original
        completed = self.review_service.publish_response(str(event["id"]))
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertIsNotNone(completed["session_id"])

    async def test_review_scope_requires_explicit_in_scope_authorization(self):
        event = self.correction_event()
        blocked = self.review_service.prepare_correction(
            self.supervision.id, [str(event["id"])], "new unrelated feature",
            CorrectionScope.OUT_OF_SCOPE, actor="operator", authorized=True,
        )
        self.assertEqual(blocked["status"], "BLOCKED")
        with self.assertRaises(ConflictError):
            self.review_service.prepare_correction(
                self.supervision.id, [str(event["id"])], "different",
                CorrectionScope.IN_SCOPE, actor="operator", authorized=True,
            )

    async def test_review_correction_reuses_workspace_session_validates_commits_and_pushes(self):
        event = self.correction_event()
        correction = self.review_service.prepare_correction(
            self.supervision.id, [str(event["id"])], "fix implementation bug",
            CorrectionScope.IN_SCOPE, actor="operator", authorized=True,
        )
        generated = await self.review_service.run_correction(str(correction["id"]))
        self.assertEqual(generated["status"], "GENERATED")
        self.assertEqual(generated["session_id"], self.execution.session_id)
        validated = self.review_service.validate_correction(str(correction["id"]))
        self.assertEqual(validated["status"], "VALIDATED")
        committed = self.review_service.commit_correction(str(correction["id"]))
        self.assertEqual(committed["status"], "COMMITTED")
        published = self.review_service.publish_correction(
            str(correction["id"]), token="secret", authorized=True
        )
        self.assertEqual(published["status"], "PUBLISHED")
        self.assertEqual(self.remote.branches[self.execution.work_branch], published["new_sha"])
        replied = self.review_service.reply_correction(str(correction["id"]))
        self.assertEqual(replied["status"], "REPLIED")
        source = self.repository.get("pull_request_events", str(event["id"]))
        self.assertEqual(source["status"], "COMPLETED")
        self.assertEqual(source["response_comment_id"], replied["response_comment_id"])
        self.assertEqual(self.remote.pr_writes, 1)

    async def test_review_external_branch_change_blocks_correction(self):
        event = self.correction_event()
        self.remote.branches[self.execution.work_branch] = "e" * 40
        with self.assertRaises(ConflictError):
            self.review_service.prepare_correction(
                self.supervision.id, [str(event["id"])], "fix",
                CorrectionScope.IN_SCOPE, actor="operator", authorized=True,
            )

    async def test_review_closed_without_merge_does_not_unlock_dependency(self):
        self.remote.pulls[0]["state"] = "closed"
        closed = self.review_service.poll(self.supervision.id)
        self.assertEqual(closed.status, PullRequestSupervisionStatus.CLOSED_UNMERGED)
        self.assertEqual(self.service.get(self.execution.id).status, TaskExecutionStatus.PR_OPEN)
        self.assertFalse(self.service.eligible(self.dependent_task.id)["eligible"])

    async def test_review_verified_merge_completes_idempotently_and_unlocks_dependency(self):
        self.remote.pulls[0].update({
            "state": "closed", "merged": True, "merge_commit_sha": "d" * 40,
        })
        merged = self.review_service.poll(self.supervision.id)
        self.assertEqual(merged.status, PullRequestSupervisionStatus.MERGED)
        self.assertEqual(self.service.get(self.execution.id).status, TaskExecutionStatus.MERGED)
        self.assertTrue(self.service.eligible(self.dependent_task.id)["eligible"])
        self.assertEqual(self.review_service.complete_merge(merged.id).id, merged.id)
        self.assertEqual(self.remote.issues[0]["state"], "open")


if __name__ == "__main__":
    unittest.main()
