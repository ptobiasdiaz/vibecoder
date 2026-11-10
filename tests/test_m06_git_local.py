import subprocess
import tempfile
import unittest
from pathlib import Path

from vibecoder.git_local import LocalGitError, LocalGitInspector, parse_github_remote


def git(cwd, *args):
    result = subprocess.run(("git", *args), cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True)
    return result.stdout.strip()


class LocalGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.bare = root / "remote.git"; git(root, "init", "--bare", str(self.bare))
        self.repo = root / "repo"; git(root, "init", "-b", "main", str(self.repo))
        git(self.repo, "config", "user.name", "Test"); git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README").write_text("one\n"); git(self.repo, "add", "README"); git(self.repo, "commit", "-m", "initial")
        git(self.repo, "remote", "add", "origin", "https://github.com/Owner/Repo.git")
        # Populate the tracking ref without network access.
        sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        git(self.repo, "branch", "--set-upstream-to=origin/main", "main")
    def tearDown(self): self.tmp.cleanup()

    def test_remote_formats_and_rejections(self):
        for value in ("https://github.com/o/r.git", "git@github.com:o/r.git",
                      "ssh://git@github.com/o/r.git"):
            self.assertEqual(parse_github_remote(value).casefold(), "o/r")
        for value in ("https://gitlab.com/o/r", "file:///tmp/r", "https://x:y@github.com/o/r", "https://github.com/a/b/c"):
            with self.assertRaises(LocalGitError): parse_github_remote(value)

    def test_clean_dirty_ahead_behind_and_diverged_observation(self):
        inspector = LocalGitInspector()
        before_head = git(self.repo, "rev-parse", "HEAD")
        before_status = git(self.repo, "status", "--porcelain=v1")
        clean = inspector.inspect(self.repo)
        self.assertFalse(clean.dirty); self.assertEqual((clean.ahead, clean.behind), (0, 0))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(self.repo, "status", "--porcelain=v1"), before_status)
        (self.repo / "README").write_text("two\n")
        (self.repo / "new").write_text("new")
        dirty = inspector.inspect(self.repo)
        self.assertTrue(dirty.unstaged); self.assertTrue(dirty.untracked)
        git(self.repo, "add", "README"); self.assertTrue(inspector.inspect(self.repo).staged)
        git(self.repo, "commit", "-m", "local")
        self.assertEqual(inspector.inspect(self.repo).ahead, 1)

    def test_worktree_and_detached_head(self):
        worktree = Path(self.tmp.name) / "wt"
        git(self.repo, "worktree", "add", "-b", "feature", str(worktree))
        observed = LocalGitInspector().inspect(worktree)
        self.assertEqual(observed.branch, "feature"); self.assertTrue((worktree / ".git").is_file())
        git(worktree, "checkout", "--detach")
        with self.assertRaises(LocalGitError): LocalGitInspector().inspect(worktree)

    def test_missing_or_invalid_origin(self):
        git(self.repo, "remote", "remove", "origin")
        with self.assertRaises(LocalGitError): LocalGitInspector().inspect(self.repo)

