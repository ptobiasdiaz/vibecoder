"""Opt-in tests requiring the prepared Debian VM and real systemd."""
from __future__ import annotations
import json
import os
import socket
import stat
import subprocess
import unittest
from pathlib import Path

LIVE = os.environ.get("VIBECODER_LIVE_SYSTEMD") == "1"

@unittest.skipUnless(LIVE, "requires prepared Debian VM and real systemd")
class DebianSidLiveTests(unittest.TestCase):
    def test_services_and_socket(self):
        for unit in ("vibecoderd", "vibecoder-worker-launcher"):
            subprocess.run(("systemctl", "is-active", "--quiet", unit), check=True)
        info = Path("/run/vibecoder/control.sock").stat()
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o660)

    def test_daemon_status(self):
        completed = subprocess.run(("vibecoder", "status", "--json"), check=True,
                                   text=True, capture_output=True)
        self.assertTrue(json.loads(completed.stdout)["daemon_available"])

    def test_control_socket_is_af_unix(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try: client.connect("/run/vibecoder/control.sock")
        finally: client.close()

if __name__ == "__main__": unittest.main()
