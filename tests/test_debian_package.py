from __future__ import annotations

import json
import os
import re
import unittest
from importlib import resources
from pathlib import Path

from vibecoder.supervisor import SupervisorConfig


ROOT = Path(__file__).resolve().parents[1]
DEBIAN = ROOT / "debian"


class DebianPackageTests(unittest.TestCase):
    def test_control_declares_runtime_and_build_dependencies(self):
        control = (DEBIAN / "control").read_text(encoding="utf-8")
        for dependency in (
            "debhelper-compat (= 13)", "dh-python", "pybuild-plugin-pyproject",
            "python3-all", "python3-httpx (>= 0.26)",
            "python3-pydantic (>= 1.10.14)", "git", "ca-certificates", "adduser",
            "systemd-sysv",
            "debconf (>= 0.5) | debconf-2.0",
        ):
            self.assertIn(dependency, control)
        self.assertIn("Architecture: all", control)
        self.assertIn("Rules-Requires-Root: no", control)

    def test_initial_configuration_is_fhs_and_inert(self):
        path = DEBIAN / "supervisor.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        installed_accounts = payload["accounts"]
        config = SupervisorConfig.model_validate({
            **payload, "accounts": str(DEBIAN / "accounts.json")
        })
        self.assertEqual(config.operational_state, "NOT_CONFIGURED")
        self.assertEqual(config.database, Path("/var/lib/vibecoder/vibecoder.db"))
        self.assertEqual(config.task_workspace_root, Path("/var/lib/vibecoder/workspaces"))
        self.assertEqual(installed_accounts, "/etc/vibecoder/accounts.json")
        self.assertEqual(config.projects, {})
        self.assertFalse(config.inference_enabled)
        self.assertFalse(config.publication_enabled)
        self.assertFalse(config.remote_reads_enabled)
        self.assertNotRegex(path.read_text(), r"/home/|github_pat_|ghp_|sk-")
        self.assertEqual(json.loads((DEBIAN / "accounts.json").read_text()), {"accounts": {}})
        self.assertEqual(payload["github_token_env"], "VIBECODER_GITHUB_TOKEN")

    def test_systemd_unit_uses_dedicated_identity_and_hardening(self):
        unit = (DEBIAN / "vibecoder.service").read_text(encoding="utf-8")
        required = (
            "User=vibecoder", "Group=vibecoder", "Restart=on-failure",
            "KillSignal=SIGTERM", "PrivateTmp=true", "NoNewPrivileges=true",
            "ProtectSystem=strict", "ProtectHome=true", "UMask=0027",
            "StateDirectory=vibecoder", "RuntimeDirectory=vibecoder",
            "CapabilityBoundingSet=", "SystemCallArchitectures=native",
            "Documentation=file:/usr/share/doc/vibecoder/debian.md",
            "ExecStart=/usr/bin/vibecoder-supervisor --config /etc/vibecoder/supervisor.json run",
        )
        for value in required:
            self.assertIn(value, unit)
        self.assertNotIn("User=root", unit)

    def test_maintainer_scripts_are_conservative(self):
        postinst = (DEBIAN / "postinst").read_text(encoding="utf-8")
        postrm = (DEBIAN / "postrm").read_text(encoding="utf-8")
        self.assertIn("--system", postinst)
        self.assertIn("/usr/sbin/nologin", postinst)
        self.assertNotIn("chmod -R", postinst)
        self.assertNotIn("0777", postinst)
        self.assertNotIn("systemctl", postinst)
        self.assertNotRegex(postinst, r"\b(codex|git clone|pip install)\b")
        self.assertNotRegex(postrm, r"rm\s+-r.*var/lib/vibecoder")
        for script in (DEBIAN / "rules", DEBIAN / "postinst", DEBIAN / "postrm"):
            self.assertTrue(os.stat(script).st_mode & 0o111, script)

    def test_entrypoint_and_migrations_are_packaged(self):
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('vibecoder-supervisor = "vibecoder.supervisor:main"', project)
        self.assertIn('vibecoder-admin = "vibecoder.admin:main"', project)
        migrations = resources.files("vibecoder").joinpath("migrations")
        names = sorted(item.name for item in migrations.iterdir() if item.name.endswith(".sql"))
        self.assertEqual(names[0], "0001_initial.sql")
        self.assertEqual(names[-1], "0015_maintenance.sql")
        links = (DEBIAN / "links").read_text(encoding="utf-8")
        for command in re.findall(r"^(vibecoder-[\w-]+)\s*=", project, re.MULTILINE):
            if command not in {"vibecoder-supervisor", "vibecoder-admin"}:
                self.assertIn(f"usr/share/man/man1/{command}.1", links)
        self.assertTrue((DEBIAN / "vibecoder-supervisor.1").is_file())
        self.assertTrue((DEBIAN / "vibecoder-admin.1").is_file())
        self.assertIn("var/lib/vibecoder/secrets", (DEBIAN / "dirs").read_text())
        self.assertIn("0700 /var/lib/vibecoder/secrets", (DEBIAN / "postinst").read_text())
        self.assertIn("0700 /var/lib/vibecoder/backups", (DEBIAN / "postinst").read_text())
        self.assertIn("maintenance package-upgrade", (DEBIAN / "postinst").read_text())
        self.assertIn('operational_state=$(vibecoder-admin', (DEBIAN / "postinst").read_text())
        self.assertIn('[ "$operational_state" = NOT_CONFIGURED ]', (DEBIAN / "postinst").read_text())
        self.assertTrue((DEBIAN / "config").stat().st_mode & 0o111)
        self.assertTrue((DEBIAN / "templates").is_file())

    def test_binary_install_manifest_excludes_development_state(self):
        install = (DEBIAN / "install").read_text(encoding="utf-8")
        self.assertNotRegex(install, r"(^|\s)(\.git|\.venv|tests|workspaces|.*\.db)(/|\s|$)")
        forbidden = re.compile(r"(^|/)(\.git|\.venv|__pycache__)(/|$)|\.(db|sqlite3?|log|pyc)$")
        declared = [line.split()[0] for line in install.splitlines() if line.strip()]
        self.assertFalse([path for path in declared if forbidden.search(path)])


if __name__ == "__main__":
    unittest.main()
