"""Versioned, length-prefixed JSON protocol for the local control socket."""
from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 1024 * 1024
HEADER_SIZE = 4


class ProtocolError(RuntimeError):
    code = "PROTOCOL_ERROR"


class InvalidMessage(ProtocolError):
    code = "INVALID_MESSAGE"


class IncompatibleProtocol(ProtocolError):
    code = "INCOMPATIBLE_PROTOCOL"


class MessageTooLarge(ProtocolError):
    code = "MESSAGE_TOO_LARGE"


class ProtocolTimeout(ProtocolError):
    code = "TIMEOUT"


@dataclass(frozen=True)
class ControlRequest:
    request_id: str
    operation: str
    arguments: Mapping[str, Any]
    version: int = PROTOCOL_VERSION

    @classmethod
    def from_payload(cls, payload: Any) -> "ControlRequest":
        if not isinstance(payload, dict):
            raise InvalidMessage("la petición debe ser un objeto JSON")
        request_id = payload.get("request_id")
        operation = payload.get("operation")
        arguments = payload.get("arguments", {})
        version = payload.get("version")
        if not isinstance(request_id, str) or not request_id.strip():
            raise InvalidMessage("request_id inválido")
        if not isinstance(operation, str) or not operation.strip():
            raise InvalidMessage("operation inválida")
        if not isinstance(arguments, dict):
            raise InvalidMessage("arguments debe ser un objeto")
        if not isinstance(version, int):
            raise InvalidMessage("version debe ser un entero")
        if version != PROTOCOL_VERSION:
            raise IncompatibleProtocol(
                f"versión {version} incompatible; se requiere {PROTOCOL_VERSION}")
        return cls(request_id, operation, arguments, version)

    def payload(self) -> dict[str, Any]:
        return {"version": self.version, "request_id": self.request_id,
                "operation": self.operation, "arguments": dict(self.arguments)}


def success(request_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "request_id": request_id,
            "ok": True, "result": dict(result)}


def failure(request_id: str | None, code: str, message: str,
            details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "request_id": request_id,
            "ok": False, "error": {"code": code, "message": message,
                                     "details": dict(details or {})}}


def encode_frame(payload: Mapping[str, Any], *, max_size: int = MAX_MESSAGE_SIZE) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > max_size:
        raise MessageTooLarge(f"mensaje de {len(body)} bytes; máximo {max_size}")
    return struct.pack("!I", len(body)) + body


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    try:
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise EOFError("conexión cerrada durante el mensaje")
            chunks.extend(chunk)
    except socket.timeout as exc:
        raise ProtocolTimeout("tiempo de espera agotado") from exc
    return bytes(chunks)


def receive_frame(connection: socket.socket, *, max_size: int = MAX_MESSAGE_SIZE) -> Any:
    header = _receive_exact(connection, HEADER_SIZE)
    size = struct.unpack("!I", header)[0]
    if size > max_size:
        raise MessageTooLarge(f"mensaje de {size} bytes; máximo {max_size}")
    if size == 0:
        raise InvalidMessage("mensaje vacío")
    raw = _receive_exact(connection, size)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidMessage("JSON inválido") from exc


def send_frame(connection: socket.socket, payload: Mapping[str, Any], *,
               max_size: int = MAX_MESSAGE_SIZE) -> None:
    connection.sendall(encode_frame(payload, max_size=max_size))

