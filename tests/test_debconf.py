from __future__ import annotations

import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEBIAN = ROOT / "debian"


class DebconfTests(unittest.TestCase):
    def test_templates_are_non_secret_and_have_safe_defaults(self):
        text = (DEBIAN / "templates").read_text(encoding="utf-8")
        self.assertIn("Template: vibecoder/initial-admin-id", text)
        self.assertIn("Template: vibecoder/show-first-run-hint", text)
        self.assertIn("Default: true", text)
        self.assertNotRegex(text.lower(), r"template:.*(token|password|secret|api-key)")
        self.assertNotRegex(text, r"ghp_|github_pat_|sk-")

    def test_config_uses_standard_debconf_without_network_or_secrets(self):
        text = (DEBIAN / "config").read_text(encoding="utf-8")
        self.assertIn("/usr/share/debconf/confmodule", text)
        self.assertIn("db_input", text)
        self.assertIn("db_go", text)
        self.assertNotRegex(text, r"\b(curl|wget|codex|git clone|systemctl)\b")
        self.assertTrue(os.stat(DEBIAN / "config").st_mode & 0o111)

    def test_postinst_uses_admin_service_and_never_ad_hoc_sql(self):
        text = (DEBIAN / "postinst").read_text(encoding="utf-8")
        self.assertIn("setup bootstrap-admin", text)
        self.assertIn("db_get vibecoder/initial-admin-id", text)
        self.assertNotRegex(text.lower(), r"sqlite3|insert\s+into|create\s+table")
        self.assertNotRegex(text, r"\b(curl|wget|codex|git clone)\b")
        self.assertNotIn("setup activate", text)
        self.assertNotIn("operational_state\": \"READY", text)

    def test_reconfiguration_is_conservative(self):
        postinst = (DEBIAN / "postinst").read_text(encoding="utf-8")
        self.assertNotRegex(postinst, r"rm\s+.*(vibecoder\.db|secrets|workspaces)")
        self.assertNotIn("credentials revoke", postinst)
        self.assertNotIn("projects disable", postinst)


if __name__ == "__main__": unittest.main()
