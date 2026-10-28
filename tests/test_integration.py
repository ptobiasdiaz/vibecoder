"""Integración real, deshabilitada salvo M00_CODEX_INTEGRATION=1."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from m00_codex.client import AppServerClient, RpcError
from m00_codex.diagnostic import find_codex


@unittest.skipUnless(os.environ.get("M00_CODEX_INTEGRATION") == "1", "integración opt-in")
class RealAppServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_thread_is_not_persisted_before_first_turn(self) -> None:
        codex = find_codex(os.environ.get("M00_CODEX_BIN"))
        self.assertIsNotNone(codex)
        with tempfile.TemporaryDirectory(prefix="vibecoder-m00-it-") as raw_home:
            home = Path(raw_home)
            first = AppServerClient(codex, codex_home=home, cwd=Path.cwd())
            await first.start()
            thread_id = (await first.start_thread(cwd=os.getcwd()))["thread"]["id"]
            await first.close()
            second = AppServerClient(codex, codex_home=home, cwd=Path.cwd())
            await second.start()
            with self.assertRaises(RpcError) as raised:
                await second.resume_thread(thread_id)
            self.assertIn("no rollout found", str(raised.exception))
            await second.close()

    @unittest.skipUnless(os.environ.get("M00_CODEX_LIVE") == "1", "turno real opt-in")
    async def test_persist_after_turn_and_resume_after_restart(self) -> None:
        codex = find_codex(os.environ.get("M00_CODEX_BIN"))
        configured_home = os.environ.get("M00_CODEX_HOME")
        self.assertIsNotNone(configured_home, "M00_CODEX_HOME aislado es obligatorio")
        home = Path(configured_home).expanduser().resolve()
        self.assertNotEqual(home, Path.home() / ".codex", "no usar el CODEX_HOME habitual")
        first = AppServerClient(codex, codex_home=home, cwd=Path.cwd())
        try:
            await first.start()
            thread_id = (await first.start_thread(cwd=os.getcwd()))["thread"]["id"]
            result = await first.run_turn(thread_id, "Responde únicamente: M00_OK")
            self.assertEqual(result.status, "completed", result.turn.get("error"))
        finally:
            await first.close()
        second = AppServerClient(codex, codex_home=home, cwd=Path.cwd())
        try:
            await second.start()
            resumed = await second.resume_thread(thread_id)
            self.assertEqual(resumed["thread"]["id"], thread_id)
        finally:
            await second.close()


if __name__ == "__main__":
    unittest.main()
