"""Servidor JSONL simulado; se ejecuta como sustituto del binario codex."""

from __future__ import annotations

import json
import sys
import time


def emit(message: object) -> None:
    print(json.dumps(message), flush=True)


def main() -> None:
    threads: dict[str, dict[str, object]] = {}
    active: dict[str, str] = {}
    for raw in sys.stdin:
        message = json.loads(raw)
        method = message["method"]
        request_id = message.get("id")
        params = message.get("params", {})
        if request_id is None:
            continue
        if method == "initialize":
            result = {"userAgent": "fake-codex/0.1"}
        elif method == "thread/start":
            thread_id = f"thr_{len(threads) + 1}"
            threads[thread_id] = {"id": thread_id, "ephemeral": False}
            result = {"thread": threads[thread_id]}
        elif method == "thread/resume":
            thread_id = params["threadId"]
            result = {"thread": threads.get(thread_id, {"id": thread_id})}
        elif method == "thread/read":
            result = {"thread": threads[params["threadId"]]}
        elif method == "thread/list":
            result = {"data": list(threads.values()), "nextCursor": None}
        elif method == "turn/start":
            turn_id = f"turn_{len(active) + 1}"
            active[params["threadId"]] = turn_id
            result = {"turn": {"id": turn_id, "status": "inProgress"}}
            emit({"id": request_id, "result": result})
            text = params.get("input", [{}])[0].get("text", "")
            if text == "wait":
                continue
            emit({"method": "item/agentMessage/delta", "params": {"threadId": params["threadId"], "turnId": turn_id, "delta": "pong"}})
            time.sleep(0.01)
            emit({"method": "turn/completed", "params": {"threadId": params["threadId"], "turn": {"id": turn_id, "status": "completed"}}})
            continue
        elif method == "turn/interrupt":
            result = {}
            emit({"id": request_id, "result": result})
            emit({"method": "turn/completed", "params": {"threadId": params["threadId"], "turn": {"id": params["turnId"], "status": "interrupted"}}})
            continue
        elif method == "account/rateLimits/read":
            result = {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 1}}}
        elif method == "test/error":
            emit({"id": request_id, "error": {"code": -32000, "message": params["message"]}})
            continue
        else:
            emit({"id": request_id, "error": {"code": -32601, "message": "method not found"}})
            continue
        emit({"id": request_id, "result": result})


if __name__ == "__main__":
    main()

