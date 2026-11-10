"""Trusted fake provider runtime used only by offline contract tests."""
from __future__ import annotations
import json
import os
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from .runtime_protocol import MAX_RUNTIME_MESSAGE, RUNTIME_PROTOCOL_VERSION, encode_runtime


def _receive() -> dict[str, Any]:
    header = sys.stdin.buffer.read(4)
    if len(header) != 4: raise ValueError("missing frame")
    size = struct.unpack("!I", header)[0]
    if size > MAX_RUNTIME_MESSAGE: raise ValueError("large frame")
    value = json.loads(sys.stdin.buffer.read(size))
    if not isinstance(value, dict) or value.get("version") != RUNTIME_PROTOCOL_VERSION: raise ValueError("bad protocol")
    return value


def _result(request: dict[str, Any]) -> dict[str, Any]:
    operation, arguments = request.get("operation"), request.get("arguments", {})
    mode = arguments.get("mode", "success")
    if mode == "crash": os._exit(17)
    if mode == "timeout": time.sleep(float(arguments.get("seconds", 5)))
    if operation == "probe":
        if mode == "unavailable": return {"available": False, "capabilities": [], "diagnostic": "simulated unavailable"}
        return {"available": True, "authenticated": mode != "auth-required",
                "capabilities": arguments.get("capabilities", ["STRUCTURED_OUTPUT", "RECONCILIATION", "CANCELLATION", "PERSISTENT_SESSIONS", "SESSION_CONTINUATION"])}
    if operation == "create_session":
        return {"provider_reference": f"fake-session-{uuid.uuid4()}", "continuity": {"fake": True}}
    if operation == "continue_session": return {"continued": True, "provider_reference": arguments["provider_reference"]}
    if operation == "execute":
        outcomes = {"success": "SUCCEEDED", "failure": "FAILED", "unknown": "UNKNOWN",
                    "quota": "WAITING_QUOTA", "unsupported": "UNSUPPORTED"}
        outcome = outcomes.get(mode, "SUCCEEDED")
        return {"outcome": outcome, "provider_execution_reference": arguments.get("provider_execution_reference") or f"fake-exec-{uuid.uuid4()}",
                "structured_result": {"echo": arguments.get("instructions")},
                "error_category": None if outcome == "SUCCEEDED" else mode.upper()}
    if operation == "observe":
        return {"outcome": arguments.get("observed_outcome", "SUCCEEDED"),
                "provider_execution_reference": arguments["provider_reference"], "structured_result": {"observed": True}}
    if operation == "cancel":
        if mode == "unsupported": return {"outcome": "UNSUPPORTED", "provider_execution_reference": arguments["provider_reference"]}
        return {"outcome": "CANCELLED", "provider_execution_reference": arguments["provider_reference"]}
    if operation == "isolation_probe":
        results = {}
        for raw in arguments.get("paths", []):
            path = Path(str(raw))
            try:
                if path.is_dir(): next(path.iterdir(), None)
                else:
                    with path.open("rb") as stream: stream.read(1)
            except (OSError, PermissionError) as exc:
                results[str(path)] = f"DENIED:{type(exc).__name__}:{getattr(exc, 'errno', '')}"
            else: results[str(path)] = "ACCESSIBLE"
        results["effective_ids"] = {"uid": os.geteuid(), "gid": os.getegid(), "groups": os.getgroups()}
        results["environment_keys"] = sorted(os.environ)
        try: results["open_fds"] = sorted(int(x) for x in os.listdir("/proc/self/fd"))
        except OSError: results["open_fds"] = []
        return results
    raise ValueError("unknown operation")


def main() -> int:
    try:
        request = _receive(); result = _result(request)
        response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": request.get("request_id"),
                    "worker_id": request.get("worker_id"), "ok": True, "result": result}
    except Exception as exc:
        response = {"version": RUNTIME_PROTOCOL_VERSION, "request_id": locals().get("request", {}).get("request_id"),
                    "worker_id": locals().get("request", {}).get("worker_id"), "ok": False,
                    "error": {"category": type(exc).__name__, "message": "fake runtime request failed"}}
    sys.stdout.buffer.write(encode_runtime(response)); sys.stdout.buffer.flush(); return 0

if __name__ == "__main__": raise SystemExit(main())
