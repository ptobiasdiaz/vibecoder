from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from vibecoder.agents import AgentManagementService
from vibecoder.domain import (
    AgentRevisionStatus,
    AgentStatus,
    ProjectStatus,
    SpecialistStatus,
)
from vibecoder.errors import ConflictError, InvalidTransition, ValidationError
from vibecoder.service import OrchestratorCore
from vibecoder.sqlite import SQLiteRepository


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=workspace, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": __import__("os").environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout.strip()


class AgentManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "agents.sqlite3"
        self.workspace = root / "project"
        self.workspace.mkdir()
        git(self.workspace, "init", "-b", "main")
        (self.workspace / "AGENTS.md").write_text(
            "# Project rules\n\n- Rule: network = denied\n", encoding="utf-8"
        )
        git(self.workspace, "add", "AGENTS.md")
        git(self.workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial")
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.account = self.core.register_account("primary", "CHATGPT", "codex-home://primary")
        self.second = self.core.register_account("second", "API_KEY", "codex-home://second")
        self.project = self.core.register_project(
            "active", self.account.id, status=ProjectStatus.ACTIVE
        )
        self.service = AgentManagementService(self.core)

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    def propose(self, logical_id: str = "backend", instructions: str | None = None):
        return self.service.propose(
            self.project.id, logical_id, "Backend", "Backend engineering",
            ("Implement approved backend work",), ("Edit backend files",),
            ("Do not access the network",),
            instructions or "# Backend\n\n- Rule: network = denied\n",
            self.account.id, self.workspace, actor="operator:test",
        )

    def integrate(self, agent_id: str) -> None:
        self.service.validate(agent_id, self.workspace, actor="operator:test")
        git(self.workspace, "add", ".vibecoder")
        git(self.workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "add specialist")

    def activate(self, agent_id: str):
        return self.service.activate(
            agent_id, self.workspace, actor="operator:test", authorized=True
        )

    def test_proposal_has_stable_identity_revision_and_audit_history(self) -> None:
        specialist, revision = self.propose()
        self.assertEqual(specialist.status, SpecialistStatus.PROPOSED)
        self.assertEqual(revision.status, AgentRevisionStatus.PROPOSED)
        self.assertEqual(self.service.get_by_logical_id(self.project.id, "backend").agent_id,
                         specialist.agent_id)
        self.assertTrue((self.workspace / revision.instructions_path).is_file())
        self.assertEqual(self.core.get_agent(specialist.agent_id).status, AgentStatus.INACTIVE)
        self.assertEqual(self.service.history(specialist.agent_id)[0]["operation"], "PROPOSE")

    def test_activation_requires_authorization_validation_and_integrated_file(self) -> None:
        specialist, _ = self.propose()
        with self.assertRaises(ConflictError):
            self.service.activate(specialist.agent_id, self.workspace,
                                  actor="operator:test", authorized=False)
        self.assertEqual(self.service.history(specialist.agent_id)[-1]["result"], "REJECTED")
        self.service.validate(specialist.agent_id, self.workspace, actor="operator:test")
        with self.assertRaises(ConflictError):
            self.activate(specialist.agent_id)
        git(self.workspace, "add", ".vibecoder")
        git(self.workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "approve specialist")
        active = self.activate(specialist.agent_id)
        revision = self.service.active_revision(active.agent_id)
        self.assertEqual(active.status, SpecialistStatus.ACTIVE)
        self.assertIsNotNone(revision.source_commit)
        context = self.service.build_context(active.agent_id, self.workspace, "Do one task")
        self.assertIn("Instrucciones específicas", context.text)
        self.assertNotIn("# Project rules", context.text)
        self.assertIn("AGENTS.md", context.versions)

    def test_configuration_update_is_versioned_and_requires_new_integration(self) -> None:
        specialist, first = self.propose()
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        same, second = self.propose(
            instructions="# Backend v2\n\n- Rule: network = denied\n"
        )
        self.assertEqual(same.agent_id, specialist.agent_id)
        self.assertEqual(second.version, first.version + 1)
        self.assertNotEqual(second.configuration_hash, first.configuration_hash)
        self.assertEqual(self.service.active_revision(specialist.agent_id).id, first.id)
        with self.assertRaises(ConflictError):
            self.service.build_context(specialist.agent_id, self.workspace, "task")
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        self.assertEqual(
            self.service.revision(first.id).status, AgentRevisionStatus.SUPERSEDED
        )

    def test_explicit_instruction_conflict_and_symlink_are_rejected(self) -> None:
        specialist, _ = self.propose(instructions="# Backend\n\n- Rule: network = allowed\n")
        with self.assertRaises(ConflictError):
            self.service.validate(specialist.agent_id, self.workspace, actor="operator:test")
        path = self.workspace / ".vibecoder" / "agents" / "backend" / "AGENTS.md"
        path.unlink()
        path.symlink_to(self.workspace / "AGENTS.md")
        with self.assertRaises(ValidationError):
            self.service.validate(specialist.agent_id, self.workspace, actor="operator:test")

    def test_lifecycle_preserves_identity_and_retired_identifier(self) -> None:
        specialist, _ = self.propose()
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        suspended = self.service.suspend(
            specialist.agent_id, actor="operator:test", authorized=True
        )
        self.assertEqual(suspended.status, SpecialistStatus.SUSPENDED)
        resumed = self.service.resume(
            specialist.agent_id, self.workspace, actor="operator:test", authorized=True
        )
        self.assertEqual(resumed.status, SpecialistStatus.ACTIVE)
        retired = self.service.retire(
            specialist.agent_id, actor="operator:test", authorized=True
        )
        self.assertEqual(retired.status, SpecialistStatus.RETIRED)
        with self.assertRaises(ConflictError):
            self.propose()
        with self.assertRaises(InvalidTransition):
            self.service.resume(
                specialist.agent_id, self.workspace, actor="operator:test", authorized=True
            )

    def test_account_change_retains_old_session_and_uses_new_account(self) -> None:
        specialist, _ = self.propose()
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        first = self.service.create_session(specialist.agent_id)
        with self.assertRaises(ConflictError):
            self.service.assign_account(
                specialist.agent_id, self.second.id, actor="operator:test", authorized=True
            )
        self.service.suspend(specialist.agent_id, actor="operator:test", authorized=True)
        self.service.assign_account(
            specialist.agent_id, self.second.id, actor="operator:test", authorized=True
        )
        self.service.resume(
            specialist.agent_id, self.workspace, actor="operator:test", authorized=True
        )
        second = self.core.get_or_create_session(specialist.agent_id)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.codex_account_id, self.account.id)
        self.assertEqual(second.codex_account_id, self.second.id)
        self.assertIn(first.id, self.service.check(specialist.agent_id, self.workspace)
                      ["historical_sessions_with_other_account"])

    def test_restart_recovers_agent_and_external_instruction_change_blocks_it(self) -> None:
        specialist, _ = self.propose()
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        self.repository.close()
        self.repository = SQLiteRepository(self.database)
        self.core = OrchestratorCore(self.repository)
        self.service = AgentManagementService(self.core)
        self.assertEqual(self.service.get(specialist.agent_id).status, SpecialistStatus.ACTIVE)
        path = self.workspace / ".vibecoder" / "agents" / "backend" / "AGENTS.md"
        path.write_text("changed\n", encoding="utf-8")
        self.assertFalse(self.service.check(specialist.agent_id, self.workspace)["consistent"])
        with self.assertRaises(ConflictError):
            self.service.build_context(specialist.agent_id, self.workspace, "task")

    def test_requires_active_project_valid_identifier_and_complete_configuration(self) -> None:
        inactive = self.core.register_project("inactive", self.account.id)
        with self.assertRaises(ConflictError):
            self.service.propose(
                inactive.id, "backend", "Backend", "Role", ("one",), ("one",),
                ("one",), "instructions", self.account.id, self.workspace, actor="operator"
            )
        with self.assertRaises(ValidationError):
            self.propose("../escape")
        with self.assertRaises(ValidationError):
            self.propose("main")

    def test_specialist_cannot_receive_tasks_until_active(self) -> None:
        specialist, _ = self.propose()
        with self.assertRaises(ConflictError):
            self.core.create_task(self.project.id, specialist.agent_id, "SPECIALIST")
        self.integrate(specialist.agent_id)
        self.activate(specialist.agent_id)
        with self.assertRaises(ConflictError):
            self.core.create_task(self.project.id, specialist.agent_id, "SPECIALIST")


if __name__ == "__main__":
    unittest.main()
