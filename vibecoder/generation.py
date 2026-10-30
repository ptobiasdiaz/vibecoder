"""M03.3: generación inicial aprobada dentro de un workspace Git aislado."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .codex_executor import AccountRuntimeConfig, CodexAppServerExecutor
from .documents import InstructionLoader
from .domain import (
    BootstrapStatus,
    GenerationStatus,
    GenerationWorkflow,
    GitConfigurationStatus,
    TaskStatus,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .git import GitAdapter, GitStatus, WorkspaceManager
from .git_config import GitConfigurationService
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository
from .worker import Executor, LocalWorker, WorkerOutcome


MAX_FILE_SIZE = 2 * 1024 * 1024
SENSITIVE_NAMES = {
    ".env", "id_rsa", "id_ed25519", "credentials", "credentials.json",
    "secrets.json", "accounts.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    files: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: Mapping[str, int]


class GeneratedFilesValidator:
    def __init__(self, git: GitAdapter, *, max_file_size: int = MAX_FILE_SIZE):
        self.git = git
        self.max_file_size = max_file_size

    def validate(self, workspace: Path, expected_branch: str) -> ValidationReport:
        workspace = workspace.resolve(strict=True)
        status = self.git.status(workspace)
        errors: list[str] = []
        warnings: list[str] = []
        paths = sorted({change.path for change in status.changes})
        if status.branch != expected_branch:
            errors.append(f"rama activa inesperada: {status.branch!r}")
        if not paths:
            errors.append("la generación no produjo cambios")
        errors.extend(
            f"enlace simbólico peligroso: {relative}"
            for relative in self.dangerous_symlinks(workspace)
        )
        for required in ("AGENTS.md", ".vibecoder/agents/main/AGENTS.md"):
            path = workspace / required
            if not path.is_file() or path.is_symlink() or not path.read_text(
                encoding="utf-8"
            ).strip():
                errors.append(f"falta documento obligatorio: {required}")
        for relative in paths:
            candidate = workspace / relative
            if candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(workspace)
            except ValueError:
                errors.append(f"ruta fuera del workspace: {relative}")
                continue
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
            if size > self.max_file_size:
                errors.append(f"archivo excesivamente grande: {relative} ({size} bytes)")
                continue
            lowered = candidate.name.casefold()
            if lowered in SENSITIVE_NAMES or candidate.suffix.casefold() in SENSITIVE_SUFFIXES:
                errors.append(f"archivo sensible conocido: {relative}")
            data = candidate.read_bytes()
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                errors.append(f"posible credencial detectada: {relative}")
            if relative.startswith(".vibecoder/") and candidate.name.casefold() in {
                "installation.md", "user-guide.md", "api.md"
            }:
                warnings.append(f"documentación posiblemente pública bajo .vibecoder/: {relative}")
        return ValidationReport(
            valid=not errors,
            files=tuple(paths),
            errors=tuple(errors),
            warnings=tuple(warnings),
            summary={
                "changed_files": len(paths),
                "errors": len(errors),
                "warnings": len(warnings),
            },
        )

    @staticmethod
    def dangerous_symlinks(workspace: Path) -> tuple[str, ...]:
        workspace = workspace.resolve(strict=True)
        dangerous: list[str] = []
        for root, directories, files in os.walk(workspace, followlinks=False):
            root_path = Path(root)
            if root_path == workspace:
                directories[:] = [item for item in directories if item != ".git"]
            for name in (*directories, *files):
                candidate = root_path / name
                if not candidate.is_symlink():
                    continue
                relative = str(candidate.relative_to(workspace))
                try:
                    candidate.resolve(strict=True).relative_to(workspace)
                except (FileNotFoundError, ValueError):
                    dangerous.append(relative)
        return tuple(sorted(set(dangerous)))


class GenerationService:
    def __init__(
        self,
        core: OrchestratorCore,
        account_configs: dict[str, AccountRuntimeConfig],
        workspace_root: Path,
        *,
        git: GitAdapter | None = None,
        executor_factory: Callable[[AccountRuntimeConfig], Executor] = CodexAppServerExecutor,
        orchestrator_root: Path | None = None,
        max_file_size: int = MAX_FILE_SIZE,
    ):
        self.core = core
        self.repository = core.repository
        self.account_configs = account_configs
        self.git = git or GitAdapter()
        forbidden = (orchestrator_root,) if orchestrator_root else ()
        self.workspaces = WorkspaceManager(workspace_root, forbidden_roots=forbidden)
        self.executor_factory = executor_factory
        self.validator = GeneratedFilesValidator(self.git, max_file_size=max_file_size)

    def prepare(self, project_id: str, *, remote_url: str | None = None) -> GenerationWorkflow:
        project = self.core.get_project(project_id)
        bootstrap_row = self.repository.find_one("bootstrap_workflows", project_id=project.id)
        if bootstrap_row is None:
            raise NotFoundError("el proyecto no tiene workflow de bootstrap")
        if BootstrapStatus(str(bootstrap_row["status"])) is not BootstrapStatus.APPROVED:
            raise ConflictError("la generación requiere un bootstrap aprobado")
        required = ("approved_by", "approved_event_id", "frozen_specification")
        if any(not bootstrap_row.get(field) for field in required):
            raise ConflictError("la aprobación del bootstrap no es verificable")
        specification = str(bootstrap_row["frozen_specification"])
        specification_hash = _hash(specification)
        existing = self.repository.find_one("generation_workflows", project_id=project.id)
        if existing:
            generation = self._generation(existing)
            if generation.specification_hash != specification_hash:
                raise ConflictError("la especificación aprobada cambió; requiere nuevo workflow")
            self._verify_existing_workspace(generation)
            return generation
        config_rows = [
            *self.repository.list(
                "git_configurations", workflow_id=str(bootstrap_row["id"]),
                status=GitConfigurationStatus.APPLIED.value,
            ),
            *self.repository.list(
                "git_configurations", workflow_id=str(bootstrap_row["id"]),
                status=GitConfigurationStatus.APPROVED.value,
            ),
        ]
        if not config_rows:
            raise ConflictError(
                "falta configuración Git aprobada; use reconciliación explícita para workflows históricos"
            )
        git_configuration = GitConfigurationService._from_row(
            max(config_rows, key=lambda item: int(item["revision"]))
        )
        if git_configuration.specification_hash != specification_hash:
            raise ConflictError("la configuración Git no pertenece a la especificación aprobada")
        repo = self.repository.get("github_repositories", str(bootstrap_row["repository_id"]))
        if git_configuration.observed_github_id != int(repo["github_id"]):
            raise ConflictError("cambió la identidad estable del repositorio")
        remote = remote_url or f"{str(repo['html_url']).rstrip('/')}.git"
        workspace = self.workspaces.path_for(project.id)
        generation_id = str(uuid4())
        branch = git_configuration.bootstrap_branch
        now = _now()
        self.repository.insert(
            "generation_workflows",
            {
                "id": generation_id,
                "project_id": project.id,
                "bootstrap_workflow_id": str(bootstrap_row["id"]),
                "repository_id": str(repo["id"]),
                "approved_by": str(bootstrap_row["approved_by"]),
                "approved_event_id": int(bootstrap_row["approved_event_id"]),
                "specification_hash": specification_hash,
                "workspace_path": str(workspace),
                "remote_url": remote,
                "base_branch": None,
                "base_commit": None,
                "bootstrap_branch": branch,
                "git_configuration_id": git_configuration.id,
                "git_configuration_hash": git_configuration.configuration_hash,
                "task_id": None,
                "session_id": None,
                "status": GenerationStatus.PREPARING_WORKSPACE.value,
                "instruction_versions_json": "{}",
                "generated_instruction_versions_json": "{}",
                "validation_result_json": None,
                "commit_hash": None,
                "blocked_reason": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        try:
            self.git.clone(remote, workspace)
            remote_base = self.git.default_branch(workspace)
            if git_configuration.base_branch_exists:
                if remote_base != git_configuration.main_branch:
                    raise ConflictError("la rama por defecto remota cambió desde la aprobación")
                self.git.create_branch(workspace, branch, remote_base)
                base = remote_base
                base_commit = self.git.status(workspace).head
                if base_commit != git_configuration.observed_head_sha:
                    raise ConflictError("el HEAD remoto cambió desde la aprobación")
            elif remote_base is not None:
                raise ConflictError("el repositorio dejó de estar vacío desde la aprobación")
            else:
                base = git_configuration.main_branch
                base_commit = self.git.create_empty_base(workspace, base)
                self.git.create_branch_from_local(workspace, branch, base)
            dangerous = self.validator.dangerous_symlinks(workspace)
            if dangerous:
                raise ValidationError(
                    "el repositorio contiene enlaces que escapan del workspace: "
                    + ", ".join(dangerous)
                )
            self.repository.update(
                "generation_workflows",
                generation_id,
                {
                    "base_branch": base,
                    "base_commit": base_commit,
                    "status": GenerationStatus.PENDING.value,
                    "updated_at": _now(),
                },
            )
            if git_configuration.status is GitConfigurationStatus.APPROVED:
                applied_at = _now()
                self.repository.update(
                    "git_configurations", git_configuration.id,
                    {
                        "status": GitConfigurationStatus.APPLIED.value,
                        "applied_at": applied_at,
                        "updated_at": applied_at,
                    },
                )
        except Exception as exc:
            self.repository.update(
                "generation_workflows",
                generation_id,
                {
                    "status": GenerationStatus.FAILED.value,
                    "blocked_reason": str(exc),
                    "updated_at": _now(),
                },
            )
            raise
        return self.get(generation_id)

    async def generate(self, generation_id: str) -> GenerationWorkflow:
        generation = self.get(generation_id)
        if generation.status is GenerationStatus.READY_TO_PUBLISH:
            self._verify_commit(generation)
            return generation
        if generation.status is not GenerationStatus.PENDING:
            raise ConflictError(f"generación no ejecutable desde {generation.status.value}")
        workspace = self._verify_existing_workspace(generation)
        dangerous = self.validator.dangerous_symlinks(workspace)
        if dangerous:
            self._block(
                generation.id,
                "enlaces que escapan del workspace antes de ejecutar: " + ", ".join(dangerous),
            )
            return self.get(generation.id)
        initial_status = self.git.status(workspace)
        if initial_status.changes or initial_status.head != generation.base_commit:
            self._block(
                generation.id,
                "el workspace contiene cambios o commits ajenos previos a la generación",
            )
            return self.get(generation.id)
        bootstrap = self.repository.get("bootstrap_workflows", generation.bootstrap_workflow_id)
        specification = str(bootstrap["frozen_specification"])
        if _hash(specification) != generation.specification_hash:
            raise ConflictError("la especificación congelada no coincide con su hash autorizado")
        if generation.git_configuration_id:
            config = GitConfigurationService._from_row(
                self.repository.get("git_configurations", generation.git_configuration_id)
            )
            if (
                GitConfigurationService.hash_payload(
                    GitConfigurationService._payload(config)
                ) != config.configuration_hash
                or
                config.configuration_hash != generation.git_configuration_hash
                or config.specification_hash != generation.specification_hash
                or config.bootstrap_branch != generation.bootstrap_branch
            ):
                self._block(generation.id, "la configuración Git aplicada cambió o es incompatible")
                return self.get(generation.id)
        task_context = self._task_context(specification, generation)
        instructions = InstructionLoader(workspace).load("main", task_context)
        agent_id = str(bootstrap["principal_agent_id"])
        task = self.core.create_task(
            generation.project_id,
            agent_id,
            "INITIAL_PROJECT_GENERATION",
            external_ref=f"generation:{generation.id}",
            idempotency_key=f"initial-generation:{generation.specification_hash}",
            prompt=instructions.text,
        )
        session = (
            self.core.get_session(generation.session_id)
            if generation.session_id
            else self.core.create_session(agent_id)
        )
        self.repository.update(
            "generation_workflows",
            generation.id,
            {
                "task_id": task.id,
                "session_id": session.id,
                "status": GenerationStatus.GENERATING.value,
                "instruction_versions_json": json.dumps(instructions.versions, sort_keys=True),
                "updated_at": _now(),
            },
        )
        account_id = self.core.get_project(generation.project_id).codex_account_id
        base_config = self.account_configs.get(account_id)
        if base_config is None:
            self._block(generation.id, "falta configuración runtime de la cuenta")
            raise ValidationError(f"falta configuración runtime para la cuenta {account_id}")
        runtime = replace(base_config, cwd=workspace)
        worker = LocalWorker(
            self.core, self.account_configs, executor_factory=self.executor_factory
        )
        outcome: WorkerOutcome = await worker.run_task(
            task.id, session_id=session.id, runtime_config=runtime
        )
        if outcome.status != TaskStatus.COMPLETED:
            self._block(generation.id, f"ejecución Codex: {outcome.status}: {outcome.detail}")
            return self.get(generation.id)
        self.repository.update(
            "generation_workflows", generation.id,
            {"status": GenerationStatus.VALIDATING.value, "updated_at": _now()},
        )
        return self._validate_and_commit(generation.id)

    def inspect(self, generation_id: str) -> Mapping[str, Any]:
        generation = self.get(generation_id)
        workspace = self._verify_existing_workspace(generation)
        status = self.git.status(workspace)
        return {
            "generation": asdict(generation),
            "git": asdict(status),
            "diff": self.git.diff(workspace),
        }

    def recover(self, generation_id: str) -> GenerationWorkflow:
        generation = self.get(generation_id)
        self._verify_existing_workspace(generation)
        if generation.status is GenerationStatus.PREPARING_WORKSPACE:
            self._block(generation.id, "preparación interrumpida; inspección manual requerida")
        elif generation.status is GenerationStatus.GENERATING:
            if not generation.task_id:
                self._block(generation.id, "generación interrumpida sin tarea persistida")
            else:
                task = self.core.get_task(generation.task_id)
                if task.status is TaskStatus.RUNNING:
                    task = self.core.recover_interrupted_task(task.id)
                if task.status is TaskStatus.COMPLETED:
                    self.repository.update(
                        "generation_workflows", generation.id,
                        {"status": GenerationStatus.VALIDATING.value, "updated_at": _now()},
                    )
                    return self._validate_and_commit(generation.id)
                self._block(
                    generation.id,
                    f"resultado de generación indeterminado: tarea {task.status.value}",
                )
        elif generation.status is GenerationStatus.VALIDATING:
            return self._validate_and_commit(generation.id)
        elif generation.status is GenerationStatus.READY_TO_PUBLISH:
            self._verify_commit(generation)
        elif generation.status is GenerationStatus.BLOCKED and generation.task_id:
            task = self.core.get_task(generation.task_id)
            if task.status is TaskStatus.COMPLETED:
                self.repository.update(
                    "generation_workflows", generation.id,
                    {"status": GenerationStatus.VALIDATING.value, "updated_at": _now()},
                )
                return self._validate_and_commit(generation.id)
        return self.get(generation.id)

    def archive(self, generation_id: str) -> Path:
        generation = self.get(generation_id)
        if generation.status not in {
            GenerationStatus.READY_TO_PUBLISH,
            GenerationStatus.BLOCKED,
            GenerationStatus.FAILED,
        }:
            raise ConflictError("solo se archivan workspaces finalizados o bloqueados")
        archived = self.workspaces.archive(Path(generation.workspace_path))
        self.repository.update(
            "generation_workflows", generation.id,
            {
                "workspace_path": str(archived),
                "status": GenerationStatus.ARCHIVED.value,
                "blocked_reason": (
                    generation.blocked_reason or "workspace archivado explícitamente"
                ),
                "updated_at": _now(),
            },
        )
        return archived

    def get(self, generation_id: str) -> GenerationWorkflow:
        return self._generation(self.repository.get("generation_workflows", generation_id))

    def get_project_generation(self, project_id: str) -> GenerationWorkflow:
        row = self.repository.find_one("generation_workflows", project_id=project_id)
        if row is None:
            raise NotFoundError("el proyecto no tiene generación inicial")
        return self._generation(row)

    def legacy_compatibility(self, generation_id: str) -> Mapping[str, Any]:
        generation = self.get(generation_id)
        if generation.git_configuration_id:
            return {
                "compatible": True,
                "legacy": False,
                "configuration_id": generation.git_configuration_id,
                "configuration_hash": generation.git_configuration_hash,
            }
        return {
            "compatible": generation.status in {
                GenerationStatus.READY_TO_PUBLISH,
                GenerationStatus.ARCHIVED,
            },
            "legacy": True,
            "preserved": True,
            "requires_new_approval_for_remote_operations": True,
            "detail": (
                "la generación se conserva sin cambios; su aprobación histórica no "
                "autoriza operaciones remotas M03.4"
            ),
        }

    def _validate_and_commit(self, generation_id: str) -> GenerationWorkflow:
        generation = self.get(generation_id)
        workspace = self._verify_existing_workspace(generation)
        report = self.validator.validate(workspace, generation.bootstrap_branch)
        versions = InstructionLoader(workspace).load("main", "validación de contexto").versions
        values: dict[str, object] = {
            "validation_result_json": json.dumps(asdict(report), sort_keys=True),
            "generated_instruction_versions_json": json.dumps(versions, sort_keys=True),
            "updated_at": _now(),
        }
        if not report.valid:
            values.update({
                "status": GenerationStatus.BLOCKED.value,
                "blocked_reason": "; ".join(report.errors),
            })
            self.repository.update("generation_workflows", generation.id, values)
            return self.get(generation.id)
        commit = generation.commit_hash
        if commit:
            self._verify_commit(generation)
        else:
            commit = self.git.commit_all(
                workspace,
                f"Bootstrap inicial aprobado ({generation.specification_hash[:12]})",
            )
        values.update({
            "status": GenerationStatus.READY_TO_PUBLISH.value,
            "commit_hash": commit,
            "blocked_reason": None,
        })
        self.repository.update("generation_workflows", generation.id, values)
        return self.get(generation.id)

    def _verify_existing_workspace(self, generation: GenerationWorkflow) -> Path:
        workspace = self.workspaces.assert_safe(Path(generation.workspace_path))
        if not workspace.is_dir():
            raise ConflictError("workspace persistido inexistente")
        self.git.verify_remote(workspace, generation.remote_url)
        branch = self.git.current_branch(workspace)
        if branch != generation.bootstrap_branch:
            raise ConflictError("workspace en una rama distinta de la registrada")
        return workspace

    def _verify_commit(self, generation: GenerationWorkflow) -> None:
        if not generation.commit_hash:
            raise ConflictError("generación preparada sin commit persistido")
        workspace = self._verify_existing_workspace(generation)
        status = self.git.status(workspace)
        if status.head != generation.commit_hash or status.changes:
            raise ConflictError("el workspace no coincide con el commit persistido")

    def _block(self, generation_id: str, reason: str) -> None:
        self.repository.update(
            "generation_workflows", generation_id,
            {
                "status": GenerationStatus.BLOCKED.value,
                "blocked_reason": reason,
                "updated_at": _now(),
            },
        )

    @staticmethod
    def _task_context(specification: str, generation: GenerationWorkflow) -> str:
        return f"""Genera la estructura inicial del proyecto exclusivamente dentro del workspace actual.

