import json
import socket
import struct
import unittest

from vibecoder.control_protocol import (
    ControlRequest, IncompatibleProtocol, InvalidMessage, MessageTooLarge,
    ProtocolTimeout, encode_frame, receive_frame,
)


class ProtocolTests(unittest.TestCase):
    class MemorySocket:
        def __init__(self, data=b"", error=None):
            self.data = bytearray(data); self.error = error
        def recv(self, size):
            if self.error:
                raise self.error
            result = bytes(self.data[:size]); del self.data[:size]
            return result
    def test_valid_request_and_identifier(self):
        request = ControlRequest.from_payload({"version": 1, "request_id": "abc",
            "operation": "status", "arguments": {}})
        self.assertEqual(request.request_id, "abc")

    def test_incompatible_version_unknown_shape_and_invalid_json(self):
        with self.assertRaises(IncompatibleProtocol):
            ControlRequest.from_payload({"version": 2, "request_id": "x", "operation": "status"})
        with self.assertRaises(InvalidMessage):
            ControlRequest.from_payload([])
        connection = self.MemorySocket(struct.pack("!I", 1) + b"{")
        with self.assertRaises(InvalidMessage):
            receive_frame(connection)

    def test_message_limit_and_declared_oversize(self):
        with self.assertRaises(MessageTooLarge):
            encode_frame({"data": "x" * 100}, max_size=20)
        with self.assertRaises(MessageTooLarge):
            receive_frame(self.MemorySocket(struct.pack("!I", 100)), max_size=20)

    def test_timeout_and_disconnect(self):
        with self.assertRaises(ProtocolTimeout):
            receive_frame(self.MemorySocket(error=socket.timeout()))
        with self.assertRaises(EOFError):
            receive_frame(self.MemorySocket())

