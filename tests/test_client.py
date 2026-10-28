from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from m00_codex.client import AppServerClient, RpcError


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "codex-home"
        self.wrapper = Path(self.temp.name) / "codex"
        self.wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -m tests.fake_codex\n")
        self.wrapper.chmod(self.wrapper.stat().st_mode | stat.S_IXUSR)
        self.client = AppServerClient(self.wrapper, codex_home=self.home, cwd=Path.cwd())
        await self.client.start()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temp.cleanup()

    async def test_handshake_thread_and_resume(self) -> None:
        started = await self.client.start_thread(cwd=os.getcwd())
        thread_id = started["thread"]["id"]
        resumed = await self.client.resume_thread(thread_id)
        self.assertEqual(resumed["thread"]["id"], thread_id)

    async def test_fake_server_rejects_invalid_thread_start_enums(self) -> None:
        with self.assertRaises(RpcError) as raised:
            await self.client.start_thread(
                approvalPolicy="never", sandbox="workspaceWrite"
            )
        self.assertEqual(raised.exception.error["code"], -32600)

    async def test_turn_streams_text_and_final_status(self) -> None:
        started = await self.client.start_thread()
        result = await self.client.run_turn(started["thread"]["id"], "ping")
        self.assertEqual(result.text, "pong")
        self.assertEqual(result.status, "completed")

    async def test_two_sessions_can_run_concurrently(self) -> None:
        import asyncio

        one = (await self.client.start_thread())["thread"]["id"]
        two = (await self.client.start_thread())["thread"]["id"]
        results = await asyncio.gather(self.client.run_turn(one, "one"), self.client.run_turn(two, "two"))
        self.assertEqual([r.status for r in results], ["completed", "completed"])

    async def test_error_categories(self) -> None:
        for message, category in [
            ("authentication required", "authentication"),
            ("quota usage limit reached", "quota"),
            ("tool execution failed", "execution"),
        ]:
            with self.assertRaises(RpcError) as raised:
                await self.client.request("test/error", {"message": message})
            self.assertEqual(raised.exception.category, category)

    async def test_interrupt_reports_interrupted_final_state(self) -> None:
        thread_id = (await self.client.start_thread())["thread"]["id"]
        turn = await self.client.start_turn(thread_id, "wait")
        turn_id = turn["turn"]["id"]
        await self.client.interrupt_turn(thread_id, turn_id)
        event = await self.client.next_event(timeout=1)
        self.assertEqual(event["method"], "turn/completed")
        self.assertEqual(event["params"]["turn"]["status"], "interrupted")

    async def test_codex_home_is_explicit_and_created(self) -> None:
        self.assertTrue(self.home.is_dir())


if __name__ == "__main__":
    unittest.main()

