"""Codex App Server JSONL adapter, contained entirely inside the Codex plugin."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class CodexProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str | None
    status: str
    text: str
    error: dict[str, Any] | None


@dataclass(frozen=True)
class CodexApprovalReply:
    response: dict[str, Any]
    approval_id: str


class CodexAppServer:
    """One isolated App Server process for one bounded operation."""
    def __init__(self, executable: Path, codex_home: Path, workspace: Path | None,
                 *, timeout: float = 60.0,
                 approval_handler: Callable[[str, dict[str, Any], str], CodexApprovalReply] | None = None):
        self.executable, self.codex_home, self.workspace = executable, codex_home, workspace
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._events: queue.Queue[str | None] = queue.Queue()
        self.approval_handler = approval_handler
        self._approval_confirmations: list[str] = []

    def __enter__(self) -> "CodexAppServer":
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "HOME": str(self.codex_home.parent), "CODEX_HOME": str(self.codex_home)}
        self.process = subprocess.Popen(
            (str(self.executable), "app-server", "--listen", "stdio://"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=str(self.workspace) if self.workspace else str(self.codex_home),
            env=env, close_fds=True)
        assert self.process.stdout is not None
        threading.Thread(target=self._read_lines, args=(self.process.stdout,), daemon=True).start()
        response = self.request("initialize", {"clientInfo": {
            "name": "vibecoder", "title": "vibecoder Codex worker", "version": "0.0.1"},
            "capabilities": {"experimentalApi": True, "requiredHumanApproval": True}})
        if not isinstance(response, dict):
            raise CodexProtocolError("respuesta initialize inválida")
        self.notify("initialized", {})
        return self

    def __exit__(self, *_: object) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
        if self.process:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream: stream.close()

    def _read_lines(self, stream: Any) -> None:
        try:
            for line in stream: self._events.put(line)
        finally: self._events.put(None)

    def _write(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise CodexProtocolError("App Server no iniciado")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _read(self, deadline: float) -> dict[str, Any]:
        if not self.process:
            raise CodexProtocolError("App Server no iniciado")
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise queue.Empty
            line = self._events.get(timeout=remaining)
        except queue.Empty as exc: raise TimeoutError("timeout esperando evento Codex") from exc
        if not line:
            raise CodexProtocolError("App Server terminó sin resultado confirmado")
        try: value = json.loads(line)
        except json.JSONDecodeError as exc: raise CodexProtocolError("evento App Server no es JSON") from exc
        if not isinstance(value, dict): raise CodexProtocolError("evento App Server inválido")
        if self._approval_confirmations and self.approval_handler is not None:
            confirm = getattr(self.approval_handler, "confirm", None)
            if callable(confirm):
                pending, self._approval_confirmations = self._approval_confirmations, []
                for approval_id in pending: confirm(approval_id)
        if "id" in value and isinstance(value.get("method"), str):
            if self.approval_handler is None: raise CodexProtocolError("solicitud de aprobación sin mediador")
            params = value.get("params")
            if not isinstance(params, dict): raise CodexProtocolError("parámetros de aprobación inválidos")
            reply = self.approval_handler(str(value["method"]), params, json.dumps(value["id"], sort_keys=True))
            self._write({"id": value["id"], "result": reply.response})
            self._approval_confirmations.append(reply.approval_id)
            return self._read(deadline)
        return value

    def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id; self._next_id += 1
        self._write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            event = self._read(deadline)
            if event.get("id") != request_id: continue
            if "error" in event: raise CodexProtocolError(str(event["error"]))
            return event.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def start_thread(self) -> str:
        result = self.request("thread/start", {"cwd": str(self.workspace) if self.workspace else None,
            "approvalPolicy": "on-request", "approvalsReviewer": "user",
            "sandbox": "read-only"})
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id: raise CodexProtocolError("thread/start sin ID")
        return thread_id

    def resume_thread(self, thread_id: str) -> None:
        result = self.request("thread/resume", {"threadId": thread_id})
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise CodexProtocolError("thread/resume no confirmó la sesión")

    def execute(self, instructions: str, thread_id: str | None = None) -> CodexTurnResult:
        if thread_id: self.resume_thread(thread_id)
        else: thread_id = self.start_thread()
        started = self.request("turn/start", {"threadId": thread_id,
            "input": [{"type": "text", "text": instructions}]})
        turn = started.get("turn") if isinstance(started, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) and isinstance(turn.get("id"), str) else None
        deadline, chunks = time.monotonic() + self.timeout, []
        while True:
            event = self._read(deadline); method = event.get("method"); params = event.get("params", {})
            if method == "item/agentMessage/delta" and isinstance(params, dict):
                delta = params.get("delta")
                if isinstance(delta, str): chunks.append(delta)
            if method != "turn/completed" or not isinstance(params, dict): continue
            completed = params.get("turn")
            if not isinstance(completed, dict): raise CodexProtocolError("turn/completed inválido")
            completed_id = completed.get("id") if isinstance(completed.get("id"), str) else turn_id
            status = completed.get("status")
            if not isinstance(status, str): raise CodexProtocolError("turn/completed sin estado")
            error = completed.get("error") if isinstance(completed.get("error"), dict) else None
            return CodexTurnResult(thread_id, completed_id, status, "".join(chunks), error)
