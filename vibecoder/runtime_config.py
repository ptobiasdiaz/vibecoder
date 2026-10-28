"""Carga de configuración no secreta de runtimes Codex por cuenta."""

from __future__ import annotations

import json
from pathlib import Path

from .codex_executor import AccountRuntimeConfig


def load_account_configs(path: str | Path) -> dict[str, AccountRuntimeConfig]:
    payload = json.loads(Path(path).resolve().read_text(encoding="utf-8"))
    configs: dict[str, AccountRuntimeConfig] = {}
    for account_id, raw in payload.get("accounts", {}).items():
        config = AccountRuntimeConfig(
            account_id=str(account_id),
            codex_home=Path(raw["codex_home"]).expanduser().resolve(),
            codex_executable=str(raw.get("codex_executable", "codex")),
            cwd=Path(raw.get("cwd", ".")).expanduser().resolve(),
            extra_args=tuple(str(value) for value in raw.get("extra_args", [])),
            timeout_seconds=float(raw.get("timeout_seconds", 600)),
        )
        config.validate()
        configs[str(account_id)] = config
    return configs
