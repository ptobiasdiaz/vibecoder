"""M03.3 live; desactivado por defecto y sin ninguna operación Git remota."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

from vibecoder.generation import GenerationService
from vibecoder.runtime_config import load_account_configs
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


@unittest.skipUnless(
    os.environ.get("M033_LIVE") == "I_UNDERSTAND_CODEX_WILL_GENERATE_LOCAL_FILES",
    "M03.3 live requires explicit opt-in and consumes Codex quota",
)
class GenerationLiveTests(unittest.TestCase):
    def test_prepare_generate_commit_and_recover(self) -> None:
        database = Path(os.environ["M033_DATABASE"]).resolve()
        workspace_root = Path(os.environ["M033_WORKSPACE_ROOT"]).resolve()
        accounts = load_account_configs(os.environ["M033_ACCOUNTS"])
        project_id = os.environ["M033_PROJECT_ID"]
        with SQLiteRepository(database) as repository:
            service = GenerationService(
                OrchestratorCore(repository),
                accounts,
                workspace_root,
                orchestrator_root=Path.cwd().resolve(),
            )
            generation = service.prepare(project_id)
            generation = asyncio.run(service.generate(generation.id))
            self.assertEqual(generation.status.value, "READY_TO_PUBLISH")
            self.assertIsNotNone(generation.commit_hash)
        with SQLiteRepository(database) as repository:
            recovered = GenerationService(
                OrchestratorCore(repository),
                accounts,
                workspace_root,
                orchestrator_root=Path.cwd().resolve(),
            ).recover(generation.id)
            self.assertEqual(recovered.commit_hash, generation.commit_hash)


if __name__ == "__main__":
    unittest.main()
