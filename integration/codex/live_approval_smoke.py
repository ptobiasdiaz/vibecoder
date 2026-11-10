"""Opt-in real Codex approval exercise for the prepared Debian VM."""
from __future__ import annotations
import argparse
import grp
import json
from pathlib import Path

from vibecoder.plugin_registry import PluginRegistry
from vibecoder.sqlite import SQLiteRepository
from vibecoder.worker_runtime import PrivilegedWorkerLauncherClient
from vibecoder.workers import WorkerService


PROMPTS = {
    "deny": "Ejecuta exactamente el comando `printf 'approval-test-deny\\n' > approval-deny.txt`. No hagas nada más.",
    "approve": "Ejecuta exactamente el comando `printf 'approval-test-approve\\n' > approval-approved.txt`. No hagas nada más.",
    "expire": "Ejecuta exactamente el comando `printf 'approval-test-expire\\n' > approval-expired.txt`. No hagas nada más.",
    "restart": "Ejecuta exactamente el comando `printf 'approval-test-restart\\n' > approval-restart.txt`. No hagas nada más.",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(PROMPTS))
    parser.add_argument("--database", type=Path, default=Path("/var/lib/vibecoder/vibecoder.db"))
    parser.add_argument("--session", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--run", default="1")
    args = parser.parse_args()
    repository = SQLiteRepository(args.database); service = None
    try:
        service = WorkerService(repository, PluginRegistry(repository), Path("/var/lib/vibecoder/workers"),
            PrivilegedWorkerLauncherClient(), admin_gid=grp.getgrnam("vibecoder-admin").gr_gid)
        result = service.execute(args.session, f"m06.6.2-{args.case}-{args.run}", PROMPTS[args.case],
                                 approval_timeout=args.timeout)
        print(json.dumps({"id": result["id"], "outcome": result["outcome"],
            "error_category": result["error_category"], "result": json.loads(result["result_json"])},
            ensure_ascii=False, sort_keys=True))
        return 0 if result["outcome"] in {"SUCCEEDED", "UNKNOWN", "CANCELLED"} else 1
    finally:
        if service is not None: service.close()
        repository.close()


if __name__ == "__main__": raise SystemExit(main())
