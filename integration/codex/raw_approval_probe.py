"""Opt-in protocol probe: print only JSON-RPC method/shape and deny approvals."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "HOME": str(args.codex_home.parent), "CODEX_HOME": str(args.codex_home)}
    process = subprocess.Popen((str(args.executable), "app-server", "--listen", "stdio://"),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        bufsize=1, cwd=args.workspace, env=env, close_fds=True)
    assert process.stdin and process.stdout
    next_id = 1

    def send(method: str, params: dict) -> int:
        nonlocal next_id
        ident = next_id; next_id += 1
        process.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
        process.stdin.flush(); return ident

    initialize = send("initialize", {"clientInfo": {"name": "vibecoder-probe",
        "title": "vibecoder approval probe", "version": "0.0.1"},
        "capabilities": {"experimentalApi": True, "requiredHumanApproval": True}})
    thread_id = None; deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line: break
        value = json.loads(line); method = value.get("method")
        params = value.get("params", {}) if isinstance(value.get("params"), dict) else {}
        item = params.get("item", {}) if isinstance(params.get("item"), dict) else {}
        print(json.dumps({"id": value.get("id"), "method": method,
            "parameter_keys": sorted(value.get("params", {})) if isinstance(value.get("params"), dict) else [],
            "has_error": "error" in value, "item_type": item.get("type"),
            "item_status": item.get("status"), "item_error": item.get("error")}), flush=True)
        if method in {"configWarning", "warning"}:
            print(json.dumps({"diagnostic": params}, ensure_ascii=False), flush=True)
        if method == "item/agentMessage/delta":
            print(json.dumps({"agent_delta": params.get("delta")}, ensure_ascii=False), flush=True)
        if value.get("id") == initialize:
            process.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n"); process.stdin.flush()
            send("thread/start", {"cwd": str(args.workspace), "approvalPolicy": "on-request",
                "approvalsReviewer": "user", "sandbox": "read-only"})
        elif value.get("id") == 2 and isinstance(value.get("result"), dict):
            thread_id = value["result"]["thread"]["id"]
            send("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text":
                "Ejecuta exactamente `printf 'approval-raw-probe\\n' > raw-probe.txt`. No hagas nada más."}]})
        elif isinstance(method, str) and method.endswith("/requestApproval") and "id" in value:
            process.stdin.write(json.dumps({"id": value["id"], "result": {"decision": "decline"}}) + "\n")
            process.stdin.flush()
        elif method == "turn/completed":
            return 0
    return 1


if __name__ == "__main__":
    try: raise SystemExit(main())
    finally: pass
