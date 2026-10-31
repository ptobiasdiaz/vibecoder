"""M04.3: ejecución controlada y publicación de tareas aprobadas."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .agents import AgentManagementService
from .codex_executor import AccountRuntimeConfig, CodexAppServerExecutor
from .domain import (
    AgentType,
    ProjectStatus,
    SpecialistStatus,
    TaskExecutionStatus,
    TaskExecutionWorkflow,
    TaskStatus,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .generation import GeneratedFilesValidator
from .git import GitAdapter, GitError, WorkspaceManager
from .git_config import GitConfigurationService
from .github import GitHubConfig, GitHubError, GitHubHttpClient, GitHubTransientError
from .github_service import GitHubRegistryService
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository
from .worker import Executor, LocalWorker


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _digest_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TaskExecutionService:
    def __init__(
        self,
        core: OrchestratorCore,
        github: GitHubRegistryService,
        account_configs: dict[str, AccountRuntimeConfig],
        workspace_root: Path,
        *,
        project_workspace: Path,
        git: GitAdapter | None = None,
        executor_factory: Callable[[AccountRuntimeConfig], Executor] = CodexAppServerExecutor,
        orchestrator_root: Path | None = None,
        max_file_size: int = 2 * 1024 * 1024,
    ):
        self.core = core
        self.repository = core.repository
        self.github = github
        self.account_configs = account_configs
        self.project_workspace = project_workspace.resolve(strict=True)
        self.git = git or GitAdapter()
        forbidden = (orchestrator_root,) if orchestrator_root else ()
        self.workspaces = WorkspaceManager(workspace_root, forbidden_roots=forbidden)
        self.executor_factory = executor_factory
        self.validator = GeneratedFilesValidator(self.git, max_file_size=max_file_size)
        self.agents = AgentManagementService(core, git=self.git)

    # -- elegibilidad y preparación ---------------------------------------------

    def eligible(self, task_id: str) -> Mapping[str, Any]:
        try:
            context = self._authorization(task_id)
            pending = self._pending_dependencies(str(context["planned"]["id"]))
            return {"eligible": not pending, "pending_dependencies": pending, "reason": None}
        except (ConflictError, ValidationError, NotFoundError) as exc:
            return {"eligible": False, "pending_dependencies": (), "reason": str(exc)}

    def prepare(
        self,
        task_id: str,
        *,
        allowed_paths: Sequence[str],
        validation_commands: Sequence[Sequence[str]] = (),
        validation_authorized_by: str | None = None,
        remote_url: str | None = None,
    ) -> TaskExecutionWorkflow:
        context = self._authorization(task_id)
        planned, revision, issue = context["planned"], context["revision"], context["issue"]
        normalized_paths = self._allowed_paths(allowed_paths)
        commands = self._validation_commands(validation_commands)
        if commands and not (validation_authorized_by or "").strip():
            raise ConflictError("los comandos de validación requieren un operador autorizador")
        existing = self.repository.find_one("task_execution_workflows", task_id=task_id)
        if existing:
            execution = self._execution(existing)
            if execution.allowed_paths != normalized_paths or execution.validation_commands != commands:
                raise ConflictError("el workflow existente usa otra política de validación")
            self._verify_authorization(execution)
            if execution.status is TaskExecutionStatus.WAITING_DEPENDENCIES:
                if self._pending_dependencies(execution.planned_task_id):
                    return execution
                return self._prepare_workspace(execution.id)
            if execution.status is TaskExecutionStatus.READY:
                return self._prepare_workspace(execution.id)
            return execution
        if context["task"].status is not TaskStatus.PENDING:
            raise ConflictError("solo se prepara una PLANNED_WORK pendiente")
        repo = self.repository.get("github_repositories", str(issue["repository_id"]))
        config = self._active_git_configuration(str(issue["project_id"]))
        if str(config.repository_id) != str(repo["id"]):
            raise ConflictError("la configuración Git pertenece a otro repositorio")
        workspace = self.workspaces.path_for(task_id)
        work_branch = f"vibecoder/task-{task_id}"
        self.git.validate_branch(work_branch)
        specialist = context["specialist"]
        agent_revision = context["agent_revision"]
        snapshot = context["snapshot"]
        pending = self._pending_dependencies(str(planned["id"]))
        now = _now()
        execution_id = str(uuid4())
        self.repository.insert(
            "task_execution_workflows",
            {
                "id": execution_id, "project_id": str(issue["project_id"]),
                "issue_workflow_id": str(issue["id"]), "plan_revision_id": str(revision["id"]),
                "planned_task_id": str(planned["id"]), "task_id": task_id,
                "repository_id": str(repo["id"]), "agent_id": str(planned["assigned_agent_id"]),
                "agent_revision_id": agent_revision.id,
                "agent_configuration_hash": agent_revision.configuration_hash,
                "instructions_hash": agent_revision.instructions_hash,
                "account_id": specialist.assigned_account_id,
                "status": (TaskExecutionStatus.WAITING_DEPENDENCIES if pending else TaskExecutionStatus.READY).value,
                "workspace_path": str(workspace),
                "remote_url": remote_url or f"{str(repo['html_url']).rstrip('/')}.git",
                "base_branch": config.main_branch, "base_sha": "",
                "work_branch": work_branch, "session_id": None,
                "instruction_versions_json": "{}",
                "authorization_snapshot_json": json.dumps(snapshot, sort_keys=True),
                "context_hash": None,
                "allowed_paths_json": json.dumps(normalized_paths),
                "validation_commands_json": json.dumps(commands),
                "validation_authorized_by": validation_authorized_by.strip() if validation_authorized_by else None,
                "result_text": None, "validation_result_json": None,
                "commit_sha": None, "published_sha": None,
                "pull_request_id": None, "pull_request_number": None,
                "pull_request_url": None, "merge_commit_sha": None,
                "remote_state_json": None,
                "blocked_reason": "dependencias pendientes" if pending else None,
                "created_at": now, "updated_at": now,
            },
        )
        if pending:
            return self.get(execution_id)
        return self._prepare_workspace(execution_id)

    def _prepare_workspace(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        self._verify_authorization(execution)
        pending = self._pending_dependencies(execution.planned_task_id)
        if pending:
            return self._set_status(
                execution.id, TaskExecutionStatus.WAITING_DEPENDENCIES,
                f"dependencias pendientes: {', '.join(pending)}",
            )
        observation = self.github.observe_repository_state(execution.repository_id)
        repo = self.repository.get("github_repositories", execution.repository_id)
        if int(observation["github_id"]) != int(repo["github_id"]):
            raise ConflictError("cambió la identidad estable del repositorio")
        if observation.get("is_empty") or observation.get("default_branch") != execution.base_branch:
            raise ConflictError("rama base remota inexistente o incompatible")
        base_sha = str(observation.get("head_sha") or "")
        if not base_sha:
            raise ConflictError("la rama base no tiene HEAD verificable")
        workspace = self.workspaces.assert_safe(Path(execution.workspace_path))
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"base_sha": base_sha, "status": TaskExecutionStatus.PREPARING.value,
             "blocked_reason": None, "updated_at": _now()},
        )
        try:
            if workspace.exists():
                raise ConflictError("workspace preexistente; requiere reconciliación")
            self.git.clone(execution.remote_url, workspace)
            self.git.verify_remote(workspace, execution.remote_url)
            if self.git.default_branch(workspace) != execution.base_branch:
                raise ConflictError("la rama por defecto clonada no coincide")
            self.git.create_branch(workspace, execution.work_branch, execution.base_branch)
            status = self.git.status(workspace)
            if status.head != base_sha or status.changes:
                raise ConflictError("el checkout no coincide con la base remota observada")
            dangerous = self.validator.dangerous_symlinks(workspace)
            if dangerous:
                raise ValidationError("symlinks peligrosos: " + ", ".join(dangerous))
        except Exception as exc:
            self._block(execution.id, str(exc))
            raise
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"base_sha": base_sha, "status": TaskExecutionStatus.WORKSPACE_READY.value,
             "blocked_reason": None, "updated_at": _now()},
        )
        return self.get(execution.id)

    # -- Codex -------------------------------------------------------------------

    async def run(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        if execution.status in {
            TaskExecutionStatus.GENERATED, TaskExecutionStatus.VALIDATED,
            TaskExecutionStatus.COMMITTED, TaskExecutionStatus.BRANCH_PUBLISHED,
            TaskExecutionStatus.PR_OPEN, TaskExecutionStatus.MERGED,
        }:
            return execution
        if execution.status is not TaskExecutionStatus.WORKSPACE_READY:
            raise ConflictError(f"ejecución no permitida desde {execution.status.value}")
        self._verify_authorization(execution)
        self._verify_workspace(execution, expect_head=execution.base_sha, clean=True)
        self._verify_remote_base(execution)
        prompt, versions, context_hash = self._implementation_context(execution)
        task = self.core.get_task(execution.task_id)
        if task.status is not TaskStatus.PENDING:
            raise ConflictError("la tarea ya tiene estado operativo incompatible")
        self.repository.update("tasks", task.id, {"prompt": prompt, "updated_at": _now()})
        session = self._implementation_session(execution)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"session_id": session.id, "instruction_versions_json": json.dumps(versions, sort_keys=True),
             "context_hash": context_hash, "status": TaskExecutionStatus.RUNNING.value,
             "blocked_reason": None, "updated_at": _now()},
        )
        runtime = self.account_configs.get(execution.account_id)
        if runtime is None:
            self._block(execution.id, "falta configuración runtime de la cuenta")
            raise ValidationError(f"falta runtime para {execution.account_id}")
        worker = LocalWorker(self.core, self.account_configs, executor_factory=self.executor_factory)
        outcome = await worker.run_task(
            task.id, session_id=session.id, runtime_config=replace(runtime, cwd=Path(execution.workspace_path)),
            execution_workflow_id=execution.id,
        )
        if outcome.status is not TaskStatus.COMPLETED:
            self._block(execution.id, f"ejecución Codex: {outcome.status}: {outcome.detail}")
            return self.get(execution.id)
        attempts = [item for item in self.core.list_attempts(task.id) if item.status.value == "SUCCEEDED"]
        if not attempts or attempts[-1].result_text is None:
            self._block(execution.id, "tarea completada sin resultado persistido")
            return self.get(execution.id)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"status": TaskExecutionStatus.GENERATED.value,
             "result_text": attempts[-1].result_text, "updated_at": _now()},
        )
        return self.get(execution.id)

    # -- validación y commit -----------------------------------------------------

    def validate(
        self, execution_id: str, *, run_approved_commands: bool = False
    ) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        if execution.status is TaskExecutionStatus.VALIDATED:
            self._verify_validation_manifest(execution)
            return execution
        if execution.status is not TaskExecutionStatus.GENERATED:
            raise ConflictError(f"validación no permitida desde {execution.status.value}")
        self._verify_authorization(execution)
        workspace = self._verify_workspace(execution, expect_head=execution.base_sha, clean=False)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"status": TaskExecutionStatus.VALIDATING.value, "updated_at": _now()},
        )
        base_report = self.validator.validate(workspace, execution.work_branch)
        errors = list(base_report.errors)
        warnings = list(base_report.warnings)
        changed = tuple(base_report.files)
        for path in changed:
            if self._sensitive_path(path):
                errors.append(f"archivo de control sensible: {path}")
            elif not self._path_allowed(path, execution.allowed_paths):
                errors.append(f"cambio fuera de rutas autorizadas: {path}")
        command_results: list[Mapping[str, Any]] = []
        if execution.validation_commands and not run_approved_commands:
            warnings.append("comandos funcionales aprobados no ejecutados")
            command_results = [
                {"argv": list(command), "status": "NOT_RUN"}
                for command in execution.validation_commands
            ]
        elif run_approved_commands:
            if not execution.validation_commands or not execution.validation_authorized_by:
                errors.append("no existen comandos aprobados para ejecutar")
            else:
                for command in execution.validation_commands:
                    result = self._run_validation_command(workspace, command)
                    command_results.append(result)
                    if result["returncode"] != 0:
                        errors.append(f"validación funcional falló: {command[0]}")
        manifest = self._change_manifest(workspace, changed)
        report = {
            "valid": not errors, "files": list(changed), "errors": errors,
            "warnings": warnings, "summary": dict(base_report.summary),
            "manifest": manifest, "commands": command_results,
            "commands_authorized_by": execution.validation_authorized_by,
        }
        values: dict[str, object] = {
            "validation_result_json": json.dumps(report, sort_keys=True), "updated_at": _now()
        }
        if errors:
            values.update({"status": TaskExecutionStatus.BLOCKED.value,
                           "blocked_reason": "; ".join(errors)})
        else:
            values.update({"status": TaskExecutionStatus.VALIDATED.value, "blocked_reason": None})
        self.repository.update("task_execution_workflows", execution.id, values)
        return self.get(execution.id)

    def commit(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        if execution.status is TaskExecutionStatus.COMMITTED:
            self._verify_commit(execution)
            return execution
        if execution.status is not TaskExecutionStatus.VALIDATED:
            raise ConflictError(f"commit no permitido desde {execution.status.value}")
        self._verify_authorization(execution)
        self._verify_validation_manifest(execution)
        workspace = self._verify_workspace(execution, expect_head=execution.base_sha, clean=False)
        planned = self.repository.get("planned_tasks", execution.planned_task_id)
        commit_sha = self.git.commit_all(
            workspace, f"task({planned['logical_id']}): implementación aprobada"
        )
        if self.git.parent(workspace, commit_sha) != execution.base_sha:
            self._block(execution.id, "el commit no desciende directamente de la base registrada")
            return self.get(execution.id)
        status = self.git.status(workspace)
        if status.head != commit_sha or status.changes:
            self._block(execution.id, "workspace inconsistente después del commit")
            return self.get(execution.id)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"status": TaskExecutionStatus.COMMITTED.value, "commit_sha": commit_sha,
             "blocked_reason": None, "updated_at": _now()},
        )
        return self.get(execution.id)

    # -- remoto y PR -------------------------------------------------------------

    def publish(self, execution_id: str, *, token: str, authorized: bool) -> TaskExecutionWorkflow:
        if not authorized:
            raise ConflictError("la publicación requiere confirmación explícita")
        execution = self.get(execution_id)
        if execution.status is TaskExecutionStatus.BRANCH_PUBLISHED:
            return self._reconcile_push(execution)
        if execution.status not in {TaskExecutionStatus.COMMITTED, TaskExecutionStatus.UNKNOWN,
                                    TaskExecutionStatus.PUSHING}:
            raise ConflictError(f"push no permitido desde {execution.status.value}")
        self._verify_authorization(execution)
        self._verify_commit(execution)
        self._verify_remote_base(execution)
        operation = self._operation(
            execution.id, "PUBLISH_TASK_BRANCH", execution.work_branch,
            str(execution.commit_sha),
        )
        branch = self.github.get_branch(execution.repository_id, execution.work_branch)
        if branch is not None:
            if self._branch_sha(branch) == execution.commit_sha:
                self._complete_operation(str(operation["id"]), str(execution.commit_sha))
                return self._published(execution.id)
            return self._block(execution.id, "rama remota incompatible")
        self._set_status(execution.id, TaskExecutionStatus.PUSHING)
        try:
            self.git.push_branch(
                Path(execution.workspace_path), execution.work_branch,
                execution.work_branch, token=token,
            )
        except GitError as exc:
            self._unknown_operation(str(operation["id"]), "git", str(exc))
            return self._set_status(
                execution.id, TaskExecutionStatus.UNKNOWN,
                "resultado del push indeterminado",
            )
        remote = self.github.get_branch(execution.repository_id, execution.work_branch)
        if self._branch_sha(remote) != execution.commit_sha:
            self._unknown_operation(str(operation["id"]), "verification", "SHA no confirmado")
            return self._set_status(execution.id, TaskExecutionStatus.UNKNOWN, "push no confirmado")
        self._complete_operation(str(operation["id"]), str(execution.commit_sha))
        return self._published(execution.id)

    def create_pr(self, execution_id: str, *, authorized: bool) -> TaskExecutionWorkflow:
        if not authorized:
            raise ConflictError("crear el PR requiere confirmación explícita")
        execution = self.get(execution_id)
        if execution.status is TaskExecutionStatus.PR_OPEN:
            return execution
        if execution.status not in {TaskExecutionStatus.BRANCH_PUBLISHED,
                                    TaskExecutionStatus.CREATING_PR,
                                    TaskExecutionStatus.UNKNOWN}:
            raise ConflictError(f"PR no permitido desde {execution.status.value}")
        self._verify_authorization(execution)
        self._verify_remote_base(execution)
        if self._branch_sha(self.github.get_branch(execution.repository_id, execution.work_branch)) != execution.commit_sha:
            return self._block(execution.id, "rama publicada no coincide con el commit")
        operation = self._operation(
            execution.id, "CREATE_TASK_PR", execution.work_branch,
            str(execution.commit_sha),
        )
        self._set_status(execution.id, TaskExecutionStatus.CREATING_PR)
        issue = self.repository.get("issue_workflows", execution.issue_workflow_id)
        revision = self.repository.get("plan_revisions", execution.plan_revision_id)
        planned = self.repository.get("planned_tasks", execution.planned_task_id)
        validation = execution.validation_result or {}
        body = (
            f"Implementación generada por vibecoder para #{issue['issue_number']}.\n\n"
            f"- Plan: revisión `{revision['revision']}` (`{revision['plan_hash']}`)\n"
            f"- Tarea: `{planned['logical_id']}` — {planned['description']}\n"
            f"- Especialista: `{self.core.get_agent(execution.agent_id).name}`\n"
            f"- Commit: `{execution.commit_sha}`\n"
            f"- Criterios: {planned['acceptance_criteria_json']}\n"
            f"- Archivos validados: {json.dumps(validation.get('files', []), ensure_ascii=False)}\n"
            f"- Validaciones funcionales: {json.dumps(validation.get('commands', []), ensure_ascii=False)}\n\n"
            "El contenido fue generado por un agente. Requiere revisión y merge humano; "
            "vibecoder no fusionará este Pull Request."
        )
        try:
            pr = self.github.create_pull_request(
                execution.repository_id,
                title=f"Task {planned['logical_id']}: {planned['description']}",
                body=body, head=execution.work_branch, base=execution.base_branch,
                idempotency_key=f"task-pr:{execution.id}:{execution.commit_sha}",
            )
        except GitHubTransientError:
            self._unknown_operation(str(operation["id"]), "github", "resultado del PR indeterminado")
            return self._set_status(execution.id, TaskExecutionStatus.UNKNOWN, "resultado del PR indeterminado")
        except (GitHubError, ConflictError) as exc:
            return self._block(execution.id, str(exc))
        self._validate_pr(execution, pr)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"status": TaskExecutionStatus.PR_OPEN.value,
             "pull_request_id": int(pr["id"]), "pull_request_number": int(pr["number"]),
             "pull_request_url": str(pr["html_url"]),
             "remote_state_json": json.dumps(pr, sort_keys=True),
             "blocked_reason": None, "updated_at": _now()},
        )
        self._complete_operation(str(operation["id"]), str(pr["id"]))
        return self.get(execution.id)

    def check_merge(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        if execution.pull_request_number is None:
            raise ConflictError("la tarea no tiene Pull Request")
        pr = self.github.get_pull_request(execution.repository_id, execution.pull_request_number)
        self._validate_pr(execution, pr)
        if bool(pr.get("merged")):
            merge_sha = str(pr.get("merge_commit_sha") or "")
            if not merge_sha:
                return self._block(execution.id, "merge sin SHA verificable")
            self.repository.update(
                "task_execution_workflows", execution.id,
                {"status": TaskExecutionStatus.MERGED.value, "merge_commit_sha": merge_sha,
                 "remote_state_json": json.dumps(pr, sort_keys=True), "updated_at": _now()},
            )
            return self.get(execution.id)
        if str(pr.get("state")) == "closed":
            return self._block(execution.id, "Pull Request cerrado sin merge")
        return self._set_status(execution.id, TaskExecutionStatus.PR_OPEN, remote_state=pr)

    # -- recuperación e inspección ----------------------------------------------

    def reconcile(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        if execution.status is TaskExecutionStatus.PREPARING:
            return self._recover_preparation(execution)
        if execution.status is TaskExecutionStatus.RUNNING:
            task = self.core.get_task(execution.task_id)
            if task.status is TaskStatus.PENDING:
                return self._set_status(execution.id, TaskExecutionStatus.WORKSPACE_READY)
            if task.status is TaskStatus.COMPLETED:
                attempts = [item for item in self.core.list_attempts(task.id) if item.status.value == "SUCCEEDED"]
                if attempts and attempts[-1].result_text is not None:
                    self.repository.update(
                        "task_execution_workflows", execution.id,
                        {"status": TaskExecutionStatus.GENERATED.value,
                         "result_text": attempts[-1].result_text, "updated_at": _now()},
                    )
                    return self.get(execution.id)
            if task.status is TaskStatus.RUNNING:
                self.core.recover_interrupted_task(task.id)
            return self._block(execution.id, f"resultado Codex indeterminado: {self.core.get_task(task.id).status.value}")
        if execution.status is TaskExecutionStatus.VALIDATING:
            return self._block(execution.id, "validación interrumpida; no se repiten comandos automáticamente")
        if execution.status in {TaskExecutionStatus.PUSHING, TaskExecutionStatus.UNKNOWN}:
            operation = self._latest_operation(execution.id)
            if operation and operation["operation_type"] == "PUBLISH_TASK_BRANCH":
                return self._reconcile_push(execution)
            if (operation and operation["operation_type"] == "CREATE_TASK_PR") or execution.pull_request_number:
                return self.create_pr(execution.id, authorized=True)
        if execution.status is TaskExecutionStatus.COMMITTED:
            self._verify_commit(execution)
        if execution.status is TaskExecutionStatus.PR_OPEN:
            return self.check_merge(execution.id)
        return self.get(execution.id)

    def inspect(self, execution_id: str) -> Mapping[str, Any]:
        execution = self.get(execution_id)
        workspace = Path(execution.workspace_path)
        result: dict[str, Any] = {"execution": asdict(execution)}
        if workspace.exists():
            result.update({"git": asdict(self.git.status(workspace)), "diff": self.git.diff(workspace)})
        return result

    def list(self, project_id: str | None = None) -> list[TaskExecutionWorkflow]:
        criteria = {"project_id": project_id} if project_id else {}
        return [self._execution(row) for row in self.repository.list("task_execution_workflows", **criteria)]

    def get(self, execution_id: str) -> TaskExecutionWorkflow:
        return self._execution(self.repository.get("task_execution_workflows", execution_id))

    def errors(self, execution_id: str) -> Mapping[str, Any]:
        execution = self.get(execution_id)
        return {"status": execution.status.value, "blocked_reason": execution.blocked_reason,
                "operations": self.repository.list("task_execution_operations", execution_id=execution.id)}

    # -- invariantes internas ----------------------------------------------------

    def _authorization(self, task_id: str) -> Mapping[str, Any]:
        task = self.core.get_task(task_id)
        if task.task_type != "PLANNED_WORK":
            raise ConflictError("la tarea no es PLANNED_WORK")
        if task.status not in {TaskStatus.PENDING, TaskStatus.COMPLETED}:
            raise ConflictError("estado operativo de tarea incompatible con M04.3")
        project = self.core.get_project(task.project_id)
        if project.status is not ProjectStatus.ACTIVE:
            raise ConflictError("el proyecto no está ACTIVE")
        planned = self.repository.find_one("planned_tasks", materialized_task_id=task.id)
        if planned is None or str(planned["assigned_agent_id"]) != task.agent_id:
            raise ConflictError("tarea sin materialización verificable")
        revision = self.repository.get("plan_revisions", str(planned["plan_revision_id"]))
        issue = self.repository.get("issue_workflows", str(revision["workflow_id"]))
        if revision["status"] != "APPROVED" or not revision["approved_event_id"]:
            raise ConflictError("el plan no está aprobado de forma verificable")
        approval = self.repository.find_one("plan_approval_events", plan_revision_id=str(revision["id"]))
        if approval is None or int(approval["github_event_id"]) != int(revision["approved_event_id"]):
            raise ConflictError("falta el evento de aprobación persistido")
        payload = json.loads(str(revision["plan_json"]))
        if _digest_json(payload) != str(revision["plan_hash"]):
            raise ConflictError("hash del plan incompatible")
        if str(issue["project_id"]) != task.project_id:
            raise ConflictError("Issue, plan y tarea pertenecen a proyectos distintos")
        agent = self.core.get_agent(task.agent_id)
        if agent.agent_type is not AgentType.SPECIALIZED:
            raise ConflictError("M04.3 solo ejecuta tareas de especialistas")
        specialist = self.agents.get(agent.id)
        agent_revision = self.agents.active_revision(agent.id)
        if specialist.status is not SpecialistStatus.ACTIVE or agent_revision is None:
            raise ConflictError("especialista sin configuración activa")
        self.agents.build_context(agent.id, self._project_checkout(task.project_id), "authorization")
        snapshots = json.loads(str(revision["specialist_snapshot_json"] or "{}"))
        snapshot = snapshots.get(specialist.logical_id)
        if not isinstance(snapshot, Mapping):
            raise ConflictError("el especialista no aparece en el snapshot aprobado")
        expected = {
            "agent_id": agent.id, "account_id": specialist.assigned_account_id,
            "configuration_revision_id": agent_revision.id,
            "configuration_hash": agent_revision.configuration_hash,
            "instructions_hash": agent_revision.instructions_hash,
        }
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ConflictError("configuración del especialista distinta de la aprobada")
        if self.core.agent_account_id(agent.id) != specialist.assigned_account_id:
            raise ConflictError("cuenta del especialista incompatible")
        return {"task": task, "project": project, "planned": planned,
                "revision": revision, "issue": issue, "agent": agent,
                "specialist": specialist, "agent_revision": agent_revision,
                "snapshot": dict(snapshot)}

    def _verify_authorization(self, execution: TaskExecutionWorkflow) -> None:
        context = self._authorization(execution.task_id)
        specialist, revision = context["specialist"], context["agent_revision"]
        if (
            str(context["planned"]["id"]) != execution.planned_task_id
            or str(context["revision"]["id"]) != execution.plan_revision_id
            or str(context["issue"]["id"]) != execution.issue_workflow_id
            or specialist.agent_id != execution.agent_id
            or specialist.assigned_account_id != execution.account_id
            or revision.id != execution.agent_revision_id
            or revision.configuration_hash != execution.agent_configuration_hash
            or revision.instructions_hash != execution.instructions_hash
            or dict(context["snapshot"]) != dict(execution.authorization_snapshot)
        ):
            raise ConflictError("la autorización cambió después de preparar la ejecución")

    def _project_checkout(self, project_id: str) -> Path:
        agent = self.repository.find_one("agents", project_id=project_id, name="main")
        if agent is None:
            raise ConflictError("el proyecto no tiene agente principal")
        try:
            status = self.git.status(self.project_workspace)
        except (GitError, OSError) as exc:
            raise ConflictError(f"checkout de contexto inválido: {exc}") from exc
        if status.changes:
            raise ConflictError("el checkout de contexto debe estar limpio")
        repository = self.repository.find_one("github_repositories", project_id=project_id)
        if repository is None:
            raise ConflictError("el proyecto no tiene repositorio registrado")
        expected = f"{str(repository['html_url']).rstrip('/')}.git"
        self.git.verify_remote(self.project_workspace, expected)
        return self.project_workspace

    def _active_git_configuration(self, project_id: str):
        bootstrap = self.repository.find_one("bootstrap_workflows", project_id=project_id)
        if bootstrap is None:
            raise ConflictError("falta bootstrap del proyecto")
        rows = [
            *self.repository.list("git_configurations", workflow_id=str(bootstrap["id"]), status="APPLIED"),
            *self.repository.list("git_configurations", workflow_id=str(bootstrap["id"]), status="APPROVED"),
        ]
        if not rows:
            raise ConflictError("falta configuración Git activa")
        return GitConfigurationService._from_row(max(rows, key=lambda row: int(row["revision"])))

    def _pending_dependencies(self, planned_task_id: str) -> tuple[str, ...]:
        deps = self.repository.list("planned_task_dependencies", task_id=planned_task_id)
        pending: list[str] = []
        for dependency in deps:
            planned = self.repository.get("planned_tasks", str(dependency["depends_on_task_id"]))
            execution = self.repository.find_one("task_execution_workflows", planned_task_id=str(planned["id"]))
            if execution is None or execution["status"] != TaskExecutionStatus.MERGED.value:
                pending.append(str(planned["logical_id"]))
        return tuple(sorted(pending))

    def _implementation_context(
        self, execution: TaskExecutionWorkflow
    ) -> tuple[str, Mapping[str, str], str]:
        revision = self.repository.get("plan_revisions", execution.plan_revision_id)
        planned = self.repository.get("planned_tasks", execution.planned_task_id)
        frozen = json.loads(str(revision["frozen_context_json"]))
        dependencies = []
        for row in self.repository.list("planned_task_dependencies", task_id=execution.planned_task_id):
            dep = self.repository.get("planned_tasks", str(row["depends_on_task_id"]))
            dep_execution = self.repository.find_one("task_execution_workflows", planned_task_id=str(dep["id"]))
            dependencies.append({"task": dep["logical_id"], "merge_commit_sha": dep_execution["merge_commit_sha"]})
        task_context = (
            "Implementa exclusivamente la tarea aprobada dentro del workspace actual. "
            "No ejecutes Git, no publiques, no cambies AGENTS.md ni `.vibecoder/`, no "
            "amplíes el alcance y no ejecutes tests o scripts. Deja los cambios sin commit.\n\n"
            f"## Contexto aprobado\n\n{json.dumps(frozen, ensure_ascii=False, indent=2)}\n\n"
            f"## Tarea\n\n{json.dumps(dict(planned), ensure_ascii=False, indent=2)}\n\n"
            f"## Dependencias integradas\n\n{json.dumps(dependencies, indent=2)}\n\n"
            f"## Rutas autorizadas\n\n{json.dumps(execution.allowed_paths)}"
        )
        context = self.agents.build_context(execution.agent_id, self._project_checkout(execution.project_id), task_context)
        if context.versions.get(f".vibecoder/agents/{self.core.get_agent(execution.agent_id).name}/AGENTS.md") != execution.instructions_hash:
            raise ConflictError("hash de instrucciones específico incompatible")
        digest = hashlib.sha256(context.text.encode("utf-8")).hexdigest()
        return context.text, context.versions, digest

    def _implementation_session(self, execution: TaskExecutionWorkflow):
        if execution.session_id:
            session = self.core.get_session(execution.session_id)
            if session.agent_id != execution.agent_id or session.codex_account_id != execution.account_id:
                raise ConflictError("sesión de implementación incompatible")
            return session
        return self.core.create_session(execution.agent_id)

    def _verify_workspace(
        self, execution: TaskExecutionWorkflow, *, expect_head: str | None, clean: bool
    ) -> Path:
        workspace = self.workspaces.assert_safe(Path(execution.workspace_path))
        if not workspace.is_dir():
            raise ConflictError("workspace inexistente")
        self.git.verify_remote(workspace, execution.remote_url)
        status = self.git.status(workspace)
        if status.branch != execution.work_branch or (expect_head and status.head != expect_head):
            raise ConflictError("rama o HEAD local incompatible")
        if clean and status.changes:
            raise ConflictError("workspace con cambios inesperados")
        dangerous = self.validator.dangerous_symlinks(workspace)
        if dangerous:
            raise ValidationError("symlinks peligrosos: " + ", ".join(dangerous))
        return workspace

    def _verify_remote_base(self, execution: TaskExecutionWorkflow) -> None:
        branch = self.github.get_branch(execution.repository_id, execution.base_branch)
        if self._branch_sha(branch) != execution.base_sha:
            raise ConflictError("la rama base remota cambió")

    def _verify_commit(self, execution: TaskExecutionWorkflow) -> None:
        if not execution.commit_sha:
            raise ConflictError("falta commit local")
        workspace = self._verify_workspace(execution, expect_head=execution.commit_sha, clean=True)
        if self.git.parent(workspace, execution.commit_sha) != execution.base_sha:
            raise ConflictError("el commit no desciende de la base registrada")

    def _verify_validation_manifest(self, execution: TaskExecutionWorkflow) -> None:
        report = execution.validation_result or {}
        files = tuple(str(path) for path in report.get("files", []))
        current = self._change_manifest(Path(execution.workspace_path), files)
        if current != report.get("manifest"):
            raise ConflictError("los cambios variaron después de la validación")

    def _change_manifest(self, workspace: Path, files: Sequence[str]) -> Mapping[str, str]:
        result: dict[str, str] = {}
        for relative in sorted(files):
            path = workspace / relative
            if path.is_symlink():
                result[relative] = "symlink:" + os.readlink(path)
            elif path.is_file():
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                result[relative] = "deleted"
        return result

    @staticmethod
    def _sensitive_path(path: str) -> bool:
        return path == "AGENTS.md" or path.startswith(".vibecoder/") or path.startswith(".git/")

    @staticmethod
    def _path_allowed(path: str, allowed: Sequence[str]) -> bool:
        candidate = PurePosixPath(path)
        return any(candidate == PurePosixPath(prefix) or PurePosixPath(prefix) in candidate.parents for prefix in allowed)

    @staticmethod
    def _allowed_paths(values: Sequence[str]) -> tuple[str, ...]:
        result: list[str] = []
        for value in values:
            path = PurePosixPath(value.strip().rstrip("/"))
            if (not value.strip() or path.is_absolute() or path in {PurePosixPath("."), PurePosixPath("")}
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or str(path) == "AGENTS.md" or str(path).startswith((".git", ".vibecoder"))):
                raise ValidationError(f"ruta autorizada no válida: {value}")
            result.append(str(path))
        if not result:
            raise ValidationError("se requiere al menos una ruta autorizada")
        return tuple(sorted(set(result)))

    @staticmethod
    def _validation_commands(commands: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
        result: list[tuple[str, ...]] = []
        for command in commands:
            normalized = tuple(str(item) for item in command if str(item))
            if not normalized or len(normalized) > 32 or any("\x00" in item for item in normalized):
                raise ValidationError("comando de validación no válido")
            result.append(normalized)
        return tuple(result)

    @staticmethod
    def _run_validation_command(workspace: Path, command: Sequence[str]) -> Mapping[str, Any]:
        try:
            result = subprocess.run(
                tuple(command), cwd=workspace, shell=False, check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
                env={"PATH": os.environ.get("PATH", ""), "HOME": str(workspace),
                     "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"},
            )
            return {"argv": list(command), "status": "COMPLETED", "returncode": result.returncode,
                    "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"argv": list(command), "status": "FAILED", "returncode": -1,
                    "stdout": "", "stderr": str(exc)}

    def _recover_preparation(self, execution: TaskExecutionWorkflow) -> TaskExecutionWorkflow:
        workspace = Path(execution.workspace_path)
        if not workspace.exists():
            return self._set_status(execution.id, TaskExecutionStatus.READY)
        try:
            self._verify_workspace(execution, expect_head=execution.base_sha or None, clean=True)
        except (ConflictError, ValidationError) as exc:
            return self._block(execution.id, f"preparación interrumpida: {exc}")
        if not execution.base_sha:
            return self._block(execution.id, "preparación interrumpida sin SHA base persistido")
        return self._set_status(execution.id, TaskExecutionStatus.WORKSPACE_READY)

    def _reconcile_push(self, execution: TaskExecutionWorkflow) -> TaskExecutionWorkflow:
        branch = self.github.get_branch(execution.repository_id, execution.work_branch)
        if self._branch_sha(branch) == execution.commit_sha:
            operation = self._latest_operation(execution.id)
            if operation:
                self._complete_operation(str(operation["id"]), str(execution.commit_sha))
            return self._published(execution.id)
        if branch is not None:
            return self._block(execution.id, "rama remota incompatible")
        return self._set_status(execution.id, TaskExecutionStatus.COMMITTED)

    def _published(self, execution_id: str) -> TaskExecutionWorkflow:
        execution = self.get(execution_id)
        self.repository.update(
            "task_execution_workflows", execution.id,
            {"status": TaskExecutionStatus.BRANCH_PUBLISHED.value,
             "published_sha": execution.commit_sha, "blocked_reason": None,
             "updated_at": _now()},
        )
        return self.get(execution.id)

    def _operation(
        self, execution_id: str, operation_type: str, expected_ref: str, expected_sha: str
    ) -> Mapping[str, Any]:
        key = f"{operation_type}:{expected_ref}:{expected_sha}"
        existing = self.repository.find_one(
            "task_execution_operations", execution_id=execution_id,
            operation_type=operation_type, idempotency_key=key,
        )
        if existing: return existing
        operation_id = str(uuid4())
        self.repository.insert(
            "task_execution_operations",
            {"id": operation_id, "execution_id": execution_id,
             "operation_type": operation_type, "idempotency_key": key,
             "status": "PENDING", "expected_ref": expected_ref,
             "expected_sha": expected_sha, "remote_id": None,
             "error_category": None, "error_message": None,
             "started_at": _now(), "finished_at": None},
        )
        return self.repository.get("task_execution_operations", operation_id)

    def _latest_operation(self, execution_id: str) -> Mapping[str, Any] | None:
        rows = self.repository.list("task_execution_operations", execution_id=execution_id)
        return rows[-1] if rows else None

    def _complete_operation(self, operation_id: str, remote_id: str) -> None:
        self.repository.update(
            "task_execution_operations", operation_id,
            {"status": "COMPLETED", "remote_id": remote_id, "error_category": None,
             "error_message": None, "finished_at": _now()},
        )

    def _unknown_operation(self, operation_id: str, category: str, message: str) -> None:
        self.repository.update(
            "task_execution_operations", operation_id,
            {"status": "UNKNOWN", "error_category": category,
             "error_message": message, "finished_at": _now()},
        )

    @staticmethod
    def _branch_sha(branch: Mapping[str, Any] | None) -> str | None:
        commit = branch.get("commit") if branch else None
        return str(commit.get("sha")) if isinstance(commit, Mapping) and commit.get("sha") else None

    @staticmethod
    def _validate_pr(execution: TaskExecutionWorkflow, pr: Mapping[str, Any]) -> None:
        head, base = pr.get("head"), pr.get("base")
        if not isinstance(head, Mapping) or not isinstance(base, Mapping):
            raise ConflictError("Pull Request sin referencias verificables")
        if (str(head.get("ref")) != execution.work_branch
                or str(head.get("sha")) != execution.commit_sha
                or str(base.get("ref")) != execution.base_branch):
            raise ConflictError("Pull Request incompatible")

    def _set_status(
        self, execution_id: str, status: TaskExecutionStatus,
        reason: str | None = None, *, remote_state: Mapping[str, Any] | None = None,
    ) -> TaskExecutionWorkflow:
        values: dict[str, object] = {"status": status.value, "blocked_reason": reason,
                                    "updated_at": _now()}
        if remote_state is not None: values["remote_state_json"] = json.dumps(remote_state, sort_keys=True)
        self.repository.update("task_execution_workflows", execution_id, values)
        return self.get(execution_id)

    def _block(self, execution_id: str, reason: str) -> TaskExecutionWorkflow:
        return self._set_status(execution_id, TaskExecutionStatus.BLOCKED, reason)

    @staticmethod
    def _execution(row: Mapping[str, Any]) -> TaskExecutionWorkflow:
        return TaskExecutionWorkflow(
            id=str(row["id"]), project_id=str(row["project_id"]),
            issue_workflow_id=str(row["issue_workflow_id"]), plan_revision_id=str(row["plan_revision_id"]),
            planned_task_id=str(row["planned_task_id"]), task_id=str(row["task_id"]),
            repository_id=str(row["repository_id"]), agent_id=str(row["agent_id"]),
            agent_revision_id=str(row["agent_revision_id"]),
            agent_configuration_hash=str(row["agent_configuration_hash"]),
            instructions_hash=str(row["instructions_hash"]), account_id=str(row["account_id"]),
            status=TaskExecutionStatus(str(row["status"])), workspace_path=str(row["workspace_path"]),
            remote_url=str(row["remote_url"]), base_branch=str(row["base_branch"]),
            base_sha=str(row["base_sha"]), work_branch=str(row["work_branch"]),
            session_id=str(row["session_id"]) if row["session_id"] else None,
            instruction_versions=json.loads(str(row["instruction_versions_json"])),
            authorization_snapshot=json.loads(str(row["authorization_snapshot_json"])),
            context_hash=str(row["context_hash"]) if row["context_hash"] else None,
            allowed_paths=tuple(json.loads(str(row["allowed_paths_json"]))),
            validation_commands=tuple(tuple(cmd) for cmd in json.loads(str(row["validation_commands_json"]))),
            validation_authorized_by=str(row["validation_authorized_by"]) if row["validation_authorized_by"] else None,
            result_text=str(row["result_text"]) if row["result_text"] else None,
            validation_result=json.loads(str(row["validation_result_json"])) if row["validation_result_json"] else None,
            commit_sha=str(row["commit_sha"]) if row["commit_sha"] else None,
            published_sha=str(row["published_sha"]) if row["published_sha"] else None,
            pull_request_id=int(row["pull_request_id"]) if row["pull_request_id"] else None,
            pull_request_number=int(row["pull_request_number"]) if row["pull_request_number"] else None,
            pull_request_url=str(row["pull_request_url"]) if row["pull_request_url"] else None,
            merge_commit_sha=str(row["merge_commit_sha"]) if row["merge_commit_sha"] else None,
            remote_state=json.loads(str(row["remote_state_json"])) if row["remote_state_json"] else None,
            blocked_reason=str(row["blocked_reason"]) if row["blocked_reason"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--project-workspace", required=True, type=Path)
    parser.add_argument("--accounts")
    parser.add_argument("--github-token-env", default="VIBECODER_GITHUB_TOKEN")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("list"); cmd.add_argument("--project-id")
    for name in ("show", "eligible", "run", "commit", "publish", "create-pr", "check-merge", "reconcile", "errors", "inspect"):
        cmd = sub.add_parser(name); cmd.add_argument("identifier")
        if name in {"publish", "create-pr"}: cmd.add_argument("--authorize-remote", action="store_true")
    cmd = sub.add_parser("prepare"); cmd.add_argument("task_id")
    cmd.add_argument("--allow-path", action="append", required=True)
    cmd.add_argument("--validation-command", action="append", default=[])
    cmd.add_argument("--validation-authorized-by")
    cmd = sub.add_parser("validate"); cmd.add_argument("identifier")
    cmd.add_argument("--run-approved-commands", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    remote_commands = {"prepare", "publish", "create-pr", "check-merge", "reconcile"}
    token = os.environ.get(args.github_token_env)
    if args.command in remote_commands and not token:
        raise SystemExit(f"falta variable {args.github_token_env}")
    configs = load_account_configs(args.accounts) if args.accounts else {}
    with SQLiteRepository(Path(args.database).resolve()) as repository, GitHubHttpClient(
        GitHubConfig(token=token or "local-command-without-remote-access")
    ) as client:
        core = OrchestratorCore(repository)
        service = TaskExecutionService(
            core, GitHubRegistryService(core, client), configs, args.workspace_root,
            project_workspace=args.project_workspace,
            orchestrator_root=Path.cwd().resolve(),
        )
        command = args.command
        if command == "list": result: Any = service.list(args.project_id)
        elif command == "eligible": result = service.eligible(args.identifier)
        elif command == "prepare":
            commands = [json.loads(value) for value in args.validation_command]
            result = service.prepare(
                args.task_id, allowed_paths=args.allow_path,
                validation_commands=commands,
                validation_authorized_by=args.validation_authorized_by,
            )
        elif command == "show": result = service.get(args.identifier)
        elif command == "run":
            if not args.accounts: raise SystemExit("--accounts es obligatorio con run")
            result = asyncio.run(service.run(args.identifier))
        elif command == "validate": result = service.validate(args.identifier, run_approved_commands=args.run_approved_commands)
        elif command == "commit": result = service.commit(args.identifier)
        elif command == "publish": result = service.publish(args.identifier, token=token or "", authorized=args.authorize_remote)
        elif command == "create-pr": result = service.create_pr(args.identifier, authorized=args.authorize_remote)
        elif command == "check-merge": result = service.check_merge(args.identifier)
        elif command == "reconcile": result = service.reconcile(args.identifier)
        elif command == "errors": result = service.errors(args.identifier)
        else: result = service.inspect(args.identifier)
        print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result,
                         indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
