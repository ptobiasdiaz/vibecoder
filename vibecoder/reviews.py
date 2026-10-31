"""M04.4: supervisión persistente de Pull Requests y correcciones controladas."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .agents import AgentManagementService
from .codex_executor import AccountRuntimeConfig, CodexAppServerExecutor
from .documents import InstructionLoader
from .domain import (
    AgentType, CorrectionScope, CorrectionStatus, ProjectStatus,
    PullRequestSupervision, PullRequestSupervisionStatus, SpecialistStatus,
    TaskExecutionStatus, TaskStatus,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .generation import GeneratedFilesValidator
from .git import GitAdapter, GitError
from .github import GitHubConfig, GitHubError, GitHubHttpClient, GitHubTransientError
from .github_service import GitHubRegistryService
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository
from .tasks import TaskExecutionService
from .worker import Executor, LocalWorker


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class PullRequestReviewService:
    def __init__(
        self,
        core: OrchestratorCore,
        github: GitHubRegistryService,
        account_configs: dict[str, AccountRuntimeConfig],
        project_workspace: Path,
        *,
        authorized_users: Sequence[str],
        git: GitAdapter | None = None,
        executor_factory: Callable[[AccountRuntimeConfig], Executor] = CodexAppServerExecutor,
    ):
        self.core = core
        self.repository = core.repository
        self.github = github
        self.account_configs = account_configs
        self.project_workspace = project_workspace.resolve(strict=True)
        self.authorized_users = {item.casefold() for item in authorized_users}
        self.git = git or GitAdapter()
        self.executor_factory = executor_factory
        self.agents = AgentManagementService(core, git=self.git)
        self.validator = GeneratedFilesValidator(self.git)

    # -- supervisión y polling -------------------------------------------------

    def start(self, execution_id: str) -> PullRequestSupervision:
        existing = self.repository.find_one("pull_request_supervisions", execution_id=execution_id)
        if existing:
            return self._supervision(existing)
        execution = self.repository.get("task_execution_workflows", execution_id)
        if execution["status"] not in {"PR_OPEN", "MERGED"} or not execution["pull_request_number"]:
            raise ConflictError("M04.4 requiere un PR de implementación abierto o fusionado")
        if self.core.get_project(str(execution["project_id"])).status is not ProjectStatus.ACTIVE:
            raise ConflictError("el proyecto no está ACTIVE")
        pr = self.github.get_pull_request(str(execution["repository_id"]), int(execution["pull_request_number"]))
        self._verify_pr(execution, pr, allow_merged=True)
        now, supervision_id = _now(), str(uuid4())
        status = PullRequestSupervisionStatus.MERGED if pr.get("merged") else PullRequestSupervisionStatus.MONITORING
        self.repository.insert("pull_request_supervisions", {
            "id": supervision_id, "execution_id": execution_id,
            "project_id": str(execution["project_id"]),
            "repository_id": str(execution["repository_id"]),
            "pull_request_number": int(pr["number"]), "pull_request_id": int(pr["id"]),
            "status": status.value, "review_state": "PENDING",
            "observed_head_sha": str(pr["head"]["sha"]),
            "observed_base_sha": str(pr["base"].get("sha") or "") or None,
            "merge_commit_sha": str(pr.get("merge_commit_sha") or "") or None,
            "blocked_reason": None, "last_polled_at": now,
            "created_at": now, "updated_at": now,
        })
        return self.get(supervision_id)

    def poll(self, supervision_id: str) -> PullRequestSupervision:
        supervision = self.get(supervision_id)
        execution = self.repository.get("task_execution_workflows", supervision.execution_id)
        pr = self.github.get_pull_request(supervision.repository_id, supervision.pull_request_number)
        self._verify_pr(execution, pr, allow_merged=True)
        head_sha = str(pr["head"]["sha"])
        if not pr.get("merged") and head_sha != str(execution["published_sha"]):
            return self._set_supervision(
                supervision.id, PullRequestSupervisionStatus.BLOCKED,
                "UNKNOWN", head_sha, "la rama del PR cambió fuera del workflow",
            )
        own_ids = {
            int(row["remote_id"]) for row in self.repository.list(
                "github_operations", repository_id=supervision.repository_id,
                operation_type="CREATE_COMMENT", status="COMPLETED",
            ) if row["remote_id"] is not None
        }
        sources = (
            ("ISSUE_COMMENT", self.github.list_comments(supervision.repository_id, supervision.pull_request_number)),
            ("REVIEW", self.github.list_pull_request_reviews(supervision.repository_id, supervision.pull_request_number)),
            ("REVIEW_COMMENT", self.github.list_pull_request_review_comments(supervision.repository_id, supervision.pull_request_number)),
        )
        for kind, items in sources:
            seen: set[int] = set()
            for remote in items:
                seen.add(int(remote["id"]))
                self._record_event(supervision, kind, remote, own_ids)
            for stored in self.repository.list(
                "pull_request_events", supervision_id=supervision.id, remote_kind=kind
            ):
                if int(stored["remote_id"]) not in seen and stored["status"] not in {"IGNORED", "REJECTED"}:
                    self.repository.update("pull_request_events", str(stored["id"]), {
                        "status": "BLOCKED", "rejection_reason": "evento remoto eliminado",
                        "updated_at": _now(),
                    })
        review_state = self._aggregate_review_state(supervision.id, head_sha)
        if pr.get("merged"):
            return self.complete_merge(supervision.id, remote=pr)
        if str(pr.get("state")) == "closed":
            return self._set_supervision(supervision.id, PullRequestSupervisionStatus.CLOSED_UNMERGED,
                                         review_state, head_sha, "PR cerrado sin merge")
        state = {
            "APPROVED": PullRequestSupervisionStatus.APPROVED,
            "CHANGES_REQUESTED": PullRequestSupervisionStatus.CHANGES_REQUESTED,
        }.get(review_state, PullRequestSupervisionStatus.REVIEW_PENDING)
        return self._set_supervision(supervision.id, state, review_state, head_sha)

    def events(self, supervision_id: str) -> list[Mapping[str, Any]]:
        self.get(supervision_id)
        return self.repository.list("pull_request_events", supervision_id=supervision_id)

    # -- conversación ----------------------------------------------------------

    async def process_event(self, event_id: str) -> Mapping[str, Any]:
        event = self.repository.get("pull_request_events", event_id)
        if event["status"] == "COMPLETED":
            return event
        if event["status"] == "RESPONSE_PENDING":
            return self.publish_response(event_id)
        if event["status"] not in {"RECEIVED", "QUEUED"} or not event["authorized"]:
            raise ConflictError("el evento no está autorizado para conversación")
        supervision = self.get(str(event["supervision_id"]))
        execution = self.repository.get("task_execution_workflows", supervision.execution_id)
        agent_id = str(event["recipient_agent_id"])
        prompt, versions = self._conversation_prompt(execution, event, agent_id)
        task = self.core.create_task(
            supervision.project_id, agent_id, "ISSUE_CONSULTATION",
            external_ref=f"pr:{supervision.pull_request_number}:event:{event['remote_kind']}:{event['remote_id']}",
            idempotency_key=f"pr-review:{supervision.id}:{event['remote_kind']}:{event['remote_id']}",
            prompt=prompt,
        )
        session_id = self._conversation_session(supervision.id, agent_id, versions)
        self.repository.update("pull_request_events", event_id, {
            "status": "RUNNING", "task_id": task.id, "session_id": session_id, "updated_at": _now()
        })
        worker = LocalWorker(self.core, self.account_configs, executor_factory=self.executor_factory)
        runtime = self.account_configs.get(self.core.agent_account_id(agent_id))
        if runtime is None:
            return self._block_event(event_id, "falta runtime de cuenta")
        outcome = await worker.run_task(
            task.id, session_id=session_id,
            runtime_config=replace(runtime, cwd=self.project_workspace),
        )
        if outcome.status is not TaskStatus.COMPLETED:
            return self._block_event(event_id, f"Codex: {outcome.status}: {outcome.detail}")
        attempts = self.core.list_attempts(task.id)
        response = attempts[-1].result_text if attempts else None
        if not response:
            return self._block_event(event_id, "respuesta Codex ausente")
        self.repository.update("pull_request_events", event_id, {
            "status": "RESPONSE_PENDING", "response_text": response, "updated_at": _now()
        })
        return self.publish_response(event_id)

    def publish_response(self, event_id: str) -> Mapping[str, Any]:
        event = self.repository.get("pull_request_events", event_id)
        if event["status"] == "COMPLETED":
            return event
        if event["status"] != "RESPONSE_PENDING" or not event["response_text"]:
            raise ConflictError("no existe respuesta persistida pendiente")
        supervision = self.get(str(event["supervision_id"]))
        try:
            remote = self.github.create_comment(
                supervision.repository_id, supervision.pull_request_number,
                str(event["response_text"]),
                idempotency_key=f"pr-response:{event_id}",
            )
        except GitHubTransientError:
            return event
        self.repository.update("pull_request_events", event_id, {
            "status": "COMPLETED", "response_comment_id": int(remote["id"]), "updated_at": _now()
        })
        return self.repository.get("pull_request_events", event_id)

    # -- correcciones ----------------------------------------------------------

    def prepare_correction(
        self, supervision_id: str, event_ids: Sequence[str], description: str,
        scope: CorrectionScope | str, *, actor: str, authorized: bool,
    ) -> Mapping[str, Any]:
        supervision = self.get(supervision_id)
        scope = CorrectionScope(scope)
        if not event_ids or not description.strip():
            raise ValidationError("la corrección requiere eventos y descripción")
        events = [self.repository.get("pull_request_events", item) for item in event_ids]
        if any(str(item["supervision_id"]) != supervision.id for item in events):
            raise ConflictError("evento perteneciente a otro PR")
        if any(not item["authorized"] for item in events):
            raise ConflictError("una solicitud no está autorizada")
        if any(item["commit_sha"] and item["commit_sha"] != supervision.observed_head_sha for item in events):
            raise ConflictError("una solicitud pertenece a un HEAD anterior")
        canonical_ids = tuple(sorted(set(event_ids)))
        encoded = json.dumps(canonical_ids)
        existing = self.repository.find_one(
            "pull_request_corrections", supervision_id=supervision.id,
            source_event_ids_json=encoded,
        )
        if existing:
            if (str(existing["description"]) != description.strip()
                    or str(existing["scope"]) != scope.value):
                raise ConflictError("los mismos eventos ya pertenecen a otra corrección")
            return existing
        execution = self.repository.get("task_execution_workflows", supervision.execution_id)
        now, correction_id = _now(), str(uuid4())
        if scope is not CorrectionScope.IN_SCOPE or not authorized or not actor.strip():
            reason = "cambio fuera de alcance; requiere nueva revisión del plan" if scope is CorrectionScope.OUT_OF_SCOPE else "alcance ambiguo; requiere aclaración humana"
            self.repository.insert("pull_request_corrections", {
                "id": correction_id, "supervision_id": supervision.id,
                "execution_id": supervision.execution_id, "source_event_ids_json": encoded,
                "description": description.strip(), "scope": scope.value,
                "authorized_by": None, "authorized_at": None, "status": "BLOCKED",
                "previous_sha": supervision.observed_head_sha, "new_sha": None,
                "task_id": None, "session_id": None, "result_text": None,
                "validation_result_json": None, "commit_sha": None,
                "response_text": None, "response_comment_id": None,
                "blocked_reason": reason, "created_at": now, "updated_at": now,
            })
            return self.repository.get("pull_request_corrections", correction_id)
        if supervision.status in {PullRequestSupervisionStatus.MERGED, PullRequestSupervisionStatus.CLOSED_UNMERGED}:
            raise ConflictError("el PR ya no admite correcciones")
        active = [row for row in self.repository.list(
            "pull_request_corrections", supervision_id=supervision.id
        ) if row["status"] not in {"REPLIED", "BLOCKED"}]
        if active:
            raise ConflictError("ya existe una corrección activa")
        self._verify_correction_context(execution, supervision)
        task = self.core.create_task(
            supervision.project_id, str(execution["agent_id"]), "REVIEW_CORRECTION",
            external_ref=f"pr:{supervision.pull_request_number}:correction:{correction_id}",
            idempotency_key=f"review-correction:{correction_id}", prompt="pending",
            _allow_review_correction=True,
        )
        self.repository.insert("pull_request_corrections", {
            "id": correction_id, "supervision_id": supervision.id,
            "execution_id": supervision.execution_id, "source_event_ids_json": encoded,
            "description": description.strip(), "scope": scope.value,
            "authorized_by": actor.strip(), "authorized_at": now, "status": "AUTHORIZED",
            "previous_sha": supervision.observed_head_sha, "new_sha": None,
            "task_id": task.id, "session_id": execution["session_id"], "result_text": None,
            "validation_result_json": None, "commit_sha": None,
            "response_text": None, "response_comment_id": None,
            "blocked_reason": None, "created_at": now, "updated_at": now,
        })
        return self.repository.get("pull_request_corrections", correction_id)

    async def run_correction(self, correction_id: str) -> Mapping[str, Any]:
        correction = self.repository.get("pull_request_corrections", correction_id)
        if correction["status"] == "GENERATED":
            return correction
        if correction["status"] != "AUTHORIZED":
            raise ConflictError("la corrección no está autorizada para ejecutar")
        supervision = self.get(str(correction["supervision_id"]))
        execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
        self._verify_correction_context(execution, supervision, expected_head=str(correction["previous_sha"]))
        prompt = self._correction_prompt(execution, correction)
        self.repository.update("tasks", str(correction["task_id"]), {"prompt": prompt, "updated_at": _now()})
        self.repository.update("pull_request_corrections", correction_id, {"status": "RUNNING", "updated_at": _now()})
        runtime = self.account_configs.get(str(execution["account_id"]))
        if runtime is None:
            return self._block_correction(correction_id, "falta runtime de cuenta")
        worker = LocalWorker(self.core, self.account_configs, executor_factory=self.executor_factory)
        outcome = await worker.run_task(
            str(correction["task_id"]), session_id=str(execution["session_id"]),
            runtime_config=replace(runtime, cwd=Path(str(execution["workspace_path"]))),
            execution_workflow_id=correction_id,
        )
        if outcome.status is not TaskStatus.COMPLETED:
            status = "UNKNOWN" if outcome.status is TaskStatus.WAITING else "BLOCKED"
            self.repository.update("pull_request_corrections", correction_id, {
                "status": status, "blocked_reason": f"Codex: {outcome.status}: {outcome.detail}",
                "updated_at": _now(),
            })
            return self.repository.get("pull_request_corrections", correction_id)
        attempts = self.core.list_attempts(str(correction["task_id"]))
        result = attempts[-1].result_text if attempts else None
        self.repository.update("pull_request_corrections", correction_id, {
            "status": "GENERATED", "result_text": result, "updated_at": _now()
        })
        return self.repository.get("pull_request_corrections", correction_id)

    def validate_correction(self, correction_id: str) -> Mapping[str, Any]:
        correction = self.repository.get("pull_request_corrections", correction_id)
        if correction["status"] == "VALIDATED":
            return correction
        if correction["status"] != "GENERATED":
            raise ConflictError("corrección no generada")
        execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
        workspace = Path(str(execution["workspace_path"]))
        report = self.validator.validate(workspace, str(execution["work_branch"]))
        errors = list(report.errors)
        allowed = tuple(json.loads(str(execution["allowed_paths_json"])))
        for path in report.files:
            if TaskExecutionService._sensitive_path(path):
                errors.append(f"archivo de control sensible: {path}")
            elif not TaskExecutionService._path_allowed(path, allowed):
                errors.append(f"cambio fuera de rutas autorizadas: {path}")
        payload = {"valid": not errors, "files": list(report.files), "errors": errors,
                   "warnings": list(report.warnings), "summary": dict(report.summary),
                   "manifest": self._manifest(workspace, report.files)}
        self.repository.update("pull_request_corrections", correction_id, {
            "status": "VALIDATED" if not errors else "BLOCKED",
            "validation_result_json": json.dumps(payload, sort_keys=True),
            "blocked_reason": "; ".join(errors) if errors else None, "updated_at": _now(),
        })
        return self.repository.get("pull_request_corrections", correction_id)

    def commit_correction(self, correction_id: str) -> Mapping[str, Any]:
        correction = self.repository.get("pull_request_corrections", correction_id)
        if correction["status"] == "COMMITTED":
            return correction
        if correction["status"] != "VALIDATED":
            raise ConflictError("corrección no validada")
        execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
        workspace = Path(str(execution["workspace_path"]))
        validation = json.loads(str(correction["validation_result_json"] or "{}"))
        if self._manifest(workspace, validation.get("files", [])) != validation.get("manifest"):
            return self._block_correction(correction_id, "los cambios variaron después de validar")
        if self.git.status(workspace).head != correction["previous_sha"]:
            return self._block_correction(correction_id, "HEAD local cambió antes del commit")
        commit_sha = self.git.commit_all(workspace, f"fix(task): {correction['description']}")
        if self.git.parent(workspace, commit_sha) != correction["previous_sha"]:
            return self._block_correction(correction_id, "el commit no desciende del SHA autorizado")
        self.repository.update("pull_request_corrections", correction_id, {
            "status": "COMMITTED", "commit_sha": commit_sha, "new_sha": commit_sha,
            "updated_at": _now(),
        })
        return self.repository.get("pull_request_corrections", correction_id)

    def publish_correction(self, correction_id: str, *, token: str, authorized: bool) -> Mapping[str, Any]:
        if not authorized:
            raise ConflictError("el push requiere confirmación explícita")
        correction = self.repository.get("pull_request_corrections", correction_id)
        if correction["status"] == "PUBLISHED":
            return correction
        if correction["status"] not in {"COMMITTED", "PUSHING", "UNKNOWN"}:
            raise ConflictError("corrección no preparada para push")
        supervision = self.get(str(correction["supervision_id"]))
        execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
        remote = self.github.get_branch(supervision.repository_id, str(execution["work_branch"]))
        remote_sha = TaskExecutionService._branch_sha(remote)
        if remote_sha == correction["new_sha"]:
            return self._correction_published(correction_id)
        if remote_sha != correction["previous_sha"]:
            return self._block_correction(correction_id, "la rama remota cambió externamente")
        self.repository.update("pull_request_corrections", correction_id, {"status": "PUSHING", "updated_at": _now()})
        try:
            self.git.push_branch(
                Path(str(execution["workspace_path"])), str(execution["work_branch"]),
                str(execution["work_branch"]), token=token,
            )
        except GitError:
            remote = self.github.get_branch(supervision.repository_id, str(execution["work_branch"]))
            if TaskExecutionService._branch_sha(remote) != correction["new_sha"]:
                self.repository.update("pull_request_corrections", correction_id, {
                    "status": "UNKNOWN", "blocked_reason": "resultado del push indeterminado",
                    "updated_at": _now(),
                })
                return self.repository.get("pull_request_corrections", correction_id)
        return self._correction_published(correction_id)

    def reply_correction(self, correction_id: str) -> Mapping[str, Any]:
        correction = self.repository.get("pull_request_corrections", correction_id)
        if correction["status"] == "REPLIED":
            self._close_correction_source_events(correction)
            return correction
        if correction["status"] != "PUBLISHED":
            raise ConflictError("la corrección no está publicada")
        supervision = self.get(str(correction["supervision_id"]))
        validation = json.loads(str(correction["validation_result_json"] or "{}"))
        body = str(correction["response_text"] or (
            f"Corrección publicada en `{correction['new_sha']}`.\n\n"
            f"Solicitud: {correction['description']}\n\n"
            f"Archivos validados: {json.dumps(validation.get('files', []), ensure_ascii=False)}\n\n"
            "La observación queda atendida técnicamente, pendiente de verificación humana."
        ))
        if not correction["response_text"]:
            self.repository.update("pull_request_corrections", correction_id, {
                "response_text": body, "updated_at": _now()
            })
        try:
            remote = self.github.create_comment(
                supervision.repository_id, supervision.pull_request_number, body,
                idempotency_key=f"correction-reply:{correction_id}:{correction['new_sha']}",
            )
        except GitHubTransientError:
            return self.repository.get("pull_request_corrections", correction_id)
        with self.repository.transaction():
            self.repository.update("pull_request_corrections", correction_id, {
                "status": "REPLIED", "response_comment_id": int(remote["id"]), "updated_at": _now()
            })
            self._close_correction_source_events(
                self.repository.get("pull_request_corrections", correction_id)
            )
        return self.repository.get("pull_request_corrections", correction_id)

    def _close_correction_source_events(self, correction: Mapping[str, Any]) -> None:
        source_ids = tuple(json.loads(str(correction["source_event_ids_json"])))
        pending = [self.repository.get("pull_request_events", str(event_id)) for event_id in source_ids]
        pending = [event for event in pending if event["status"] == "RECEIVED"]
        if not pending or not correction["response_comment_id"]:
            return
        now = _now()
        primary, *duplicates = pending
        self.repository.update("pull_request_events", str(primary["id"]), {
            "status": "COMPLETED", "response_text": correction["response_text"],
            "response_comment_id": correction["response_comment_id"], "updated_at": now,
        })
        for event in duplicates:
            self.repository.update("pull_request_events", str(event["id"]), {
                "status": "IGNORED", "rejection_reason": "atendido por la misma corrección",
                "updated_at": now,
            })

    # -- merge, recuperación y consultas --------------------------------------

    def complete_merge(
        self, supervision_id: str, *, remote: Mapping[str, Any] | None = None
    ) -> PullRequestSupervision:
        supervision = self.get(supervision_id)
        if supervision.status is PullRequestSupervisionStatus.MERGED:
            return supervision
        execution = self.repository.get("task_execution_workflows", supervision.execution_id)
        pr = remote or self.github.get_pull_request(supervision.repository_id, supervision.pull_request_number)
        self._verify_pr(execution, pr, allow_merged=True)
        if not pr.get("merged") or not pr.get("merge_commit_sha"):
            raise ConflictError("el merge no está confirmado")
        expected = str(execution["published_sha"])
        if str(pr["head"].get("sha")) != expected:
            raise ConflictError("el HEAD fusionado no coincide con el último commit publicado")
        commits = self.github.list_pull_request_commits(supervision.repository_id, supervision.pull_request_number)
        if expected not in {str(item.get("sha")) for item in commits}:
            raise ConflictError("el PR fusionado no contiene el último commit publicado")
        now = _now()
        with self.repository.transaction():
            self.repository.update("pull_request_supervisions", supervision.id, {
                "status": "MERGED", "review_state": "MERGED",
                "observed_head_sha": str(pr["head"]["sha"]),
                "merge_commit_sha": str(pr["merge_commit_sha"]),
                "blocked_reason": None, "updated_at": now,
            })
            self.repository.update("task_execution_workflows", supervision.execution_id, {
                "status": "MERGED", "merge_commit_sha": str(pr["merge_commit_sha"]),
                "remote_state_json": json.dumps(pr, sort_keys=True), "updated_at": now,
            })
        return self.get(supervision.id)

    def reconcile(self, supervision_id: str) -> PullRequestSupervision:
        supervision = self.poll(supervision_id)
        for correction in self.repository.list("pull_request_corrections", supervision_id=supervision_id):
            if correction["status"] in {"PUSHING", "UNKNOWN"} and correction["new_sha"]:
                execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
                branch = self.github.get_branch(supervision.repository_id, str(execution["work_branch"]))
                if TaskExecutionService._branch_sha(branch) == correction["new_sha"]:
                    self._correction_published(str(correction["id"]))
            if correction["status"] == "PUBLISHED" and correction["response_text"]:
                self.reply_correction(str(correction["id"]))
        for event in self.repository.list("pull_request_events", supervision_id=supervision_id, status="RESPONSE_PENDING"):
            self.publish_response(str(event["id"]))
        return self.get(supervision_id)

    def list(self, project_id: str | None = None) -> list[PullRequestSupervision]:
        criteria = {"project_id": project_id} if project_id else {}
        return [self._supervision(row) for row in self.repository.list("pull_request_supervisions", **criteria)]

    def get(self, supervision_id: str) -> PullRequestSupervision:
        return self._supervision(self.repository.get("pull_request_supervisions", supervision_id))

    def corrections(self, supervision_id: str) -> list[Mapping[str, Any]]:
        self.get(supervision_id)
        return self.repository.list("pull_request_corrections", supervision_id=supervision_id)

    def errors(self, supervision_id: str) -> Mapping[str, Any]:
        supervision = self.get(supervision_id)
        return {"status": supervision.status.value, "blocked_reason": supervision.blocked_reason,
                "blocked_events": self.repository.list("pull_request_events", supervision_id=supervision_id, status="BLOCKED"),
                "blocked_corrections": self.repository.list("pull_request_corrections", supervision_id=supervision_id, status="BLOCKED")}

    # -- helpers ---------------------------------------------------------------

    def _record_event(self, supervision, kind, remote, own_ids) -> None:
        remote_id = int(remote["id"])
        existing = self.repository.find_one(
            "pull_request_events", supervision_id=supervision.id,
            remote_kind=kind, remote_id=remote_id,
        )
        if existing:
            if (str(existing["body"]) != str(remote.get("body") or "")
                    or str(existing["remote_updated_at"] or "") != str(remote.get("updated_at") or "")):
                self.repository.update("pull_request_events", str(existing["id"]), {
                    "status": "BLOCKED", "rejection_reason": "evento remoto editado",
                    "updated_at": _now(),
                })
            return
        user = remote.get("user") if isinstance(remote.get("user"), Mapping) else {}
        login, user_type = str(user.get("login") or "unknown"), str(user.get("type") or "Unknown")
        own = remote_id in own_ids
        authorized = login.casefold() in self.authorized_users and user_type.casefold() != "bot"
        try:
            body = str(remote.get("body") or "")
            recipient = self._recipient(supervision, body) if authorized and not own and body.strip() else None
            status, reason = (("RECEIVED", None) if recipient else
                              (("IGNORED", "comentario propio") if own else
                               (("IGNORED", "evento sin conversación") if authorized else
                                ("REJECTED", "usuario no autorizado"))))
        except (ConflictError, ValidationError, NotFoundError) as exc:
            recipient, status, reason = None, "BLOCKED", str(exc)
        created = str(remote.get("submitted_at") or remote.get("created_at") or _now())
        self.repository.insert("pull_request_events", {
            "id": str(uuid4()), "supervision_id": supervision.id, "remote_kind": kind,
            "remote_id": remote_id, "remote_node_id": remote.get("node_id"),
            "author_id": int(user["id"]) if user.get("id") is not None else None,
            "author_login": login, "author_type": user_type,
            "body": str(remote.get("body") or ""), "review_state": remote.get("state") if kind == "REVIEW" else None,
            "commit_sha": remote.get("commit_id"), "path": remote.get("path"),
            "remote_created_at": created, "remote_updated_at": remote.get("updated_at"),
            "authorized": int(authorized and not own), "recipient_agent_id": recipient,
            "status": status, "rejection_reason": reason, "task_id": None,
            "session_id": None, "response_text": None, "response_comment_id": None,
            "created_at": _now(), "updated_at": _now(),
        })

    def _recipient(self, supervision, body: str) -> str:
        execution = self.repository.get("task_execution_workflows", supervision.execution_id)
        first = body.lstrip().splitlines()[0].strip() if body.strip() else ""
        if first.startswith("/agent "):
            logical = first.removeprefix("/agent ").strip()
            if logical == "main":
                agents = [a for a in self.core.list_agents(supervision.project_id) if a.agent_type is AgentType.PRINCIPAL]
                if len(agents) != 1: raise ConflictError("agente principal ambiguo")
                return agents[0].id
            specialist = self.agents.get_by_logical_id(supervision.project_id, logical)
            if specialist.status is not SpecialistStatus.ACTIVE:
                raise ConflictError("especialista no activo")
            return specialist.agent_id
        specialist = self.agents.get(str(execution["agent_id"]))
        if specialist.status is not SpecialistStatus.ACTIVE:
            raise ConflictError("especialista responsable no activo")
        return specialist.agent_id

    def _aggregate_review_state(self, supervision_id: str, head_sha: str) -> str:
        reviews = [row for row in self.repository.list(
            "pull_request_events", supervision_id=supervision_id, remote_kind="REVIEW"
        ) if row["authorized"] and row["commit_sha"] == head_sha]
        latest: dict[str, Mapping[str, Any]] = {}
        for row in sorted(reviews, key=lambda item: (str(item["remote_created_at"]), int(item["remote_id"]))):
            key = f"id:{row['author_id']}" if row["author_id"] is not None else f"login:{str(row['author_login']).casefold()}"
            latest[key] = row
        states = {str(row["review_state"]).upper() for row in latest.values()}
        if "CHANGES_REQUESTED" in states: return "CHANGES_REQUESTED"
        if "APPROVED" in states: return "APPROVED"
        return "PENDING"

    def _conversation_prompt(self, execution, event, agent_id):
        body = str(event["body"])
        if body.lstrip().startswith("/agent "):
            body = "\n".join(body.splitlines()[1:]).lstrip()
        task_context = (
            "Responde únicamente a la observación de revisión. No modifiques archivos, "
            "no ejecutes Git y no interpretes el comentario como autorización de alcance.\n\n"
            f"PR #{self.get(str(event['supervision_id'])).pull_request_number}\n"
            f"Tipo: {event['remote_kind']}\nEstado review: {event['review_state']}\n\n{body}"
        )
        agent = self.core.get_agent(agent_id)
        context = (self.agents.build_context(agent_id, self.project_workspace, task_context)
                   if agent.agent_type is AgentType.SPECIALIZED
                   else InstructionLoader(self.project_workspace).load("main", task_context))
        return context.text, context.versions

    def _conversation_session(self, supervision_id, agent_id, versions):
        account_id = self.core.agent_account_id(agent_id)
        encoded = json.dumps(dict(versions), sort_keys=True)
        current = self.repository.find_one("pull_request_agent_sessions", supervision_id=supervision_id, agent_id=agent_id)
        if current and current["account_id"] == account_id and current["instruction_versions_json"] == encoded:
            return str(current["session_id"])
        session = self.core.create_session(agent_id)
        now = _now()
        if current:
            self.repository.update("pull_request_agent_sessions", str(current["id"]), {
                "session_id": session.id, "account_id": account_id,
                "instruction_versions_json": encoded, "updated_at": now,
            })
        else:
            self.repository.insert("pull_request_agent_sessions", {
                "id": str(uuid4()), "supervision_id": supervision_id, "agent_id": agent_id,
                "session_id": session.id, "account_id": account_id,
                "instruction_versions_json": encoded, "created_at": now, "updated_at": now,
            })
        return session.id

    def _correction_prompt(self, execution, correction):
        revision = self.repository.get("plan_revisions", str(execution["plan_revision_id"]))
        planned = self.repository.get("planned_tasks", str(execution["planned_task_id"]))
        events = [self.repository.get("pull_request_events", item) for item in json.loads(str(correction["source_event_ids_json"]))]
        return (
            "Corrige exclusivamente la solicitud autorizada. No ejecutes Git, tests o scripts; "
            "no cambies AGENTS.md ni .vibecoder y deja cambios sin commit.\n\n"
            f"Contexto aprobado:\n{revision['frozen_context_json']}\n\n"
            f"Tarea original:\n{json.dumps(dict(planned), ensure_ascii=False)}\n\n"
            f"Corrección autorizada: {correction['description']}\n"
            f"Eventos: {json.dumps([{'kind': e['remote_kind'], 'id': e['remote_id'], 'body': e['body']} for e in events], ensure_ascii=False)}\n"
            f"SHA actual: {correction['previous_sha']}"
        )

    def _verify_correction_context(self, execution, supervision, expected_head=None):
        if self.core.get_project(str(execution["project_id"])).status is not ProjectStatus.ACTIVE:
            raise ConflictError("proyecto no activo")
        specialist = self.agents.get(str(execution["agent_id"]))
        revision = self.agents.active_revision(specialist.agent_id)
        if specialist.status is not SpecialistStatus.ACTIVE or revision is None:
            raise ConflictError("especialista no activo")
        if revision.id != execution["agent_revision_id"] or revision.configuration_hash != execution["agent_configuration_hash"] or revision.instructions_hash != execution["instructions_hash"]:
            raise ConflictError("configuración del especialista modificada")
        plan = self.repository.get("plan_revisions", str(execution["plan_revision_id"]))
        encoded = json.dumps(json.loads(str(plan["plan_json"])), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != plan["plan_hash"]:
            raise ConflictError("el plan aprobado fue modificado")
        if not execution["session_id"]:
            raise ConflictError("falta sesión de implementación")
        session = self.core.get_session(str(execution["session_id"]))
        if session.agent_id != specialist.agent_id or session.codex_account_id != specialist.assigned_account_id:
            raise ConflictError("sesión de implementación incompatible")
        self.agents.build_context(specialist.agent_id, self.project_workspace, "review correction")
        workspace = Path(str(execution["workspace_path"]))
        status = self.git.status(workspace)
        expected = expected_head or supervision.observed_head_sha
        if status.branch != execution["work_branch"] or status.head != expected or status.changes:
            raise ConflictError("workspace incompatible o modificado")
        branch = self.github.get_branch(supervision.repository_id, str(execution["work_branch"]))
        if TaskExecutionService._branch_sha(branch) != expected:
            raise ConflictError("SHA remoto incompatible")

    @staticmethod
    def _verify_pr(execution, pr, *, allow_merged):
        if int(pr["id"]) != int(execution["pull_request_id"]):
            raise ConflictError("identidad del PR incompatible")
        if str(pr["number"]) != str(execution["pull_request_number"]):
            raise ConflictError("número del PR incompatible")
        if str(pr["head"].get("ref")) != str(execution["work_branch"]) or str(pr["base"].get("ref")) != str(execution["base_branch"]):
            raise ConflictError("ramas del PR incompatibles")
        if not allow_merged and pr.get("merged"):
            raise ConflictError("PR ya fusionado")

    def _correction_published(self, correction_id):
        correction = self.repository.get("pull_request_corrections", correction_id)
        supervision = self.get(str(correction["supervision_id"]))
        execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
        branch = self.github.get_branch(supervision.repository_id, str(execution["work_branch"]))
        if TaskExecutionService._branch_sha(branch) != correction["new_sha"]:
            return self._block_correction(correction_id, "SHA remoto de corrección no confirmado")
        now = _now()
        with self.repository.transaction():
            self.repository.update("pull_request_corrections", correction_id, {
                "status": "PUBLISHED", "blocked_reason": None, "updated_at": now,
            })
            self.repository.update("task_execution_workflows", str(correction["execution_id"]), {
                "commit_sha": correction["new_sha"], "published_sha": correction["new_sha"],
                "status": "PR_OPEN", "updated_at": now,
            })
            self.repository.update("pull_request_supervisions", supervision.id, {
                "status": "WAITING_FOR_REVIEW", "review_state": "PENDING",
                "observed_head_sha": correction["new_sha"], "updated_at": now,
            })
        return self.repository.get("pull_request_corrections", correction_id)

    def _set_supervision(self, supervision_id, status, review_state, head_sha, reason=None):
        self.repository.update("pull_request_supervisions", supervision_id, {
            "status": status.value, "review_state": review_state,
            "observed_head_sha": head_sha, "blocked_reason": reason,
            "last_polled_at": _now(), "updated_at": _now(),
        })
        return self.get(supervision_id)

    def _block_event(self, event_id, reason):
        self.repository.update("pull_request_events", event_id, {
            "status": "BLOCKED", "rejection_reason": reason, "updated_at": _now(),
        })
        return self.repository.get("pull_request_events", event_id)

    def _block_correction(self, correction_id, reason):
        self.repository.update("pull_request_corrections", correction_id, {
            "status": "BLOCKED", "blocked_reason": reason, "updated_at": _now(),
        })
        return self.repository.get("pull_request_corrections", correction_id)

    @staticmethod
    def _manifest(workspace: Path, files: Sequence[str]) -> Mapping[str, str]:
        manifest: dict[str, str] = {}
        for relative in sorted(str(item) for item in files):
            path = workspace / relative
            if path.is_symlink():
                manifest[relative] = "symlink:" + os.readlink(path)
            elif path.is_file():
                manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                manifest[relative] = "deleted"
        return manifest

    @staticmethod
    def _supervision(row):
        return PullRequestSupervision(
            id=str(row["id"]), execution_id=str(row["execution_id"]),
            project_id=str(row["project_id"]), repository_id=str(row["repository_id"]),
            pull_request_number=int(row["pull_request_number"]),
            status=PullRequestSupervisionStatus(str(row["status"])),
            review_state=str(row["review_state"]), observed_head_sha=str(row["observed_head_sha"]),
            merge_commit_sha=str(row["merge_commit_sha"]) if row["merge_commit_sha"] else None,
            blocked_reason=str(row["blocked_reason"]) if row["blocked_reason"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--project-workspace", required=True, type=Path)
    parser.add_argument("--accounts", required=True)
    parser.add_argument("--authorized-user", action="append", required=True)
    parser.add_argument("--github-token-env", default="VIBECODER_GITHUB_TOKEN")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "show", "poll", "events", "corrections", "reconcile", "errors", "process", "validate", "commit", "reply", "check-merge"):
        cmd = sub.add_parser(name); cmd.add_argument("identifier")
    cmd = sub.add_parser("list"); cmd.add_argument("--project-id")
    cmd = sub.add_parser("prepare-correction"); cmd.add_argument("supervision_id")
    cmd.add_argument("--event-id", action="append", required=True); cmd.add_argument("--description", required=True)
    cmd.add_argument("--scope", choices=[item.value for item in CorrectionScope], required=True)
    cmd.add_argument("--actor", required=True); cmd.add_argument("--authorize", action="store_true")
    cmd = sub.add_parser("run-correction"); cmd.add_argument("identifier")
    cmd = sub.add_parser("publish"); cmd.add_argument("identifier"); cmd.add_argument("--authorize-remote", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.github_token_env)
    if not token: raise SystemExit(f"falta variable {args.github_token_env}")
    repository = SQLiteRepository(Path(args.database).resolve())
    client = GitHubHttpClient(GitHubConfig(token=token))
    try:
        core = OrchestratorCore(repository)
        service = PullRequestReviewService(
            core, GitHubRegistryService(core, client), load_account_configs(args.accounts),
            args.project_workspace, authorized_users=args.authorized_user,
        )
        command = args.command
        if command == "start": result: Any = service.start(args.identifier)
        elif command == "show": result = service.get(args.identifier)
        elif command == "list": result = service.list(args.project_id)
        elif command == "poll": result = service.poll(args.identifier)
        elif command == "events": result = service.events(args.identifier)
        elif command == "corrections": result = service.corrections(args.identifier)
        elif command == "process": result = asyncio.run(service.process_event(args.identifier))
        elif command == "prepare-correction": result = service.prepare_correction(args.supervision_id, args.event_id, args.description, args.scope, actor=args.actor, authorized=args.authorize)
        elif command == "run-correction": result = asyncio.run(service.run_correction(args.identifier))
        elif command == "validate": result = service.validate_correction(args.identifier)
        elif command == "commit": result = service.commit_correction(args.identifier)
        elif command == "publish": result = service.publish_correction(args.identifier, token=token, authorized=args.authorize_remote)
        elif command == "reply": result = service.reply_correction(args.identifier)
        elif command == "check-merge": result = service.complete_merge(args.identifier)
        elif command == "reconcile": result = service.reconcile(args.identifier)
        else: result = service.errors(args.identifier)
        print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result, indent=2, ensure_ascii=False, default=str))
    finally:
        client.close(); repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
