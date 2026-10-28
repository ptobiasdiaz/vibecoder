"""Ejemplo M01: crear el núcleo, cerrarlo y recuperar sus registros."""

from __future__ import annotations

import tempfile
from pathlib import Path

from vibecoder import OrchestratorCore, SQLiteRepository


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vibecoder-m01-") as directory:
        database = Path(directory) / "orchestrator.sqlite3"

        with SQLiteRepository(database) as repository:
            core = OrchestratorCore(repository)
            account = core.register_account(
                "cuenta de ejemplo", "CHATGPT", "codex-home://example"
            )
            project = core.register_project("proyecto de ejemplo", account.id)
            agent = core.register_agent(
                project.id,
                "main",
                "PRINCIPAL",
                config_ref="examples/agent-registry.yaml#main",
                documentation_ref="README.md",
            )
            task = core.create_task(
                project.id,
                agent.id,
                "BOOTSTRAP",
                idempotency_key="example-bootstrap",
            )

        with SQLiteRepository(database) as repository:
            recovered = OrchestratorCore(repository)
            loaded = recovered.get_task(task.id)
            print(f"Proyecto recuperado: {recovered.get_project(project.id).name}")
            print(f"Tarea recuperada: {loaded.id} [{loaded.status}]")


if __name__ == "__main__":
    main()
