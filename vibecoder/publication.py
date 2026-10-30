"""Publicación remota recuperable del bootstrap, sin merge automático."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .domain import (
    BootstrapStatus,
    GenerationStatus,
    GitConfigurationStatus,
    ProjectStatus,
    PublicationStatus,
    PublicationWorkflow,
)
from .errors import ConflictError, NotFoundError
from .generation import GenerationService
from .git import GitAdapter, GitError
from .git_config import GitConfigurationService
from .github import GitHubConfig, GitHubError, GitHubHttpClient, GitHubTransientError
from .github_service import GitHubRegistryService
from .service import OrchestratorCore
from .sqlite import SQLiteRepository


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class PublicationService:
    def __init__(self, core: OrchestratorCore, github: GitHubRegistryService, *, git: GitAdapter | None = None):
        self.core = core
        self.repository = core.repository
        self.github = github
        self.git = git or GitAdapter()

    def prepare(self, project_id: str) -> PublicationWorkflow:
        existing = self.repository.find_one("publication_workflows", project_id=project_id)
        if existing:
            return self._publication(existing)
        project = self.core.get_project(project_id)
        generation_row = self.repository.find_one("generation_workflows", project_id=project_id)
        if generation_row is None:
            raise NotFoundError("el proyecto no tiene generación local")
        generation = GenerationService._generation(generation_row)
        if generation.status is not GenerationStatus.READY_TO_PUBLISH or not generation.commit_hash:
            raise ConflictError("la generación no está validada y preparada para publicación")
        if not generation.git_configuration_id or not generation.git_configuration_hash:
            raise ConflictError("una generación histórica requiere reconciliación y nueva aprobación")
        config = GitConfigurationService._from_row(
            self.repository.get("git_configurations", generation.git_configuration_id)
        )
        bootstrap = self.repository.get("bootstrap_workflows", generation.bootstrap_workflow_id)
        if BootstrapStatus(str(bootstrap["status"])) is not BootstrapStatus.APPROVED:
            raise ConflictError("el bootstrap no está aprobado")
        if config.status not in {GitConfigurationStatus.APPROVED, GitConfigurationStatus.APPLIED}:
            raise ConflictError("la configuración Git no está aprobada")
        if (
            config.repository_id != generation.repository_id
            or config.workflow_id != generation.bootstrap_workflow_id
            or config.configuration_hash != generation.git_configuration_hash
            or config.specification_hash != generation.specification_hash
            or GitConfigurationService.hash_payload(GitConfigurationService._payload(config))
            != config.configuration_hash
        ):
            raise ConflictError("hashes o relaciones de autorización incompatibles")
        required = {"PUBLISH_BOOTSTRAP_BRANCH", "CREATE_PULL_REQUEST"}
        if not config.base_branch_exists:
            required.add("CREATE_TECHNICAL_BASE")
        missing = required - set(config.authorized_operations)
        if missing:
            raise ConflictError("operaciones remotas no autorizadas: " + ", ".join(sorted(missing)))
        self.github.validate_repository_access(generation.repository_id, require_write=True)
        workspace = Path(generation.workspace_path).resolve(strict=True)
        self.git.verify_remote(workspace, generation.remote_url)
        status = self.git.status(workspace)
        if status.branch != generation.bootstrap_branch or status.head != generation.commit_hash or status.changes:
            raise ConflictError("el workspace no coincide con el commit validado")
        if generation.bootstrap_branch != config.bootstrap_branch:
            raise ConflictError("la rama local no coincide con la configuración aprobada")
        if not generation.base_commit:
            raise ConflictError("la generación no registra commit base")
        if self.git.parent(workspace, generation.commit_hash) != generation.base_commit:
            raise ConflictError("el commit generado no desciende directamente de la base registrada")
        self._verify_remote_snapshot(config)
        now = _now()
        publication_id = str(uuid4())
        self.repository.insert(
            "publication_workflows",
            {
                "id": publication_id,
                "project_id": project.id,
                "bootstrap_workflow_id": generation.bootstrap_workflow_id,
                "git_configuration_id": config.id,
                "generation_id": generation.id,
                "repository_id": generation.repository_id,
                "status": PublicationStatus.READY.value,
                "base_branch": config.main_branch,
                "base_sha": generation.base_commit,
                "published_branch": config.bootstrap_branch,
                "published_sha": generation.commit_hash,
                "pull_request_id": None,
                "pull_request_number": None,
                "pull_request_url": None,
                "merge_commit_sha": None,
                "remote_state_json": None,
                "blocked_reason": None,
                "activated_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        return self.get(publication_id)

    def next(self, publication_id: str, *, token: str) -> PublicationWorkflow:
        publication = self.get(publication_id)
        self._verify_authorization(publication)
        identity = self.github.observe_repository_identity(publication.repository_id)
        if int(identity["github_id"]) != self._config(publication).observed_github_id:
            return self._block(publication.id, "cambió la identidad estable del repositorio")
        if publication.status in {PublicationStatus.UNKNOWN, PublicationStatus.INITIALIZING_BASE,
                                  PublicationStatus.PUSHING_BRANCH, PublicationStatus.CREATING_PR}:
            publication = self.reconcile(publication.id)
            if publication.status in {PublicationStatus.UNKNOWN, PublicationStatus.BLOCKED}:
                return publication
        config = self._config(publication)
        if publication.status is PublicationStatus.READY:
            if config.base_branch_exists:
                branch = self._branch(publication.repository_id, publication.base_branch)
                if self._branch_sha(branch) != publication.base_sha:
                    return self._block(publication.id, "la rama base remota cambió")
                return self._set_status(publication.id, PublicationStatus.BASE_READY)
            return self._push(publication, "CREATE_TECHNICAL_BASE", publication.base_branch,
                              publication.base_branch, publication.base_sha, token,
                              PublicationStatus.INITIALIZING_BASE, PublicationStatus.BASE_READY)
        if publication.status is PublicationStatus.BASE_READY:
            return self._push(publication, "PUBLISH_BOOTSTRAP_BRANCH",
                              publication.published_branch, publication.published_branch,
                              publication.published_sha, token,
                              PublicationStatus.PUSHING_BRANCH, PublicationStatus.BRANCH_PUBLISHED)
        if publication.status is PublicationStatus.BRANCH_PUBLISHED:
            return self._create_pr(publication)
        if publication.status is PublicationStatus.WAITING_FOR_MERGE:
            return self.check_merge(publication.id)
        return publication

    def reconcile(self, publication_id: str) -> PublicationWorkflow:
        publication = self.get(publication_id)
        if publication.status in {PublicationStatus.INITIALIZING_BASE, PublicationStatus.UNKNOWN}:
            operation = self._latest_operation(publication.id)
            if (operation and operation["operation_type"] == "CREATE_TECHNICAL_BASE"
                    and operation["status"] != "COMPLETED"):
                branch = self.github.get_branch(publication.repository_id, publication.base_branch)
                if self._branch_sha(branch) == publication.base_sha:
                    self._complete_operation(str(operation["id"]), publication.base_sha)
                    identity = self.github.observe_repository_identity(publication.repository_id)
                    if identity.get("default_branch") != publication.base_branch:
                        return self._block(publication.id, "GitHub no confirmó la rama base como predeterminada")
                    return self._set_status(publication.id, PublicationStatus.BASE_READY)
                if branch is not None:
                    return self._block(publication.id, "rama base remota incompatible")
                return self._set_status(publication.id, PublicationStatus.READY)
        if publication.status in {PublicationStatus.PUSHING_BRANCH, PublicationStatus.UNKNOWN}:
            operation = self._latest_operation(publication.id)
            if (operation and operation["operation_type"] == "PUBLISH_BOOTSTRAP_BRANCH"
                    and operation["status"] != "COMPLETED"):
                branch = self.github.get_branch(publication.repository_id, publication.published_branch)
                if self._branch_sha(branch) == publication.published_sha:
                    self._complete_operation(str(operation["id"]), publication.published_sha)
                    return self._set_status(publication.id, PublicationStatus.BRANCH_PUBLISHED)
                if branch is not None:
                    return self._block(publication.id, "rama bootstrap remota incompatible")
                return self._set_status(publication.id, PublicationStatus.BASE_READY)
        if publication.status in {PublicationStatus.CREATING_PR, PublicationStatus.UNKNOWN}:
            return self._create_pr(publication)
        if publication.pull_request_number:
            return self.check_merge(publication.id)
        return publication

    def check_merge(self, publication_id: str) -> PublicationWorkflow:
        publication = self.get(publication_id)
        if publication.pull_request_number is None:
            raise ConflictError("la publicación no tiene Pull Request")
        pr = self.github.get_pull_request(publication.repository_id, publication.pull_request_number)
        self._validate_pr(publication, pr)
        if bool(pr.get("merged")):
            merge_sha = pr.get("merge_commit_sha")
            if not merge_sha:
                return self._block(publication.id, "GitHub indica merge sin SHA verificable")
            self.repository.update(
                "publication_workflows", publication.id,
                {"status": PublicationStatus.MERGED.value, "merge_commit_sha": str(merge_sha),
                 "remote_state_json": json.dumps(pr, sort_keys=True), "updated_at": _now()},
            )
            return self.get(publication.id)
        if str(pr.get("state")) == "closed":
            return self._block(publication.id, "Pull Request cerrado sin merge")
        return self._set_status(publication.id, PublicationStatus.WAITING_FOR_MERGE,
                                remote_state=pr)

    def activate(self, publication_id: str) -> PublicationWorkflow:
        publication = self.get(publication_id)
        if publication.status is PublicationStatus.ACTIVATED:
            project = self.core.get_project(publication.project_id)
            if project.status is not ProjectStatus.ACTIVE:
                raise ConflictError("publicación activada con proyecto inconsistente")
            return publication
        publication = self.check_merge(publication.id)
        if publication.status is not PublicationStatus.MERGED:
            raise ConflictError("el Pull Request no tiene un merge verificado")
        observation = self.github.observe_repository_state(publication.repository_id)
        config = self._config(publication)
        if int(observation["github_id"]) != config.observed_github_id:
            raise ConflictError("cambió la identidad del repositorio")
        if observation.get("default_branch") != publication.base_branch:
            raise ConflictError("la rama principal remota no coincide con la aprobada")
        now = _now()
        with self.repository.transaction():
            project = self.core.get_project(publication.project_id)
            if project.status is ProjectStatus.DESIGNING:
                self.core.transition_project(project.id, ProjectStatus.READY)
                project = self.core.get_project(project.id)
            if project.status is ProjectStatus.READY:
                self.core.transition_project(project.id, ProjectStatus.ACTIVE)
            elif project.status is not ProjectStatus.ACTIVE:
                raise ConflictError(f"el proyecto no puede activarse desde {project.status.value}")
            self.repository.update(
                "publication_workflows", publication.id,
                {"status": PublicationStatus.ACTIVATED.value, "activated_at": now,
                 "updated_at": now},
            )
        return self.get(publication.id)

    def get(self, publication_id: str) -> PublicationWorkflow:
        return self._publication(self.repository.get("publication_workflows", publication_id))

    def get_project(self, project_id: str) -> PublicationWorkflow:
        row = self.repository.find_one("publication_workflows", project_id=project_id)
        if row is None:
            raise NotFoundError("el proyecto no tiene publicación")
        return self._publication(row)

    def operations(self, publication_id: str) -> list[Mapping[str, object]]:
        self.get(publication_id)
        return self.repository.list("publication_operations", publication_id=publication_id)

    def _push(self, publication: PublicationWorkflow, operation_type: str, local_ref: str,
              remote_branch: str, expected_sha: str | None, token: str,
              in_progress: PublicationStatus, completed: PublicationStatus) -> PublicationWorkflow:
        if not expected_sha:
            return self._block(publication.id, "falta SHA autorizado para publicar")
        existing = self.github.get_branch(publication.repository_id, remote_branch)
        operation = self._operation(publication.id, operation_type, remote_branch, expected_sha)
        if existing is not None:
            if self._branch_sha(existing) == expected_sha and operation["status"] in {"PENDING", "UNKNOWN", "COMPLETED"}:
                self._complete_operation(str(operation["id"]), expected_sha)
                return self._set_status(publication.id, completed)
            return self._block(publication.id, f"rama remota incompatible: {remote_branch}")
        self._set_status(publication.id, in_progress)
        generation = GenerationService._generation(
            self.repository.get("generation_workflows", publication.generation_id)
        )
        try:
            self.git.push_branch(Path(generation.workspace_path), local_ref, remote_branch, token=token)
        except GitError as exc:
            self._unknown_operation(str(operation["id"]), "git", str(exc))
            return self._set_status(publication.id, PublicationStatus.UNKNOWN,
                                    reason="resultado del push indeterminado")
        branch = self.github.get_branch(publication.repository_id, remote_branch)
        if self._branch_sha(branch) != expected_sha:
            self._unknown_operation(str(operation["id"]), "verification", "referencia no confirmada")
            return self._set_status(publication.id, PublicationStatus.UNKNOWN,
                                    reason="push ejecutado pero referencia no confirmada")
        self._complete_operation(str(operation["id"]), expected_sha)
        if operation_type == "CREATE_TECHNICAL_BASE":
            identity = self.github.observe_repository_identity(publication.repository_id)
            if identity.get("default_branch") != publication.base_branch:
                return self._block(publication.id, "GitHub no confirmó la rama base como predeterminada")
        return self._set_status(publication.id, completed)

    def _create_pr(self, publication: PublicationWorkflow) -> PublicationWorkflow:
        if self._branch_sha(self.github.get_branch(publication.repository_id, publication.base_branch)) != publication.base_sha:
            return self._block(publication.id, "la rama base cambió antes de crear el PR")
        if self._branch_sha(self.github.get_branch(publication.repository_id, publication.published_branch)) != publication.published_sha:
            return self._block(publication.id, "la rama bootstrap cambió antes de crear el PR")
        self._set_status(publication.id, PublicationStatus.CREATING_PR)
        bootstrap = self.repository.get("bootstrap_workflows", publication.bootstrap_workflow_id)
        config = self._config(publication)
        body = (
            f"Bootstrap generado por vibecoder.\n\n"
            f"- Issue de bootstrap: #{bootstrap['issue_number']}\n"
            f"- Generación: `{publication.generation_id}`\n"
            f"- Configuración: `{config.configuration_hash}`\n"
            f"- Especificación: `{config.specification_hash}`\n"
            f"- Base: `{publication.base_branch}`\n"
            f"- Rama: `{publication.published_branch}`\n"
            f"- Commit: `{publication.published_sha}`\n\n"
            "El contenido fue generado por un agente. Revisa alcance, seguridad, "
            "documentación y código antes de fusionar. Vibecoder no realizará el merge."
        )
        try:
            pr = self.github.create_pull_request(
                publication.repository_id,
                title="Bootstrap inicial del proyecto",
                body=body,
                head=publication.published_branch,
                base=publication.base_branch,
                idempotency_key=f"bootstrap-pr:{publication.id}",
            )
        except GitHubTransientError:
            return self._set_status(publication.id, PublicationStatus.UNKNOWN,
                                    reason="resultado de creación del PR indeterminado")
        except (GitHubError, ConflictError) as exc:
            return self._block(publication.id, str(exc))
        self._validate_pr(publication, pr)
        self.repository.update(
            "publication_workflows", publication.id,
            {"status": PublicationStatus.WAITING_FOR_MERGE.value,
             "pull_request_id": int(pr["id"]), "pull_request_number": int(pr["number"]),
             "pull_request_url": str(pr["html_url"]),
             "remote_state_json": json.dumps(pr, sort_keys=True), "blocked_reason": None,
             "updated_at": _now()},
        )
        return self.get(publication.id)

    def _verify_remote_snapshot(self, config: Any) -> None:
        current = self.github.observe_repository_state(config.repository_id)
        if int(current["github_id"]) != config.observed_github_id:
            raise ConflictError("cambió la identidad del repositorio")
        if config.base_branch_exists:
            if (bool(current["is_empty"]) or current.get("default_branch") != config.main_branch
                    or current.get("head_sha") != config.observed_head_sha):
                raise ConflictError("el repositorio cambió desde la aprobación")
        elif not bool(current["is_empty"]):
            raise ConflictError("el repositorio vacío recibió commits después de la aprobación")

    def _config(self, publication: PublicationWorkflow):
        return GitConfigurationService._from_row(
            self.repository.get("git_configurations", publication.git_configuration_id)
        )

    def _verify_authorization(self, publication: PublicationWorkflow) -> None:
        config = self._config(publication)
        generation = GenerationService._generation(
            self.repository.get("generation_workflows", publication.generation_id)
        )
        if (
            config.status not in {GitConfigurationStatus.APPROVED, GitConfigurationStatus.APPLIED}
            or config.configuration_hash != generation.git_configuration_hash
            or config.specification_hash != generation.specification_hash
            or config.bootstrap_branch != publication.published_branch
            or config.main_branch != publication.base_branch
            or GitConfigurationService.hash_payload(GitConfigurationService._payload(config))
            != config.configuration_hash
        ):
            raise ConflictError("la autorización cambió después de preparar la publicación")

    def _branch(self, repository_id: str, branch: str) -> Mapping[str, Any]:
        result = self.github.get_branch(repository_id, branch)
        if result is None:
            raise ConflictError(f"rama remota inexistente: {branch}")
        return result

    @staticmethod
    def _branch_sha(branch: Mapping[str, Any] | None) -> str | None:
        commit = branch.get("commit") if branch else None
        return str(commit.get("sha")) if isinstance(commit, Mapping) and commit.get("sha") else None

    @staticmethod
    def _validate_pr(publication: PublicationWorkflow, pr: Mapping[str, Any]) -> None:
        head, base = pr.get("head"), pr.get("base")
        if not isinstance(head, Mapping) or not isinstance(base, Mapping):
            raise ConflictError("Pull Request sin referencias verificables")
        if (str(head.get("ref")) != publication.published_branch
                or str(head.get("sha")) != publication.published_sha
                or str(base.get("ref")) != publication.base_branch):
            raise ConflictError("Pull Request incompatible con la publicación")

    def _operation(self, publication_id: str, operation_type: str,
                   expected_ref: str, expected_sha: str) -> Mapping[str, object]:
        key = f"{operation_type}:{expected_ref}:{expected_sha}"
        existing = self.repository.find_one(
            "publication_operations", publication_id=publication_id,
            operation_type=operation_type, idempotency_key=key,
        )
        if existing:
            return existing
        operation_id = str(uuid4())
        self.repository.insert(
            "publication_operations",
            {"id": operation_id, "publication_id": publication_id,
             "operation_type": operation_type, "idempotency_key": key,
             "status": "PENDING", "expected_ref": expected_ref,
             "expected_sha": expected_sha, "remote_id": None,
             "error_category": None, "error_message": None,
             "started_at": _now(), "finished_at": None},
        )
        return self.repository.get("publication_operations", operation_id)

    def _latest_operation(self, publication_id: str) -> Mapping[str, object] | None:
        rows = self.repository.list("publication_operations", publication_id=publication_id)
        return rows[-1] if rows else None

    def _complete_operation(self, operation_id: str, remote_id: str) -> None:
        self.repository.update(
            "publication_operations", operation_id,
            {"status": "COMPLETED", "remote_id": remote_id, "error_category": None,
             "error_message": None, "finished_at": _now()},
        )

    def _unknown_operation(self, operation_id: str, category: str, message: str) -> None:
        self.repository.update(
            "publication_operations", operation_id,
            {"status": "UNKNOWN", "error_category": category,
             "error_message": message, "finished_at": _now()},
        )

    def _set_status(self, publication_id: str, status: PublicationStatus, *,
                    reason: str | None = None, remote_state: Mapping[str, Any] | None = None) -> PublicationWorkflow:
        values: dict[str, object] = {"status": status.value, "blocked_reason": reason,
                                    "updated_at": _now()}
        if remote_state is not None:
            values["remote_state_json"] = json.dumps(remote_state, sort_keys=True)
        self.repository.update("publication_workflows", publication_id, values)
        return self.get(publication_id)

    def _block(self, publication_id: str, reason: str) -> PublicationWorkflow:
        return self._set_status(publication_id, PublicationStatus.BLOCKED, reason=reason)

    @staticmethod
    def _publication(row: Mapping[str, object]) -> PublicationWorkflow:
        return PublicationWorkflow(
            id=str(row["id"]), project_id=str(row["project_id"]),
            bootstrap_workflow_id=str(row["bootstrap_workflow_id"]),
            git_configuration_id=str(row["git_configuration_id"]),
            generation_id=str(row["generation_id"]), repository_id=str(row["repository_id"]),
            status=PublicationStatus(str(row["status"])), base_branch=str(row["base_branch"]),
            base_sha=str(row["base_sha"]) if row["base_sha"] else None,
            published_branch=str(row["published_branch"]), published_sha=str(row["published_sha"]),
            pull_request_id=int(row["pull_request_id"]) if row["pull_request_id"] else None,
            pull_request_number=int(row["pull_request_number"]) if row["pull_request_number"] else None,
            pull_request_url=str(row["pull_request_url"]) if row["pull_request_url"] else None,
            merge_commit_sha=str(row["merge_commit_sha"]) if row["merge_commit_sha"] else None,
            remote_state_json=str(row["remote_state_json"]) if row["remote_state_json"] else None,
            blocked_reason=str(row["blocked_reason"]) if row["blocked_reason"] else None,
            activated_at=str(row["activated_at"]) if row["activated_at"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--github-token-env", default="VIBECODER_GITHUB_TOKEN")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "status", "next", "reconcile", "pr", "check-merge", "activate", "errors"):
        command = sub.add_parser(name)
        command.add_argument("identifier")
    return parser


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.github_token_env)
    if not token:
        raise SystemExit(f"falta variable {args.github_token_env}")
    with SQLiteRepository(Path(args.database).resolve()) as repository, GitHubHttpClient(
        GitHubConfig(token=token)
    ) as client:
        core = OrchestratorCore(repository)
        service = PublicationService(core, GitHubRegistryService(core, client))
        if args.command == "prepare":
            result: object = service.prepare(args.identifier)
        else:
            publication = service.get_project(args.identifier) if args.command == "status" else service.get(args.identifier)
            if args.command == "status": result = publication
            elif args.command == "next": result = service.next(publication.id, token=token)
            elif args.command == "reconcile": result = service.reconcile(publication.id)
            elif args.command in {"pr", "check-merge"}: result = service.check_merge(publication.id)
            elif args.command == "activate": result = service.activate(publication.id)
            else: result = service.operations(publication.id)
        print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result,
                         indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
