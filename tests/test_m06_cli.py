import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vibecoder import __version__
from vibecoder.cli import DaemonPermissionDenied, DaemonUnavailable, main
from vibecoder.git_local import LocalGitObservation


STATUS = {"operational_state": "STANDBY/OFFLINE", "connectivity": "UNCONFIGURED",
          "execution_capacity": "NO_WORKER", "administratively_paused": False,
          "recovery_barrier": False, "project_count": 0, "ready_worker_count": 0}


class CLITests(unittest.TestCase):
    def invoke(self, argv, result=None, error=None):
        out, err = io.StringIO(), io.StringIO()
        with patch("vibecoder.cli.ControlClient.request", side_effect=error,
                   return_value=result), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_help_and_version(self):
        with self.assertRaises(SystemExit) as help_exit:
            main(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        with self.assertRaises(SystemExit) as version_exit:
            main(["--version"])
        self.assertEqual(version_exit.exception.code, 0)

    def test_status_human_and_json(self):
        code, out, _ = self.invoke(["status"], STATUS)
        self.assertEqual(code, 0); self.assertIn("STANDBY/OFFLINE", out)
        code, out, _ = self.invoke(["status", "--json"], STATUS)
        self.assertEqual(json.loads(out)["connectivity"], "UNCONFIGURED")

    def test_doctor_json_pause_and_resume(self):
        doctor = {"checks": [], "schema_version": 1}
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "missing.sock"
            code, out, _ = self.invoke(["--socket", str(sock), "doctor", "--json"], doctor)
        self.assertEqual(code, 0); self.assertFalse(json.loads(out)["socket"]["exists"])
        self.assertEqual(self.invoke(["pause"], {"changed": True, "paused": True})[0], 0)
        self.assertEqual(self.invoke(["resume"], {"changed": False, "paused": False})[0], 0)

    def test_unavailable_and_permission_errors_are_actionable(self):
        code, _, err = self.invoke(["status"], error=DaemonUnavailable("ausente"))
        self.assertEqual(code, 3); self.assertIn("daemon no disponible", err)
        code, _, err = self.invoke(["pause"], error=DaemonPermissionDenied("use vibecoder-admin"))
        self.assertEqual(code, 4); self.assertIn("vibecoder-admin", err)

    def test_github_login_is_hidden_interactive_and_has_no_token_argument(self):
        result = {"configured": True, "login": "alice", "github_user_id": 42,
                  "connectivity": "ONLINE", "configured_at": "now", "checked_at": "now",
                  "diagnostic": {}, "available_for_projects": True}
        out, err = io.StringIO(), io.StringIO()
        with patch("vibecoder.cli.sys.stdin.isatty", return_value=True), \
             patch("vibecoder.cli.getpass.getpass", return_value="github_pat_SECRET") as hidden, \
             patch("vibecoder.cli.ControlClient.request", return_value=result) as request, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["github", "login"])
        self.assertEqual(code, 0); hidden.assert_called_once()
        request.assert_called_once_with("github.login", {"token": "github_pat_SECRET"})
        self.assertNotIn("github_pat_SECRET", out.getvalue() + err.getvalue())

    def test_github_login_refuses_noninteractive_input(self):
        out, err = io.StringIO(), io.StringIO()
        with patch("vibecoder.cli.sys.stdin.isatty", return_value=False), \
             patch("vibecoder.cli.ControlClient.request") as request, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["github", "login"])
        self.assertEqual(code, 2); self.assertIn("terminal interactivo", err.getvalue())
        request.assert_not_called()

    def test_github_status_human_and_json_have_no_secret(self):
        result = {"configured": True, "login": "alice", "github_user_id": 42,
                  "connectivity": "ONLINE", "configured_at": "now", "checked_at": "now",
                  "diagnostic": {}, "available_for_projects": True}
        code, out, _ = self.invoke(["github", "status"], result)
        self.assertEqual(code, 0); self.assertIn("alice (ID 42)", out)
        code, out, _ = self.invoke(["github", "status", "--json"], result)
        self.assertEqual(json.loads(out)["connectivity"], "ONLINE")

    def test_project_add_inspects_locally_and_project_commands(self):
        observation = LocalGitObservation("owner/repo", "https://github.com/owner/repo.git",
            "main", "a" * 40, "origin/main", "a" * 40, False, False, False, 0, 0)
        project = {"id": "full-id", "name": "repo", "canonical_name": "owner/repo",
                   "base_branch": "main", "remote_sha": "a" * 40, "changed": True}
        out, err = io.StringIO(), io.StringIO()
        with patch("vibecoder.cli.LocalGitInspector.inspect", return_value=observation), \
             patch("vibecoder.cli.ControlClient.request", return_value=project) as request, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["project", "add", "/tmp/example", "--ignore-dirty"])
        self.assertEqual(code, 0); self.assertIn("WARNING", err.getvalue())
        request.assert_called_once_with("project.add", {"observation": observation.to_payload(), "branch": None, "ignore_dirty": True})
        listing = {"projects": [project]}
        code, out, _ = self.invoke(["project", "list", "--long"], listing)
        self.assertEqual(code, 0); self.assertIn("full-id", out)
        users = {"project": project, "users": [{"login": "alice", "github_user_id": 7}]}
        code, out, _ = self.invoke(["project", "repo", "users"], users)
        self.assertEqual(code, 0); self.assertIn("alice", out)

    def test_plugin_and_worker_commands(self):
        plugins = {"plugins": [{"id": "fake", "version": "1", "provider": "test",
            "available": True, "compatible": True, "diagnostic": None}]}
        code, out, _ = self.invoke(["plugin", "list"], plugins)
        self.assertEqual(code, 0); self.assertIn("fake 1", out)
        worker = {"id": "worker-id", "name": "fake-1", "plugin_id": "fake",
            "plugin_version": "1", "status": "READY", "last_error": None, "changed": True}
        code, out, _ = self.invoke(["worker", "add", "fake", "--name", "fake-1"], worker)
        self.assertEqual(code, 0); self.assertIn("READY", out)
        code, out, _ = self.invoke(["worker", "list", "--json"], {"workers": [worker]})
        self.assertEqual(json.loads(out)["workers"][0]["name"], "fake-1")

