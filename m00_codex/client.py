"""Cliente mínimo del protocolo oficial JSONL de Codex App Server.

Este módulo es deliberadamente independiente del futuro orquestador. No interpreta
salida de terminal: habla el protocolo de app-server por stdin/stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence


class AppServerError(RuntimeError):
    """Error base del cliente o proceso App Server."""


class ProtocolError(AppServerError):
    """Mensaje inválido o cierre inesperado del transporte."""


class RpcError(AppServerError):
    """Respuesta de error JSON-RPC del servidor."""

    def __init__(self, error: Mapping[str, Any], method: str | None = None):
        self.error = dict(error)
        self.method = method
        code = self.error.get("code", "unknown")
        message = self.error.get("message", "error RPC sin mensaje")
        super().__init__(f"{method or 'RPC'}: [{code}] {message}")

    @property
    def category(self) -> str:
        """Clasificación estable y conservadora para observabilidad experimental."""
        text = f"{self.error.get('code', '')} {self.error.get('message', '')}".lower()
        if any(word in text for word in ("auth", "credential", "unauthorized", "login")):
            return "authentication"
        if any(word in text for word in ("quota", "rate limit", "usage limit", "credit")):
            return "quota"
        return "execution"


@dataclass(frozen=True)
class TurnResult:
    thread_id: str
    turn_id: str
    status: str
    text: str
    turn: Mapping[str, Any]


class AppServerClient:
    """Una conexión asíncrona a un proceso ``codex app-server --listen stdio://``."""

    def __init__(
        self,
        codex: str | os.PathLike[str] = "codex",
        *,
        codex_home: str | os.PathLike[str],
        cwd: str | os.PathLike[str] | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        home = Path(codex_home).expanduser()
        if not home.is_absolute():
            raise ValueError("codex_home debe ser una ruta absoluta")
        self.codex = os.fspath(codex)
        self.codex_home = home
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.extra_args = tuple(extra_args)
        self.env = dict(env or {})
        self.request_timeout = request_timeout
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, tuple[str, asyncio.Future[Mapping[str, Any]]]] = {}
        self._events: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
        self._turn_events: defaultdict[str, asyncio.Queue[Mapping[str, Any]]] = defaultdict(
            asyncio.Queue
        )
        self._stderr: list[str] = []
        self._stderr_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> AppServerClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> Mapping[str, Any]:
        if self.process is not None:
            raise AppServerError("el cliente ya está iniciado")
        self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        child_env = os.environ.copy()
        child_env.update(self.env)
        child_env["CODEX_HOME"] = os.fspath(self.codex_home)
        self.process = await asyncio.create_subprocess_exec(
            self.codex,
            "app-server",
            "--listen",
            "stdio://",
            *self.extra_args,
            cwd=self.cwd,
            env=child_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            result = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "vibecoder_m00",
                        "title": "vibecoder M00 diagnostics",
                        "version": "0.1.0",
                    },
                    "capabilities": {},
                },
            )
            await self.notify("initialized", {})
            return result
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            if process.stdin:
                process.stdin.close()
                with contextlib.suppress(Exception):
                    await process.stdin.wait_closed()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), 3)
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._reader_task = self._stderr_task = None
        self._fail_pending(ProtocolError("App Server cerrado"))

    @property
    def stderr(self) -> str:
        return "".join(self._stderr)

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        self._pending[request_id] = (method, future)
        await self._send({"id": request_id, "method": method, "params": dict(params or {})})
        try:
            return await asyncio.wait_for(future, self.request_timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        await self._send({"method": method, "params": dict(params or {})})

    async def start_thread(self, **params: Any) -> Mapping[str, Any]:
        return await self.request("thread/start", params)

    async def resume_thread(self, thread_id: str, *, exclude_turns: bool = True) -> Mapping[str, Any]:
        return await self.request(
            "thread/resume", {"threadId": thread_id, "excludeTurns": exclude_turns}
        )

    async def read_thread(self, thread_id: str) -> Mapping[str, Any]:
        return await self.request("thread/read", {"threadId": thread_id})

    async def list_threads(self, *, limit: int = 20) -> Mapping[str, Any]:
        return await self.request("thread/list", {"limit": limit})

    async def rate_limits(self) -> Mapping[str, Any]:
        return await self.request("account/rateLimits/read")

    async def account_usage(self) -> Mapping[str, Any]:
        return await self.request("account/usage/read")

    async def start_turn(self, thread_id: str, text: str, **params: Any) -> Mapping[str, Any]:
        payload = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
        payload.update(params)
        return await self.request("turn/start", payload)

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> Mapping[str, Any]:
        return await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def run_turn(self, thread_id: str, text: str, **params: Any) -> TurnResult:
        started = await self.start_turn(thread_id, text, **params)
        turn_id = str(started["turn"]["id"])
        deltas: list[str] = []
        try:
            while True:
                event = await self._turn_events[turn_id].get()
                method = event.get("method")
                event_params = event.get("params", {})
                if method == "item/agentMessage/delta":
                    deltas.append(str(event_params.get("delta", "")))
                if method == "turn/completed":
                    turn = event_params.get("turn", {})
                    return TurnResult(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        status=str(turn.get("status", "unknown")),
                        text="".join(deltas),
                        turn=turn,
                    )
        finally:
            self._turn_events.pop(turn_id, None)

    async def next_event(self, timeout: float | None = None) -> Mapping[str, Any]:
        if timeout is None:
            return await self._events.get()
        return await asyncio.wait_for(self._events.get(), timeout)

    async def events(self) -> AsyncIterator[Mapping[str, Any]]:
        while True:
            yield await self.next_event()

    async def _send(self, message: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise AppServerError("App Server no está iniciado")
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ProtocolError(f"JSONL inválido de App Server: {exc}") from exc
                if "id" in message and (pending := self._pending.get(message["id"])):
                    method, future = pending
                    if "error" in message:
                        future.set_exception(RpcError(message["error"], method))
                    else:
                        future.set_result(message.get("result", {}))
                elif "method" in message:
                    await self._events.put(message)
                    params = message.get("params", {})
                    turn_id = params.get("turnId")
                    if turn_id is None and isinstance(params.get("turn"), Mapping):
                        turn_id = params["turn"].get("id")
                    if turn_id:
                        await self._turn_events[str(turn_id)].put(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fail_pending(exc if isinstance(exc, Exception) else ProtocolError(str(exc)))
        else:
            code = await self.process.wait()
            self._fail_pending(ProtocolError(f"App Server terminó inesperadamente ({code})"))

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while chunk := await self.process.stderr.read(4096):
            self._stderr.append(chunk.decode(errors="replace"))

    def _fail_pending(self, exc: Exception) -> None:
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(exc)

