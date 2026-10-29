"""Inicializa datos locales para pruebas live sin modificar GitHub."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .domain import AuthType, CodexAccount, Project
from .errors import ConflictError, ValidationError
from .github import GitHubConfig, GitHubHttpClient
from .github_service import GitHubRegistryService
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository


@dataclass(frozen=True)
class LiveSetupResult:
    account_id: str
    project_id: str
    repository_id: str
    repository_full_name: str
    repository_empty: bool
    codex_home: str
    codex_executable: str
    working_directory: str
    accounts_json: str
    accounts_json_created: bool


def _directory(value: str | Path, field: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValidationError(f"{field} no es un directorio existente: {path}")
    return path


def _executable(value: str | Path) -> Path:
    raw = os.fspath(value)
    candidate = shutil.which(raw)
    if candidate is None:
        path = Path(raw).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            candidate = os.fspath(path)
    if candidate is None:
        raise ValidationError(f"ejecutable Codex no encontrado o no ejecutable: {raw}")
    return Path(candidate).resolve()


def _repository_name(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValidationError("repository debe tener formato owner/name")
    return parts[0], parts[1]


def _account(core: OrchestratorCore, name: str, codex_home: Path) -> CodexAccount:
    row = core.repository.find_one("codex_accounts", name=name)
    if row:
        return core.get_account(str(row["id"]))
    return core.register_account(
        name,
        AuthType.CHATGPT,
        f"codex-home://{codex_home}",
    )


def _project(core: OrchestratorCore, name: str, account_id: str) -> Project:
    row = core.repository.find_one("projects", name=name)
    if row:
        project = core.get_project(str(row["id"]))
        if project.codex_account_id != account_id:
            project = core.assign_project_account(project.id, account_id)
        return project
    return core.register_project(name, account_id)


def _ensure_accounts_json(
    path: Path,
    account_id: str,
    codex_home: Path,
    codex_executable: Path,
    working_directory: Path,
) -> bool:
    expected = {
        "codex_home": os.fspath(codex_home),
        "codex_executable": os.fspath(codex_executable),
        "cwd": os.fspath(working_directory),
        "timeout_seconds": 600,
        "extra_args": [],
    }
    if path.exists():
        try:
            configs = load_account_configs(path)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError(f"accounts.json existente no es válido: {path}") from exc
        config = configs.get(account_id)
        if config is None:
            raise ConflictError(
                f"accounts.json existente no contiene la cuenta {account_id}; no se sobrescribe"
            )
        if (
            config.codex_home != codex_home
            or Path(config.codex_executable).resolve() != codex_executable
            or config.cwd != working_directory
        ):
            raise ConflictError(
                "accounts.json existente no coincide con CODEX_HOME, ejecutable o cwd"
            )
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"accounts": {account_id: expected}}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    load_account_configs(path)
    return True


def initialize_live_setup(
    core: OrchestratorCore,
    github: GitHubRegistryService,
    *,
    account_name: str,
    project_name: str,
    repository: str,
    codex_home: str | Path,
    codex_executable: str | Path,
    working_directory: str | Path,
    accounts_json: str | Path,
) -> LiveSetupResult:
    account_name = account_name.strip()
    project_name = project_name.strip()
    if not account_name or not project_name:
        raise ValidationError("account_name y project_name son obligatorios")
    home = _directory(codex_home, "CODEX_HOME")
    cwd = _directory(working_directory, "working_directory")
    executable = _executable(codex_executable)
    owner, name = _repository_name(repository)

    github.validate_identity()
    account = _account(core, account_name, home)
    project = _project(core, project_name, account.id)
    registered = github.register_repository(project.id, owner, name)
    github.validate_repository_access(registered.id)
    output_path = Path(accounts_json).expanduser().resolve()
    created = _ensure_accounts_json(
        output_path, account.id, home, executable, cwd
    )
    return LiveSetupResult(
        account_id=account.id,
        project_id=project.id,
        repository_id=registered.id,
        repository_full_name=registered.full_name,
        repository_empty=registered.is_empty,
        codex_home=os.fspath(home),
        codex_executable=os.fspath(executable),
        working_directory=os.fspath(cwd),
        accounts_json=os.fspath(output_path),
        accounts_json_created=created,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", default="vibecoder-test")
    parser.add_argument("--repository", default="ptobiasdiaz/vibecoder_test")
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--codex-executable", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--accounts-json", required=True)
    parser.add_argument("--github-token-env", default="VIBECODER_GITHUB_TOKEN")
    return parser


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.github_token_env)
    if not token:
        raise SystemExit(f"falta variable de entorno {args.github_token_env}")
    with SQLiteRepository(Path(args.database).expanduser().resolve()) as repository, GitHubHttpClient(
        GitHubConfig(token=token)
    ) as client:
        core = OrchestratorCore(repository)
        result = initialize_live_setup(
            core,
            GitHubRegistryService(core, client),
            account_name=args.account_name,
            project_name=args.project_name,
            repository=args.repository,
            codex_home=args.codex_home,
            codex_executable=args.codex_executable,
            working_directory=args.working_directory,
            accounts_json=args.accounts_json,
        )
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
