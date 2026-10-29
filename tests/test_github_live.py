"""Integraciones GitHub opt-in. Las escrituras requieren una segunda autorización."""

from __future__ import annotations

import os
import time
import unittest

from vibecoder.github import GitHubConfig, GitHubHttpClient


def _configured_client() -> tuple[GitHubHttpClient, str, str]:
    token = os.environ["M031_GITHUB_TOKEN"]
    owner, name = os.environ["M031_GITHUB_REPOSITORY"].split("/", 1)
    return GitHubHttpClient(GitHubConfig(token=token)), owner, name


@unittest.skipUnless(
    os.environ.get("M031_GITHUB_LIVE_READ") == "1",
    "GitHub live read opt-in",
)
class GitHubReadLiveTests(unittest.TestCase):
    def test_authentication_and_repository_metadata(self) -> None:
        client, owner, name = _configured_client()
        with client:
            self.assertTrue(client.authenticate()["id"])
            repository = client.get_repository(owner, name)
            self.assertEqual(repository["full_name"].casefold(), f"{owner}/{name}".casefold())
            self.assertIsInstance(client.is_repository_empty(owner, name), bool)


@unittest.skipUnless(
    os.environ.get("M031_GITHUB_LIVE_WRITE") == "I_UNDERSTAND_THIS_WRITES_TO_GITHUB",
    "GitHub live write requires explicit destructive opt-in",
)
class GitHubWriteLiveTests(unittest.TestCase):
    def test_issue_comment_and_label_on_dedicated_test_repository(self) -> None:
        client, owner, name = _configured_client()
        stamp = str(time.time_ns())
        with client:
            issue = client.create_issue(
                owner, name, f"vibecoder M03.1 integration {stamp}",
                "Created by an explicitly authorized M03.1 live test.",
            )
            client.create_comment(owner, name, int(issue["number"]), "M03.1 comment test")
            label_name = "vibecoder-m031-live"
            label = client.get_label(owner, name, label_name)
            if label is None:
                client.create_label(owner, name, label_name, "1f883d", "M03.1 live test")
            assigned = client.set_issue_labels(owner, name, int(issue["number"]), [label_name])
            self.assertEqual([item["name"] for item in assigned], [label_name])
