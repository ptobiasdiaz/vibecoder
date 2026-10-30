from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from vibecoder import ConflictError, ValidationError
from vibecoder.git import GitAdapter, WorkspaceManager


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


class GitAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.adapter = GitAdapter()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def bare(self, name: str) -> Path:
        path = self.root / name
        git("init", "--bare", str(path))
        return path

    def populated_remote(self) -> Path:
        remote = self.bare("populated.git")
        source = self.root / "source"
        git("init", "-b", "main", str(source))
        (source / "README.md").write_text("# Existing\n", encoding="utf-8")
        git("add", "README.md", cwd=source)
        git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial", cwd=source)
        git("remote", "add", "origin", str(remote), cwd=source)
        git("push", "-u", "origin", "main", cwd=source)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
        return remote

    def test_clone_with_commit_detects_main_and_creates_bootstrap_branch(self) -> None:
        remote = self.populated_remote()
        workspace = self.root / "workspace"
        self.adapter.clone(str(remote), workspace)
        self.assertEqual(self.adapter.default_branch(workspace), "main")
        self.adapter.create_branch(workspace, "vibecoder/bootstrap-test", "main")
        self.assertEqual(self.adapter.current_branch(workspace), "vibecoder/bootstrap-test")
        self.assertEqual((workspace / "README.md").read_text(), "# Existing\n")

    def test_empty_repository_uses_orphan_branch_and_first_local_commit(self) -> None:
        remote = self.bare("empty.git")
        workspace = self.root / "empty-workspace"
        self.adapter.clone(str(remote), workspace)
        self.assertIsNone(self.adapter.default_branch(workspace))
        self.adapter.create_branch(workspace, "vibecoder/bootstrap-empty", None)
        (workspace / "README.md").write_text("# New\n", encoding="utf-8")
        status = self.adapter.status(workspace)
        self.assertEqual(status.changes[0].worktree_status, "?")
        commit = self.adapter.commit_all(workspace, "bootstrap")
        self.assertEqual(self.adapter.status(workspace).head, commit)
        self.assertEqual(self.adapter.commit_all(workspace, "bootstrap"), commit)

    def test_remote_and_existing_branch_are_verified(self) -> None:
        first = self.bare("one.git")
        second = self.bare("two.git")
        workspace = self.root / "workspace"
        self.adapter.clone(str(first), workspace)
        self.adapter.create_branch(workspace, "vibecoder/bootstrap-test", None)
        self.adapter.verify_remote(workspace, str(first))
        with self.assertRaises(ConflictError):
            self.adapter.verify_remote(workspace, str(second))
        self.adapter.create_branch(workspace, "vibecoder/bootstrap-test", None)

    def test_invalid_branch_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.adapter._validate_branch("../../main")

    def test_noninteractive_push_does_not_persist_token(self) -> None:
        remote = self.bare("push.git")
        workspace = self.root / "push-workspace"
        self.adapter.clone(str(remote), workspace)
        local_sha = self.adapter.create_empty_base(workspace, "main")
        self.adapter.push_branch(
            workspace, "main", "main", token="test-secret-never-persist"
        )
        config = git("config", "--local", "--list", cwd=workspace)
        self.assertNotIn("test-secret-never-persist", config)
        self.assertEqual(
            local_sha,
            git("--git-dir", str(remote), "rev-parse", "refs/heads/main"),
        )


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_isolates_projects_and_rejects_traversal(self) -> None:
        manager = WorkspaceManager(self.base / "workspaces")
        first = manager.path_for(str(uuid4()))
        second = manager.path_for(str(uuid4()))
        self.assertNotEqual(first, second)
        with self.assertRaises(ValidationError):
            manager.path_for("../../escape")

    def test_rejects_root_inside_orchestrator_and_workspace_symlink(self) -> None:
        forbidden = self.base / "orchestrator"
        forbidden.mkdir()
        with self.assertRaises(ValidationError):
            WorkspaceManager(forbidden / "workspaces", forbidden_roots=(forbidden,))
        manager = WorkspaceManager(self.base / "safe")
        project_id = str(uuid4())
        target = self.base / "target"
        target.mkdir()
        manager.path_for(project_id).symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValidationError):
            manager.path_for(project_id)

    def test_archive_is_explicit_and_recoverable(self) -> None:
        manager = WorkspaceManager(self.base / "workspaces")
        workspace = manager.path_for(str(uuid4()))
        workspace.mkdir()
        (workspace / "result.txt").write_text("keep", encoding="utf-8")
        archived = manager.archive(workspace)
        self.assertFalse(workspace.exists())
        self.assertEqual((archived / "result.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
