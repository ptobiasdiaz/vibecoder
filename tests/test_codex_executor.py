from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from vibecoder import OrchestratorCore, SQLiteRepository
from vibecoder.codex_executor import AccountRuntimeConfig, CodexAppServerExecutor
from vibecoder.domain import AttemptStatus, SessionPersistenceState, TaskStatus
from vibecoder.worker import LocalWorker


class RecordingClient:
    def __init__(self, *args: object, **kwargs: object):
        self.thread_start_params: dict[str, object] | None = None

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def start_thread(self, **params: object) -> dict[str, object]:
        self.thread_start_params = params
        return {"thread": {"id": "thr_recorded"}}


class CodexExecutorProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_start_uses_official_protocol_enum_values(self) -> None:
        from unittest.mock import patch

        config = AccountRuntimeConfig(
            account_id="test",
            codex_home=Path("/tmp/vibecoder-test-codex-home"),
            cwd=Path.cwd().resolve(),
        )
        with patch("vibecoder.codex_executor.AppServerClient", RecordingClient):
            executor = CodexAppServerExecutor(config)
            await executor.start()
            try:
                thread_id, created = await executor.ensure_thread(None)
                client = executor.client
                self.assertIsInstance(client, RecordingClient)
                self.assertEqual(
                    client.thread_start_params,
                    {
                        "cwd": str(config.cwd),
                        "approvalPolicy": "never",
                        "sandbox": "workspace-write",
                    },
                )
                self.assertEqual(thread_id, "thr_recorded")
                self.assertTrue(created)
            finally:
                await executor.close()

    async def test_starts_protocol_executes_and_reaps_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "codex"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -m tests.fake_codex\n")
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
            executor = CodexAppServerExecutor(
                AccountRuntimeConfig(
                    account_id="test",
                    codex_home=root / "home",
                    codex_executable=os.fspath(wrapper),
                    cwd=Path.cwd().resolve(),
                    timeout_seconds=2,
                )
            )
            await executor.start()
            process = executor.client.process
            thread_id, created = await executor.ensure_thread(None)
            turn_id = await executor.start_turn(thread_id, "ping")
            result = await executor.wait_turn(thread_id, turn_id)
            await executor.close()
            self.assertTrue(created)
            self.assertEqual(result.status, "completed")
            self.assertIsNotNone(process.returncode)


@unittest.skipUnless(os.environ.get("M02_CODEX_LIVE") == "1", "M02 live opt-in; consume cuota")
class CodexExecutorLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_turn_and_resume(self) -> None:
        home = os.environ.get("M02_CODEX_HOME")
        self.assertIsNotNone(home, "M02_CODEX_HOME aislado y autenticado es obligatorio")
        home_path = Path(home).expanduser().resolve()
        self.assertNotEqual(home_path, Path.home() / ".codex")
        config = AccountRuntimeConfig(
            account_id="live",
            codex_home=home_path,
            codex_executable=os.environ.get("M02_CODEX_BIN", "codex"),
            cwd=Path.cwd().resolve(),
            timeout_seconds=120,
        )
        first = CodexAppServerExecutor(config)
        await first.start()
        try:
            thread_id, _ = await first.ensure_thread(None)
            turn_id = await first.start_turn(thread_id, "Responde únicamente: M02_OK")
            result = await first.wait_turn(thread_id, turn_id)
            self.assertEqual(result.status, "completed", result.error)
        finally:
            await first.close()
        second = CodexAppServerExecutor(config)
        await second.start()
        try:
            resumed, created = await second.ensure_thread(thread_id)
            self.assertEqual(resumed, thread_id)
            self.assertFalse(created)
        finally:
            await second.close()

    async def test_worker_persists_result_and_session_across_restart(self) -> None:
        configured_home = os.environ.get("M02_CODEX_HOME")
        self.assertIsNotNone(configured_home, "M02_CODEX_HOME aislado es obligatorio")
        with tempfile.TemporaryDirectory(prefix="vibecoder-m02-live-") as directory:
            database = Path(directory) / "worker.sqlite3"
            repository = SQLiteRepository(database)
            core = OrchestratorCore(repository)
            account = core.register_account("live", "CHATGPT", "codex-home://m02-live")
            project = core.register_project("live", account.id)
            agent = core.register_agent(
                project.id,
                "main",
                "PRINCIPAL",
                config_ref="tests/live#main",
                documentation_ref="docs/milestones/M02.md",
            )
            task = core.create_task(
                project.id,
                agent.id,
                "CODEX",
                prompt="Responde únicamente: M02_OK",
                idempotency_key="m02-live-once",
            )
            config = AccountRuntimeConfig(
                account_id=account.id,
                codex_home=Path(configured_home).expanduser().resolve(),
                codex_executable=os.environ.get("M02_CODEX_BIN", "codex"),
                cwd=Path.cwd().resolve(),
                timeout_seconds=120,
            )
            outcome = await LocalWorker(core, {account.id: config}).run_once()
            self.assertEqual(outcome.status, TaskStatus.COMPLETED)
            attempt = core.list_attempts(task.id)[0]
            session = core.get_or_create_session(agent.id)
            self.assertEqual(attempt.status, AttemptStatus.SUCCEEDED)
            self.assertTrue(attempt.result_text)
            self.assertEqual(session.persistence_state, SessionPersistenceState.RECOVERABLE)
            repository.close()

            repository = SQLiteRepository(database)
            core = OrchestratorCore(repository)
            recovered_session = core.get_session(session.id)
            self.assertEqual(recovered_session.codex_thread_id, attempt.codex_thread_id)
            executor = CodexAppServerExecutor(config)
            await executor.start()
            try:
                resumed, created = await executor.ensure_thread(recovered_session.codex_thread_id)
                self.assertEqual(resumed, recovered_session.codex_thread_id)
                self.assertFalse(created)
            finally:
                await executor.close()
                repository.close()


if __name__ == "__main__":
    unittest.main()
