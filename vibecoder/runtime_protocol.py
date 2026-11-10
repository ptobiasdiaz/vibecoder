"""Private daemon-to-worker protocol. It intentionally has no daemon operations."""
from __future__ import annotations
import json
import struct
from typing import Any, Mapping

RUNTIME_PROTOCOL_VERSION = 1
MAX_RUNTIME_MESSAGE = 4 * 1024 * 1024


def encode_runtime(payload: Mapping[str, Any]) -> bytes:
    body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_RUNTIME_MESSAGE: raise ValueError("mensaje runtime demasiado grande")
    return struct.pack("!I", len(body)) + body


def decode_runtime(raw: bytes) -> dict[str, Any]:
    if len(raw) < 4: raise ValueError("respuesta runtime truncada")
    size = struct.unpack("!I", raw[:4])[0]
    if size > MAX_RUNTIME_MESSAGE or len(raw) != size + 4: raise ValueError("framing runtime inválido")
    value = json.loads(raw[4:].decode())
    if not isinstance(value, dict) or value.get("version") != RUNTIME_PROTOCOL_VERSION: raise ValueError("protocolo runtime incompatible")
    return value
