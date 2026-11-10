import os
import socket
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vibecoder.control_service import PeerIdentity
from vibecoder.daemon import (
    VibecoderDaemon, prepare_socket_path, socket_peer, supplementary_groups,
)
from vibecoder.sqlite import DatabaseKind, classify_database
import sqlite3


class FakeServer:
    def __init__(self, path, service, **kwargs):
        self.path = Path(path); self.path.touch(mode=0o600)
        self.service = service; self.timeout = None; self.closed = False; self.handled = 0
    def handle_request(self):
        self.handled += 1
    def server_close(self):
        self.closed = True
    def serve_forever(self): pass
    def shutdown(self): pass


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db, self.sock = root / "state" / "db.sqlite", root / "run" / "control.sock"
    def tearDown(self):
        self.tmp.cleanup()

    def daemon(self):
        return VibecoderDaemon(self.db, self.sock, admin_gid=os.getgid(), socket_gid=os.getgid(),
                               runtime_approval_socket=self.sock.parent / "approval.sock",
                               runtime_approval_gid=os.getgid())

    def test_start_restart_permissions_and_orderly_stop(self):
        with patch("vibecoder.daemon.ControlServer", FakeServer), patch("vibecoder.daemon.RuntimeApprovalServer", FakeServer), patch("vibecoder.daemon.os.chown"):
            first = self.daemon(); first.start()
            self.assertEqual(stat.S_IMODE(self.sock.stat().st_mode), 0o660)
            self.assertEqual(first.service.status()["operational_state"], "STANDBY/OFFLINE")
            first.request_stop(); first.close()
            self.assertFalse(self.sock.exists())
            second = self.daemon(); second.start()
            self.assertEqual(second.service.status()["project_count"], 0)
            second.close()
        self.assertEqual(classify_database(self.db), DatabaseKind.CURRENT)

    def test_run_honours_preexisting_stop_and_audits_lifecycle(self):
        with patch("vibecoder.daemon.ControlServer", FakeServer), patch("vibecoder.daemon.RuntimeApprovalServer", FakeServer), patch("vibecoder.daemon.os.chown"):
            daemon = self.daemon(); daemon.request_stop(); daemon.run()
        # Reopen independently; daemon has closed its sole connection.
        from vibecoder.sqlite import SQLiteRepository
        db = SQLiteRepository(self.db)
        operations = [row["operation"] for row in db.query_all(
            "SELECT operation FROM audit_events WHERE actor='daemon' ORDER BY created_at")]
        db.close()
        self.assertEqual(operations, ["start", "stop"])

    def test_symlink_and_regular_file_socket_paths_are_rejected(self):
        runtime = self.sock.parent; runtime.mkdir()
        target = runtime / "target"; target.write_text("x")
        self.sock.symlink_to(target)
        with self.assertRaises(RuntimeError): prepare_socket_path(self.sock)
        self.sock.unlink(); self.sock.write_text("not a socket")
        with self.assertRaises(RuntimeError): prepare_socket_path(self.sock)

    def test_so_peercred_is_the_identity_source(self):
        connection = Mock()
        connection.getsockopt.return_value = struct.pack("3i", os.getpid(), os.getuid(), os.getgid())
        peer = socket_peer(connection)
        self.assertEqual((peer.pid, peer.uid, peer.gid), (os.getpid(), os.getuid(), os.getgid()))
        self.assertIn(os.getgid(), peer.supplementary_gids)

    def test_daemon_rejects_legacy_and_corrupt_database_before_socket_bind(self):
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.db)
        connection.execute("CREATE TABLE codex_accounts(id TEXT)"); connection.commit(); connection.close()
        with self.assertRaises(Exception):
            self.daemon().start()
        self.assertFalse(self.sock.exists())
        self.db.unlink(); self.db.parent.mkdir(exist_ok=True); self.db.write_bytes(b"broken")
        with self.assertRaises(Exception):
            self.daemon().start()
        self.assertFalse(self.sock.exists())

