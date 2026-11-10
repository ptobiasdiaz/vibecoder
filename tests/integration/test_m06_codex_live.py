"""Double opt-in guard for quota-consuming Codex integration tests."""
from __future__ import annotations
import os
import unittest

class CodexLiveOptInTests(unittest.TestCase):
    def test_explicit_double_opt_in_is_required(self):
        enabled = os.environ.get("VIBECODER_LIVE_CODEX") == "1"
        quota = os.environ.get("VIBECODER_ALLOW_CODEX_QUOTA") == "1"
        if enabled:
            self.assertTrue(quota, "live Codex requires VIBECODER_ALLOW_CODEX_QUOTA=1")
        else:
            self.skipTest("Codex live disabled")

if __name__ == "__main__":
    unittest.main()
