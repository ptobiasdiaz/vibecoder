"""Opt-in live-kernel check that UNKNOWN survives retry and daemon restart."""
from __future__ import annotations

import argparse
import grp
import json
from pathlib import Path

from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.worker_runtime import PrivilegedWorkerLauncherClient
from vibecoder.workers import WorkerService

from live_codex_smoke import AGENT_ID, PROJECT_ID, seed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    repository = SQLiteRepository(args.database)
    service: WorkerService | None = None
    try:
        seed(repository, args.workspace)
        service = WorkerService(repository, PluginRegistry(repository), Path("/var/lib/vibecoder/workers"),
            PrivilegedWorkerLauncherClient(), admin_gid=grp.getgrnam("vibecoder-admin").gr_gid)
        session = service.create_session(PROJECT_ID, AGENT_ID, "fake-recovery", "m06.6.1", "fake-1")
        first = service.execute(str(session["id"]), "m06.6.1-fake-unknown", "never repeated", mode="unknown")
        repeated = service.execute(str(session["id"]), "m06.6.1-fake-unknown", "must not run", mode="success")
        result = {"execution_id": first["id"], "first": first["outcome"],
                  "retry": repeated["outcome"], "same_execution": first["id"] == repeated["id"]}
        print(json.dumps(result, sort_keys=True))
        return 0 if result == {"execution_id": first["id"], "first": "UNKNOWN",
                               "retry": "UNKNOWN", "same_execution": True} else 1
    finally:
        if service is not None: service.close()
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