La autorización humana corresponde a @{generation.approved_by}, evento GitHub
{generation.approved_event_id}, especificación SHA-256 {generation.specification_hash}.

Debes crear `AGENTS.md` en la raíz con únicamente reglas globales sustentadas por la
especificación aprobada. Crea `.vibecoder/agents/main/AGENTS.md` con responsabilidades
específicas del agente principal, sin inventar especialistas. Usa `.vibecoder/` para
contexto operativo y memoria versionada, y `doc/` para documentación destinada a
usuarios o desarrolladores. No crees archivos vacíos, secretos, credenciales, sesiones,
bases de datos, logs ni cachés. No amplíes el alcance aprobado, no ejecutes Git, no
publiques nada y no modifiques rutas externas al workspace.

## Especificación aprobada e inmutable

{specification}
"""

    @staticmethod
    def _generation(row: Mapping[str, object]) -> GenerationWorkflow:
        return GenerationWorkflow(
            id=str(row["id"]), project_id=str(row["project_id"]),
            bootstrap_workflow_id=str(row["bootstrap_workflow_id"]),
            repository_id=str(row["repository_id"]), approved_by=str(row["approved_by"]),
            approved_event_id=int(row["approved_event_id"]),
            specification_hash=str(row["specification_hash"]),
            workspace_path=str(row["workspace_path"]), remote_url=str(row["remote_url"]),
            base_branch=str(row["base_branch"]) if row["base_branch"] else None,
            base_commit=str(row["base_commit"]) if row["base_commit"] else None,
            bootstrap_branch=str(row["bootstrap_branch"]),
            git_configuration_id=(
                str(row["git_configuration_id"]) if row["git_configuration_id"] else None
            ),
            git_configuration_hash=(
                str(row["git_configuration_hash"]) if row["git_configuration_hash"] else None
            ),
            task_id=str(row["task_id"]) if row["task_id"] else None,
            session_id=str(row["session_id"]) if row["session_id"] else None,
            status=GenerationStatus(str(row["status"])),
            instruction_versions=json.loads(str(row["instruction_versions_json"])),
            generated_instruction_versions=json.loads(
                str(row["generated_instruction_versions_json"])
            ),
            validation_result=(
                json.loads(str(row["validation_result_json"]))
                if row["validation_result_json"] else None
            ),
            commit_hash=str(row["commit_hash"]) if row["commit_hash"] else None,
            blocked_reason=str(row["blocked_reason"]) if row["blocked_reason"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--accounts")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "prepare", "generate", "status", "inspect", "recover", "archive", "legacy-check"
    ):
        command = sub.add_parser(name)
        command.add_argument("identifier")
    return parser


def main() -> int:
    args = _parser().parse_args()
    configs = load_account_configs(args.accounts) if args.accounts else {}
    orchestrator_root = Path(__file__).resolve().parents[1]
    with SQLiteRepository(Path(args.database).resolve()) as repository:
        service = GenerationService(
            OrchestratorCore(repository), configs, Path(args.workspace_root),
            orchestrator_root=orchestrator_root,
        )
        if args.command == "prepare":
            result: object = service.prepare(args.identifier)
        elif args.command == "generate":
            if not args.accounts:
                raise SystemExit("--accounts es obligatorio para generate")
            result = asyncio.run(service.generate(args.identifier))
        elif args.command == "status":
            result = service.get_project_generation(args.identifier)
        elif args.command == "inspect":
            result = service.inspect(args.identifier)
        elif args.command == "recover":
            result = service.recover(args.identifier)
        elif args.command == "legacy-check":
            result = service.legacy_compatibility(args.identifier)
        else:
            result = {"archived_path": str(service.archive(args.identifier))}
        print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result,
                         indent=2, ensure_ascii=False, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
