"""Conversación persistente de bootstrap mediante un Issue GitHub."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from .codex_executor import AccountRuntimeConfig, CodexAppServerExecutor
from .domain import (
    AgentType,
    BootstrapComment,
    BootstrapCommentStatus,
    BootstrapStatus,
    BootstrapWorkflow,
    ProjectStatus,
    TaskStatus,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .github import GitHubConfig, GitHubError, GitHubHttpClient
from .github_service import GitHubRegistryService
from .runtime_config import load_account_configs
from .service import OrchestratorCore
from .sqlite import SQLiteRepository
from .worker import Executor, LocalWorker, WorkerOutcome


BOOTSTRAP_INSTRUCTIONS = """Eres el agente principal de una conversación de bootstrap.
Ayudas a definir un proyecto nuevo preguntando por objetivos, requisitos, restricciones
y arquitectura. Mantén una conversación progresiva. No asumas decisiones no
confirmadas y distingue claramente propuestas de decisiones aprobadas. No generes
código ni solicites crear o modificar archivos, ramas, commits o Pull Requests.
Responde en Markdown legible y céntrate en el mensaje humano actual."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class BootstrapOutcome:
    workflow_id: str
    comment_id: str | None
    task_id: str | None
    status: str
    detail: str


class BootstrapService:
    def __init__(
        self,
        core: OrchestratorCore,
        github: GitHubRegistryService,
        account_configs: dict[str, AccountRuntimeConfig],
        *,
        authorized_users: Sequence[str],
        executor_factory: Callable[[AccountRuntimeConfig], Executor] = CodexAppServerExecutor,
    ):
        self.core = core
        self.repository = core.repository
        self.github = github
        self.account_configs = account_configs
        self.executor_factory = executor_factory
        self.authorized_users = {value.casefold() for value in authorized_users if value.strip()}
        if not self.authorized_users:
            raise ValidationError("se requiere al menos un usuario GitHub autorizado")

    def start(
        self,
        project_id: str,
        *,
        description: str | None = None,
        approval_label: str = "status:approved",
    ) -> BootstrapWorkflow:
        existing = self.repository.find_one("bootstrap_workflows", project_id=project_id)
        if existing:
            return self._workflow(existing)
        project = self.core.get_project(project_id)
        if project.status not in {
            ProjectStatus.UNINITIALIZED,
            ProjectStatus.BOOTSTRAPPING,
        }:
            raise ConflictError(
                f"el proyecto no admite bootstrap desde estado {project.status.value}"
            )
        repo = self.github.get_project_repository(project_id)
        agent = self._get_or_create_principal(project_id)
        body = (
            "Este Issue mantiene la conversación de definición inicial del proyecto.\n\n"
            "Añade comentarios con objetivos, requisitos, restricciones y decisiones. "
            "Los mensajes se dirigirán al agente principal; también puedes usar "
            "`/agent main`. La aprobación requiere la etiqueta `status:approved` "
            "añadida por un usuario autorizado.\n\n"
            "Vibecoder no generará código, ramas ni Pull Requests durante este hito."
        )
        if description and description.strip():
            body += f"\n\n## Contexto inicial proporcionado\n\n{description.strip()}"
        issue = self.github.create_issue(
            repo.id,
            f"Bootstrap de {project.name}",
            body,
            idempotency_key=f"bootstrap:{project.id}:issue",
        )
        self.github.ensure_label(
            repo.id,
            approval_label,
            "0e8a16",
            "Bootstrap aprobado por una persona autorizada",
            idempotency_key=f"bootstrap:{project.id}:approval-label",
        )
        now = _now()
        workflow_id = str(uuid4())
        try:
            with self.repository.transaction():
                self.repository.insert(
                    "bootstrap_workflows",
                    {
                        "id": workflow_id,
                        "project_id": project_id,
                        "repository_id": repo.id,
                        "github_issue_id": int(issue["id"]),
                        "github_issue_node_id": str(issue["node_id"]),
                        "issue_number": int(issue["number"]),
                        "issue_url": str(issue["html_url"]),
                        "principal_agent_id": agent.id,
                        "status": BootstrapStatus.ACTIVE.value,
                        "approval_label": approval_label,
                        "approved_by": None,
                        "approved_event_id": None,
                        "approved_at": None,
                        "frozen_specification": None,
                        "last_polled_at": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                if project.status is ProjectStatus.UNINITIALIZED:
                    self.core.transition_project(project_id, ProjectStatus.BOOTSTRAPPING)
        except ConflictError:
            existing = self.repository.find_one("bootstrap_workflows", project_id=project_id)
            if existing is None:
                raise
            return self._workflow(existing)
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> BootstrapWorkflow:
        return self._workflow(self.repository.get("bootstrap_workflows", workflow_id))

    def get_project_workflow(self, project_id: str) -> BootstrapWorkflow:
        row = self.repository.find_one("bootstrap_workflows", project_id=project_id)
        if row is None:
            raise NotFoundError(f"bootstrap no iniciado para proyecto: {project_id}")
        return self._workflow(row)

    def list_comments(self, workflow_id: str) -> list[BootstrapComment]:
        self.get_workflow(workflow_id)
        rows = self.repository.list("bootstrap_comments", workflow_id=workflow_id)
        return sorted(
            (self._comment(row) for row in rows),
            key=lambda item: (item.remote_created_at, item.github_comment_id),
        )

    def poll(self, workflow_id: str) -> list[BootstrapComment]:
        workflow = self.get_workflow(workflow_id)
        self.github.validate_identity()
        own_comment_ids = self._own_comment_ids(workflow.repository_id)
        self._reclassify_legacy_ignored(workflow, own_comment_ids)
        remote = self.github.list_comments(workflow.repository_id, workflow.issue_number)
        ordered = sorted(remote, key=lambda item: (str(item["created_at"]), int(item["id"])))
        for item in ordered:
            if self.repository.find_one(
                "bootstrap_comments", github_comment_id=int(item["id"])
            ):
                continue
            user = item.get("user")
            if not isinstance(user, Mapping) or not user.get("login") or not user.get("type"):
                raise ValidationError("comentario GitHub sin identidad de autor")
            login = str(user["login"])
            author_type = str(user["type"])
            status, reason = self._classify_comment(
                workflow,
                login,
                author_type,
                str(item["body"]),
                int(item["id"]) in own_comment_ids,
            )
            now = _now()
            try:
                self.repository.insert(
                    "bootstrap_comments",
                    {
                        "id": str(uuid4()), "workflow_id": workflow.id,
                        "github_comment_id": int(item["id"]),
                        "github_node_id": str(item["node_id"]),
                        "author_login": login, "author_type": author_type,
                        "body": str(item["body"]),
                        "remote_created_at": str(item["created_at"]),
                        "status": status.value, "rejection_reason": reason,
                        "task_id": None, "response_text": None,
                        "response_github_comment_id": None,
                        "created_at": now, "updated_at": now,
                    },
                )
            except ConflictError:
                pass
        self.repository.update(
            "bootstrap_workflows", workflow.id,
            {"last_polled_at": _now(), "updated_at": _now()},
        )
        self._check_approval(workflow.id)
        return self.list_comments(workflow.id)

    async def process_round(self, workflow_id: str) -> BootstrapOutcome:
        self.poll(workflow_id)
        workflow = self.get_workflow(workflow_id)
        pending_publication = self._first_comment(
            workflow_id, BootstrapCommentStatus.RESPONSE_PENDING
        )
        if pending_publication:
            return self._publish_response(workflow, pending_publication)
        running = self._first_comment(workflow_id, BootstrapCommentStatus.RUNNING)
        if running:
            return BootstrapOutcome(
                workflow.id, running.id, running.task_id, "BUSY",
                "el comentario ya está reclamado por un worker",
            )
        active = self._first_comment(workflow_id, BootstrapCommentStatus.QUEUED)
        if active:
            return await self._continue_queued(workflow, active)
        if workflow.status is BootstrapStatus.APPROVED:
            return BootstrapOutcome(workflow.id, None, None, "APPROVED", "alcance congelado")
        blocked = self._first_comment(workflow_id, BootstrapCommentStatus.BLOCKED)
        if blocked:
            return BootstrapOutcome(
                workflow.id, blocked.id, blocked.task_id, "BLOCKED",
                blocked.rejection_reason or "requiere reconciliación",
            )
        received = self._first_comment(workflow_id, BootstrapCommentStatus.RECEIVED)
        if received is None:
            return BootstrapOutcome(workflow.id, None, None, "IDLE", "sin comentarios pendientes")
        task = self.core.create_task(
            workflow.project_id,
            workflow.principal_agent_id,
            "BOOTSTRAP_COMMENT",
            external_ref=f"github-comment:{received.github_comment_id}",
            idempotency_key=f"bootstrap-comment:{received.github_comment_id}",
            prompt=self._prompt(received),
        )
        self.repository.update(
            "bootstrap_comments", received.id,
            {"status": BootstrapCommentStatus.QUEUED.value, "task_id": task.id, "updated_at": _now()},
        )
        return await self._continue_queued(workflow, self._comment(
            self.repository.get("bootstrap_comments", received.id)
        ))

    def retry_publications(self, workflow_id: str) -> list[BootstrapOutcome]:
        workflow = self.get_workflow(workflow_id)
        results: list[BootstrapOutcome] = []
        for comment in self.list_comments(workflow_id):
            if comment.status is BootstrapCommentStatus.RESPONSE_PENDING:
                results.append(self._publish_response(workflow, comment))
        self._check_approval(workflow_id)
        return results

    def recover(self, workflow_id: str) -> list[BootstrapOutcome]:
        """Recupera reclamaciones tras reinicio sin repetir prompts inciertos."""
        workflow = self.get_workflow(workflow_id)
        results: list[BootstrapOutcome] = []
        running = [
            item for item in self.list_comments(workflow_id)
            if item.status is BootstrapCommentStatus.RUNNING
        ]
        for comment in running:
            if not comment.task_id:
                continue
            task = self.core.get_task(comment.task_id)
            if task.status is TaskStatus.RUNNING:
                task = self.core.recover_interrupted_task(task.id)
            if task.status is TaskStatus.COMPLETED:
                self._capture_result(comment.id, task.id)
                current = self._comment(self.repository.get("bootstrap_comments", comment.id))
                results.append(self._publish_response(workflow, current))
            elif task.status is TaskStatus.PENDING:
                self.repository.update(
                    "bootstrap_comments", comment.id,
                    {"status": BootstrapCommentStatus.QUEUED.value, "updated_at": _now()},
                )
                results.append(BootstrapOutcome(
                    workflow.id, comment.id, task.id, "QUEUED",
                    "reclamación previa sin intento",
                ))
            else:
                self.repository.update(
                    "bootstrap_comments", comment.id,
                    {
                        "status": BootstrapCommentStatus.BLOCKED.value,
                        "rejection_reason": f"resultado incierto: tarea {task.status.value}",
                        "updated_at": _now(),
                    },
                )
                results.append(BootstrapOutcome(
                    workflow.id, comment.id, task.id, "BLOCKED",
                    "requiere reconciliación M02",
                ))
        return results

    def errors(self, workflow_id: str) -> list[BootstrapComment]:
        return [
            item for item in self.list_comments(workflow_id)
            if item.status in {BootstrapCommentStatus.REJECTED, BootstrapCommentStatus.BLOCKED}
        ]

    async def _continue_queued(
        self, workflow: BootstrapWorkflow, comment: BootstrapComment
    ) -> BootstrapOutcome:
        if not comment.task_id:
            raise ConflictError("comentario QUEUED sin tarea")
        with self.repository.transaction():
            current = self._comment(self.repository.get("bootstrap_comments", comment.id))
            if current.status is not BootstrapCommentStatus.QUEUED:
                return BootstrapOutcome(
                    workflow.id, current.id, current.task_id, "BUSY",
                    "el comentario fue reclamado por otro worker",
                )
            self.repository.update(
                "bootstrap_comments", comment.id,
                {"status": BootstrapCommentStatus.RUNNING.value, "updated_at": _now()},
            )
        task = self.core.get_task(comment.task_id)
        if task.status is TaskStatus.COMPLETED:
            self._capture_result(comment.id, task.id)
            return self._publish_response(
                workflow, self._comment(self.repository.get("bootstrap_comments", comment.id))
            )
        if task.status is not TaskStatus.PENDING:
            self.repository.update(
                "bootstrap_comments", comment.id,
                {
                    "status": BootstrapCommentStatus.BLOCKED.value,
                    "rejection_reason": f"tarea Codex en estado {task.status.value}",
                    "updated_at": _now(),
                },
            )
            return BootstrapOutcome(workflow.id, comment.id, task.id, "BLOCKED", task.status.value)
        worker = LocalWorker(
            self.core, self.account_configs, executor_factory=self.executor_factory
        )
        outcome: WorkerOutcome = await worker.run_task(task.id)
        if outcome.status == TaskStatus.COMPLETED:
            self._capture_result(comment.id, task.id)
            return self._publish_response(
                workflow, self._comment(self.repository.get("bootstrap_comments", comment.id))
            )
        self.repository.update(
            "bootstrap_comments", comment.id,
            {
                "status": BootstrapCommentStatus.BLOCKED.value,
                "rejection_reason": f"ejecución Codex: {outcome.status}",
                "updated_at": _now(),
            },
        )
        return BootstrapOutcome(workflow.id, comment.id, task.id, "BLOCKED", outcome.detail)

    def _capture_result(self, comment_id: str, task_id: str) -> None:
        attempts = self.core.list_attempts(task_id)
        successful = [item for item in attempts if item.status.value == "SUCCEEDED"]
        if not successful or successful[-1].result_text is None:
            raise ConflictError("tarea completada sin resultado persistido")
        self.repository.update(
            "bootstrap_comments", comment_id,
            {
                "status": BootstrapCommentStatus.RESPONSE_PENDING.value,
                "response_text": successful[-1].result_text,
                "updated_at": _now(),
            },
        )

    def _publish_response(
        self, workflow: BootstrapWorkflow, comment: BootstrapComment
    ) -> BootstrapOutcome:
        if not comment.response_text:
            raise ConflictError("publicación pendiente sin respuesta persistida")
        body = f"**vibecoder · agente `main`**\n\n{comment.response_text}"
        try:
            remote = self.github.create_comment(
                workflow.repository_id,
                workflow.issue_number,
                body,
                idempotency_key=f"bootstrap-response:{comment.github_comment_id}",
            )
        except GitHubError as exc:
            return BootstrapOutcome(
                workflow.id, comment.id, comment.task_id, "RESPONSE_PENDING", str(exc)
            )
        self.repository.update(
            "bootstrap_comments", comment.id,
            {
                "status": BootstrapCommentStatus.COMPLETED.value,
                "response_github_comment_id": int(remote["id"]),
                "updated_at": _now(),
            },
        )
        self._check_approval(workflow.id)
        return BootstrapOutcome(
            workflow.id, comment.id, comment.task_id, "COMPLETED", "respuesta publicada"
        )

    def _check_approval(self, workflow_id: str) -> BootstrapWorkflow:
        workflow = self.get_workflow(workflow_id)
        if workflow.status is BootstrapStatus.APPROVED:
            return workflow
        issue = self.github.get_issue(workflow.repository_id, workflow.issue_number)
        labels = {
            str(label.get("name")) for label in issue.get("labels", [])
            if isinstance(label, Mapping) and label.get("name")
        }
        if workflow.approval_label not in labels:
            if workflow.status is BootstrapStatus.APPROVAL_PENDING:
                self.repository.update(
                    "bootstrap_workflows", workflow.id,
                    {"status": BootstrapStatus.ACTIVE.value, "updated_at": _now()},
                )
            return self.get_workflow(workflow.id)
        events = self.github.list_issue_events(workflow.repository_id, workflow.issue_number)
        label_events = []
        for event in events:
            actor = event.get("actor")
            label = event.get("label")
            if (
                event.get("event") in {"labeled", "unlabeled"}
                and isinstance(label, Mapping)
                and label.get("name") == workflow.approval_label
            ):
                label_events.append(event)
        latest = sorted(
            label_events, key=lambda item: (str(item["created_at"]), int(item["id"]))
        )[-1] if label_events else None
        actor = latest.get("actor") if latest else None
        verified = (
            latest is not None
            and latest.get("event") == "labeled"
            and isinstance(actor, Mapping)
            and str(actor.get("login", "")).casefold() in self.authorized_users
            and str(actor.get("type", "")).casefold() != "bot"
        )
        if not verified or self._has_unfinished_comments(workflow.id):
            self.repository.update(
                "bootstrap_workflows", workflow.id,
                {"status": BootstrapStatus.APPROVAL_PENDING.value, "updated_at": _now()},
            )
            return self.get_workflow(workflow.id)
        event = latest
        frozen = self._freeze_specification(workflow.id)
        now = _now()
        with self.repository.transaction():
            self.repository.update(
                "bootstrap_workflows", workflow.id,
                {
                    "status": BootstrapStatus.APPROVED.value,
                    "approved_by": str(actor["login"]),
                    "approved_event_id": int(event["id"]),
                    "approved_at": str(event["created_at"]),
                    "frozen_specification": frozen,
                    "updated_at": now,
                },
            )
            project = self.core.get_project(workflow.project_id)
            if project.status is ProjectStatus.BOOTSTRAPPING:
                self.core.transition_project(project.id, ProjectStatus.DESIGNING)
        return self.get_workflow(workflow.id)

    def _has_unfinished_comments(self, workflow_id: str) -> bool:
        return any(
            item.status in {
                BootstrapCommentStatus.RECEIVED,
                BootstrapCommentStatus.QUEUED,
                BootstrapCommentStatus.RUNNING,
                BootstrapCommentStatus.RESPONSE_PENDING,
                BootstrapCommentStatus.BLOCKED,
            }
            for item in self.list_comments(workflow_id)
        )

    def _freeze_specification(self, workflow_id: str) -> str:
        lines = ["# Especificación conversacional aprobada", ""]
        for item in self.list_comments(workflow_id):
            if item.status is BootstrapCommentStatus.COMPLETED:
                lines.extend(
                    [f"## {item.author_login}", "", item.body, "", "## Agente principal", "", item.response_text or "", ""]
                )
        return "\n".join(lines).rstrip() + "\n"

    def _classify_comment(
        self,
        workflow: BootstrapWorkflow,
        login: str,
        author_type: str,
        body: str,
        is_own_comment: bool,
    ) -> tuple[BootstrapCommentStatus, str | None]:
        if is_own_comment or author_type.casefold() == "bot":
            return BootstrapCommentStatus.IGNORED, "comentario del bot"
        if login.casefold() not in self.authorized_users:
            return BootstrapCommentStatus.REJECTED, "usuario no autorizado"
        if workflow.status is BootstrapStatus.APPROVED:
            return BootstrapCommentStatus.REJECTED, "alcance aprobado y congelado"
        first = body.lstrip().splitlines()[0].strip() if body.strip() else ""
        if first.startswith("/agent") and first != "/agent main":
            return BootstrapCommentStatus.REJECTED, "destinatario no permitido en M03.2"
        if first.startswith("/") and first != "/agent main":
            return BootstrapCommentStatus.REJECTED, "directiva desconocida en M03.2"
        return BootstrapCommentStatus.RECEIVED, None

    def _own_comment_ids(self, repository_id: str) -> set[int]:
        operations = self.repository.list(
            "github_operations",
            repository_id=repository_id,
            operation_type="CREATE_COMMENT",
            status="COMPLETED",
        )
        return {
            int(operation["remote_id"])
            for operation in operations
            if operation.get("remote_id") is not None
        }

    def _reclassify_legacy_ignored(
        self, workflow: BootstrapWorkflow, own_comment_ids: set[int]
    ) -> None:
        """Recupera comentarios humanos ignorados por la antigua regla por login."""
        if workflow.status is BootstrapStatus.APPROVED:
            return
        for comment in self.list_comments(workflow.id):
            if (
                comment.status is not BootstrapCommentStatus.IGNORED
                or comment.author_type.casefold() == "bot"
                or comment.author_login.casefold() not in self.authorized_users
                or comment.github_comment_id in own_comment_ids
            ):
                continue
            status, reason = self._classify_comment(
                workflow,
                comment.author_login,
                comment.author_type,
                comment.body,
                False,
            )
            self.repository.update(
                "bootstrap_comments",
                comment.id,
                {
                    "status": status.value,
                    "rejection_reason": reason,
                    "updated_at": _now(),
                },
            )

    def _prompt(self, comment: BootstrapComment) -> str:
        body = comment.body.lstrip()
        if body.splitlines() and body.splitlines()[0].strip() == "/agent main":
            body = "\n".join(body.splitlines()[1:]).lstrip()
        return (
            f"{BOOTSTRAP_INSTRUCTIONS}\n\n"
            f"Mensaje de @{comment.author_login}:\n\n{body}"
        )

    def _get_or_create_principal(self, project_id: str):
        principals = [
            agent for agent in self.core.list_agents(project_id)
            if agent.agent_type is AgentType.PRINCIPAL
        ]
        if principals:
            return principals[0]
        try:
            return self.core.register_agent(
                project_id, "main", AgentType.PRINCIPAL,
                config_ref="vibecoder://bootstrap/main",
                documentation_ref="docs/milestones/M03.2.md",
            )
        except ConflictError:
            principals = [
                agent for agent in self.core.list_agents(project_id)
                if agent.agent_type is AgentType.PRINCIPAL
            ]
            if not principals:
                raise
            return principals[0]

    def _first_comment(
        self, workflow_id: str, status: BootstrapCommentStatus
    ) -> BootstrapComment | None:
        items = [item for item in self.list_comments(workflow_id) if item.status is status]
        return items[0] if items else None

    @staticmethod
    def _workflow(row: Mapping[str, object]) -> BootstrapWorkflow:
        return BootstrapWorkflow(
            id=str(row["id"]), project_id=str(row["project_id"]),
            repository_id=str(row["repository_id"]),
            github_issue_id=int(row["github_issue_id"]),
            github_issue_node_id=str(row["github_issue_node_id"]),
            issue_number=int(row["issue_number"]), issue_url=str(row["issue_url"]),
            principal_agent_id=str(row["principal_agent_id"]),
            status=BootstrapStatus(str(row["status"])),
            approval_label=str(row["approval_label"]),
            approved_by=str(row["approved_by"]) if row["approved_by"] else None,
            approved_event_id=int(row["approved_event_id"]) if row["approved_event_id"] else None,
            approved_at=str(row["approved_at"]) if row["approved_at"] else None,
            frozen_specification=str(row["frozen_specification"]) if row["frozen_specification"] else None,
            last_polled_at=str(row["last_polled_at"]) if row["last_polled_at"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _comment(row: Mapping[str, object]) -> BootstrapComment:
        return BootstrapComment(
            id=str(row["id"]), workflow_id=str(row["workflow_id"]),
            github_comment_id=int(row["github_comment_id"]),
            github_node_id=str(row["github_node_id"]), author_login=str(row["author_login"]),
            author_type=str(row["author_type"]), body=str(row["body"]),
            remote_created_at=str(row["remote_created_at"]),
            status=BootstrapCommentStatus(str(row["status"])),
            rejection_reason=str(row["rejection_reason"]) if row["rejection_reason"] else None,
            task_id=str(row["task_id"]) if row["task_id"] else None,
            response_text=str(row["response_text"]) if row["response_text"] else None,
            response_github_comment_id=int(row["response_github_comment_id"])
            if row["response_github_comment_id"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--github-token-env", default="VIBECODER_GITHUB_TOKEN")
    parser.add_argument("--accounts")
    parser.add_argument("--authorized-user", action="append", default=[])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("project_id")
    start.add_argument("--description")
    for name in ("status", "poll", "retry", "recover", "errors"):
        command = sub.add_parser(name)
        command.add_argument("project_id")
    return parser


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.github_token_env)
    if not token:
        raise SystemExit(f"falta variable {args.github_token_env}")
    configs = load_account_configs(args.accounts) if args.accounts else {}
    with SQLiteRepository(Path(args.database).resolve()) as repository, GitHubHttpClient(
        GitHubConfig(token=token)
    ) as client:
        core = OrchestratorCore(repository)
        github = GitHubRegistryService(core, client)
        service = BootstrapService(
            core, github, configs, authorized_users=args.authorized_user
        )
        if args.command == "start":
            result = service.start(args.project_id, description=args.description)
        else:
            workflow = service.get_project_workflow(args.project_id)
            if args.command == "status":
                result = {"workflow": asdict(workflow), "comments": [asdict(item) for item in service.list_comments(workflow.id)]}
            elif args.command == "poll":
                if not args.accounts:
                    raise SystemExit("--accounts es obligatorio con poll")
                result = asyncio.run(service.process_round(workflow.id))
            elif args.command == "retry":
                result = service.retry_publications(workflow.id)
            elif args.command == "recover":
                result = service.recover(workflow.id)
            else:
                result = service.errors(workflow.id)
        print(json.dumps(_jsonable(result), indent=2, default=str, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
