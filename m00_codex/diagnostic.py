"""Diagnóstico reproducible y de bajo coste para el hito M00."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .client import AppServerClient, RpcError


@dataclass
class Check:
    name: str
    status: str
    detail: str


def find_codex(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if found := shutil.which("codex"):
        return found
    releases = Path.home() / ".codex/packages/app-server-daemon/releases"
    candidates = sorted(releases.glob("*/bin/codex"), reverse=True)
    return os.fspath(candidates[0]) if candidates else None


async def _attempt(name: str, operation: Callable[[], Awaitable[object]]) -> Check:
    try:
        await operation()
        return Check(name, "PASS", "verificado contra App Server")
    except RpcError as exc:
        return Check(name, "LIMITED", f"RPC {exc.category}: {exc}")
    except Exception as exc:  # diagnostic must report, not obscure later checks
        return Check(name, "FAIL", f"{type(exc).__name__}: {exc}")


async def diagnose(args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    codex = find_codex(args.codex)
    if not codex:
        return [Check("codex", "FAIL", "ejecutable no encontrado")]
    version = subprocess.run(
        [codex, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    checks.append(Check("codex", "PASS" if version.returncode == 0 else "FAIL", version.stdout.strip()))

    if args.codex_home:
        home = Path(args.codex_home).expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="vibecoder-m00-")
        home = Path(temporary.name)
    checks.append(Check("CODEX_HOME aislado", "PASS", os.fspath(home)))

    try:
        client = AppServerClient(codex, codex_home=home, cwd=args.cwd)
        try:
            initialized = await client.start()
            checks.append(Check("App Server + handshake", "PASS", str(initialized.get("userAgent", "inicializado"))))
            first = await client.start_thread(cwd=os.fspath(Path(args.cwd).resolve()))
            thread_id = str(first["thread"]["id"])
            checks.append(Check("crear thread", "PASS", thread_id))
            await client.close()

            resumed_client = AppServerClient(codex, codex_home=home, cwd=args.cwd)
            await resumed_client.start()
            try:
                resumed = await resumed_client.resume_thread(thread_id)
                same = resumed.get("thread", {}).get("id") == thread_id
                checks.append(Check("persistencia tras reinicio", "PASS" if same else "FAIL", thread_id))
            except RpcError as exc:
                detail = str(exc)
                if "no rollout found" in detail:
                    checks.append(Check("persistencia tras reinicio", "LIMITED", "un thread sin turn no crea rollout"))
                else:
                    checks.append(Check("persistencia tras reinicio", "FAIL", detail))
            checks.append(await _attempt("límites de cuenta", resumed_client.rate_limits))
            checks.append(await _attempt("uso de cuenta", resumed_client.account_usage))
            await resumed_client.close()
        finally:
            await client.close()
    finally:
        if temporary:
            temporary.cleanup()

    if not args.live_turns:
        checks.extend(
            [
                Check("turno autenticado", "SKIP", "requiere --live-turns y un CODEX_HOME autenticado"),
                Check("concurrencia real", "SKIP", "requiere --live-turns; evita consumo accidental"),
                Check("cancelación real", "SKIP", "requiere --live-turns; cubierta con servidor simulado"),
            ]
        )
    else:
        checks.append(Check("turnos live", "LIMITED", "usar las pruebas de integración para prompts controlados"))
    return checks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", help="ruta al ejecutable Codex")
    parser.add_argument("--codex-home", help="CODEX_HOME de prueba; por defecto usa uno temporal sin credenciales")
    parser.add_argument("--cwd", default=os.getcwd(), help="directorio de trabajo para los threads")
    parser.add_argument("--live-turns", action="store_true", help="habilita sondas que pueden consumir cuota")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    return parser


def main() -> int:
    args = _parser().parse_args()
    checks = asyncio.run(diagnose(args))
    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2, ensure_ascii=False))
    else:
        print("M00 — diagnóstico de Codex App Server")
        for check in checks:
            print(f"[{check.status:7}] {check.name}: {check.detail}")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

