import json
import socket
import struct
import tempfile
import unittest
from pathlib import Path

from vibecoder.control_protocol import encode_frame
from vibecoder.control_service import ControlService, LocalAuthorizer, PeerIdentity
from vibecoder.daemon import ControlRequestHandler
from vibecoder.sqlite import SQLiteRepository


class MemoryConnection:
    def __init__(self, incoming=b"", recv_error=None):
        self.incoming = bytearray(incoming); self.outgoing = bytearray(); self.recv_error = recv_error
    def settimeout(self, value): self.timeout = value
    def recv(self, size):
        if self.recv_error: raise self.recv_error
        result = bytes(self.incoming[:size]); del self.incoming[:size]
        return result
    def sendall(self, data): self.outgoing.extend(data)


class FakeServer:
    request_timeout = 0.1
    max_message_size = 1024
    def __init__(self, service, peer):
        self.control_service = service
        self.peer_resolver = lambda connection: peer


def decode_outgoing(connection):
    size = struct.unpack("!I", connection.outgoing[:4])[0]
    return json.loads(connection.outgoing[4:4+size])


class RequestHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SQLiteRepository(Path(self.tmp.name) / "db")
        self.service = ControlService(self.db, LocalAuthorizer(99))
        self.peer = PeerIdentity(1, 0, 0)
    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def invoke(self, payload=None, raw=None, peer=None):
        incoming = raw if raw is not None else encode_frame(payload, max_size=1024)
        connection = MemoryConnection(incoming)
        ControlRequestHandler(connection, None, FakeServer(self.service, peer or self.peer))
        return decode_outgoing(connection)

    def test_valid_unknown_version_invalid_json_and_ids(self):
        valid = self.invoke({"version": 1, "request_id": "one", "operation": "status", "arguments": {}})
        self.assertTrue(valid["ok"]); self.assertEqual(valid["request_id"], "one")
        unknown = self.invoke({"version": 1, "request_id": "two", "operation": "shell", "arguments": {}})
        self.assertEqual(unknown["error"]["code"], "UNKNOWN_OPERATION")
        version = self.invoke({"version": 8, "request_id": "three", "operation": "status", "arguments": {}})
        self.assertEqual(version["error"]["code"], "INCOMPATIBLE_PROTOCOL")
        invalid = self.invoke(raw=struct.pack("!I", 1) + b"{")
        self.assertEqual(invalid["error"]["code"], "INVALID_MESSAGE")

    def test_oversized_and_unauthorized_mutation(self):
        oversized = self.invoke(raw=struct.pack("!I", 2000))
        self.assertEqual(oversized["error"]["code"], "MESSAGE_TOO_LARGE")
        denied = self.invoke({"version": 1, "request_id": "x", "operation": "pause", "arguments": {}},
                             peer=PeerIdentity(2, 1000, 1000))
        self.assertEqual(denied["error"]["code"], "FORBIDDEN")

