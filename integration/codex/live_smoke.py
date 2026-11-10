"""Opt-in Codex smoke test against a prepared M06.6.1 installation.

The daemon must be stopped while this program owns SQLite.  It creates only a
synthetic local project/agent/workspace and never contacts GitHub.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
from pathlib import Path

from vibecoder.github_identity import utc_now
from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.worker_runtime import PrivilegedWorkerLauncherClient
from vibecoder.workers import WorkerService


REPOSITORY_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
WORKSPACE_ID = "33333333-3333-3333-3333-333333333333"
AGENT_ID = "44444444-4444-4444-4444-444444444444"
REVISION_ID = "55555555-5555-5555-5555-555555555555"


def seed(repository: SQLiteRepository, workspace: Path) -> None:
    now = utc_now()
    with repository.transaction():
        if not repository.query_one("SELECT 1 FROM github_repositories WHERE id=?", (REPOSITORY_ID,)):
            repository.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)", (
                REPOSITORY_ID, 1, "local-integration-node", "integration", "codex-smoke",
                "integration/codex-smoke", "main", now, now))
        if not repository.query_one("SELECT 1 FROM projects WHERE id=?", (PROJECT_ID,)):
            repository.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)", (
                PROJECT_ID, "codex-smoke", REPOSITORY_ID, "main", "0" * 40,
                "DISABLED", now, now))
        if not repository.query_one("SELECT 1 FROM workspaces WHERE id=?", (WORKSPACE_ID,)):
            repository.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)", (
                WORKSPACE_ID, PROJECT_ID, "integration-smoke", str(workspace), "main",
                "0" * 40, "READY", now, now))
        if not repository.query_one("SELECT 1 FROM agents WHERE id=?", (AGENT_ID,)):
            repository.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)", (
                AGENT_ID, PROJECT_ID, "smoke", "integration", "ACTIVE", 1, None, now, now))
            instructions = "Minimal Codex integration smoke agent."
            repository.execute("INSERT INTO agent_revisions VALUES(?,?,?,?,?,?,?,?,?)", (
                REVISION_ID, AGENT_ID, 1, "[]", "[]", "[]", instructions,
                hashlib.sha256(instructions.encode()).hexdigest(), now))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--worker", default="codex-1")
    parser.add_argument("--second-turn", action="store_true")
    parser.add_argument("--resume-session")
    args = parser.parse_args()
    repository = SQLiteRepository(args.database)
    service: WorkerService | None = None
    try:
        seed(repository, args.workspace.absolute())
        service = WorkerService(repository, PluginRegistry(repository), Path("/var/lib/vibecoder/workers"),
            PrivilegedWorkerLauncherClient(), admin_gid=grp.getgrnam("vibecoder-admin").gr_gid)
        if args.resume_session:
            session = repository.query_one("SELECT * FROM provider_sessions WHERE id=?", (args.resume_session,))
            if session is None: raise SystemExit("sesión solicitada no encontrada")
            result = {"session_id": session["id"]}
        else:
            session = service.create_session(PROJECT_ID, AGENT_ID, "integration-smoke", "m06.6.1",
                                             args.worker, WORKSPACE_ID)
            first = service.execute(str(session["id"]), "m06.6.1-codex-smoke-1",
                "Responde únicamente con la palabra OK. No ejecutes comandos ni modifiques archivos.")
            result = {"session_id": session["id"], "first": {
                "outcome": first["outcome"], "error_category": first["error_category"],
                "result": json.loads(first["result_json"])}}
        if args.second_turn:
            continued = service.continue_session(str(session["id"]))
            second = service.execute(str(session["id"]), "m06.6.1-codex-smoke-2-" + str(session["id"]),
                "Responde únicamente con la palabra OK2. No ejecutes comandos ni modifiques archivos.")
            result["continuity"] = json.loads(continued["continuity_json"])
            result["second"] = {"outcome": second["outcome"],
                "error_category": second["error_category"], "result": json.loads(second["result_json"])}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        outcomes = [item["outcome"] for key, item in result.items() if key in {"first", "second"}]
        return 0 if outcomes and all(value == "SUCCEEDED" for value in outcomes) else 1
    finally:
        if service is not None: service.close()
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
