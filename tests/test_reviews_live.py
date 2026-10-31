"""Validación conjunta M04.3/M04.4 por etapas; desactivada por defecto."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

from vibecoder.github import GitHubConfig, GitHubHttpClient
from vibecoder.github_service import GitHubRegistryService
from vibecoder.reviews import PullRequestReviewService
from vibecoder.runtime_config import load_account_configs
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


@unittest.skipUnless(
    os.environ.get("M044_LIVE") == "I_UNDERSTAND_M044_MONITORS_A_REAL_PR",
    "M04.4 live requiere opt-in explícito",
)
class PullRequestReviewLiveTests(unittest.TestCase):
    def run_stage(self, expected: str):
        self.assertEqual(os.environ.get("M044_LIVE_STAGE"), expected)
        repository = SQLiteRepository(Path(os.environ["M044_DATABASE"]).resolve())
        client = GitHubHttpClient(GitHubConfig(token=os.environ["VIBECODER_GITHUB_TOKEN"]))
        try:
            core = OrchestratorCore(repository)
            service = PullRequestReviewService(
                core, GitHubRegistryService(core, client),
                load_account_configs(os.environ["M044_ACCOUNTS"]),
                Path(os.environ["M044_PROJECT_WORKSPACE"]),
                authorized_users=os.environ["M044_AUTHORIZED_USERS"].split(","),
            )
            if expected == "start": return service.start(os.environ["M044_EXECUTION_ID"])
            supervision_id = os.environ["M044_SUPERVISION_ID"]
            if expected == "poll": return service.poll(supervision_id)
            if expected == "process":
                self.assertEqual(os.environ.get("M044_LIVE_CODEX"), "I_ACCEPT_CODEX_QUOTA_USAGE")
                self.assertEqual(os.environ.get("M044_LIVE_GITHUB_WRITE"), "I_AUTHORIZE_GITHUB_WRITES")
                return asyncio.run(service.process_event(os.environ["M044_EVENT_ID"]))
            if expected == "prepare-correction":
                return service.prepare_correction(
                    supervision_id, os.environ["M044_CORRECTION_EVENT_IDS"].split(","),
                    os.environ["M044_CORRECTION_DESCRIPTION"], os.environ["M044_CORRECTION_SCOPE"],
                    actor=os.environ["M044_OPERATOR"], authorized=True,
                )
            correction_id = os.environ.get("M044_CORRECTION_ID")
            if expected == "run-correction":
                self.assertEqual(os.environ.get("M044_LIVE_CODEX"), "I_ACCEPT_CODEX_QUOTA_USAGE")
                return asyncio.run(service.run_correction(correction_id))
            if expected == "validate": return service.validate_correction(correction_id)
            if expected == "commit": return service.commit_correction(correction_id)
            if expected == "publish":
                self.assertEqual(os.environ.get("M044_LIVE_GITHUB_WRITE"), "I_AUTHORIZE_GITHUB_WRITES")
                return service.publish_correction(
                    correction_id, token=os.environ["VIBECODER_GITHUB_TOKEN"], authorized=True
                )
            if expected == "reply":
                self.assertEqual(os.environ.get("M044_LIVE_GITHUB_WRITE"), "I_AUTHORIZE_GITHUB_WRITES")
                return service.reply_correction(correction_id)
            if expected == "merge": return service.complete_merge(supervision_id)
            if expected == "reconcile": return service.reconcile(supervision_id)
            raise AssertionError(expected)
        finally:
            client.close(); repository.close()

    def test_01_start(self): self.assertIn(self.run_stage("start").status.value, {"MONITORING", "MERGED"})
    def test_02_poll(self): self.run_stage("poll")
    def test_03_process_conversation(self): self.assertEqual(self.run_stage("process")["status"], "COMPLETED")
    def test_04_prepare_correction(self): self.assertEqual(self.run_stage("prepare-correction")["status"], "AUTHORIZED")
    def test_05_run_correction(self): self.assertEqual(self.run_stage("run-correction")["status"], "GENERATED")
    def test_06_validate(self): self.assertEqual(self.run_stage("validate")["status"], "VALIDATED")
    def test_07_commit(self): self.assertEqual(self.run_stage("commit")["status"], "COMMITTED")
    def test_08_publish(self): self.assertEqual(self.run_stage("publish")["status"], "PUBLISHED")
    def test_09_reply(self): self.assertEqual(self.run_stage("reply")["status"], "REPLIED")
    def test_10_merge_after_human_action(self): self.assertEqual(self.run_stage("merge").status.value, "MERGED")
    def test_11_reconcile(self): self.run_stage("reconcile")


if __name__ == "__main__":
    unittest.main()
