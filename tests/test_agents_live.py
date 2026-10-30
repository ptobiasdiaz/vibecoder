"""M04.1 live local; desactivado y sin inferencia ni escrituras GitHub."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from vibecoder.agents import AgentManagementService
from vibecoder.domain import SpecialistStatus
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


@unittest.skipUnless(
    os.environ.get("M041_LIVE") == "I_UNDERSTAND_THIS_MODIFIES_LOCAL_STATE",
    "M04.1 live local requiere opt-in explícito",
)
class AgentManagementLiveTests(unittest.TestCase):
    def service(self):
        repository = SQLiteRepository(Path(os.environ["M041_DATABASE"]).resolve())
        return repository, AgentManagementService(OrchestratorCore(repository))

    def test_check_existing_specialist_and_persistence(self) -> None:
        repository, service = self.service()
        try:
            agent = service.get(os.environ["M041_AGENT_ID"])
            report = service.check(agent.agent_id, Path(os.environ["M041_WORKSPACE"]))
            self.assertIn(agent.status, {SpecialistStatus.ACTIVE, SpecialistStatus.SUSPENDED})
            self.assertTrue(report["consistent"], report["issues"])
            sessions = repository.list("agent_sessions", agent_id=agent.agent_id)
            for session in sessions:
                self.assertEqual(session["agent_id"], agent.agent_id)
        finally:
            repository.close()


if __name__ == "__main__":
    unittest.main()
