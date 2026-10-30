"""Servicio de aplicación M03.1 para registro y operaciones GitHub explícitas."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .domain import GitHubOperation, GitHubRepository
from .errors import ConflictError, ValidationError
from .github import (
    GitHubError,
    GitHubPermissionError,
    GitHubRepositoryNotFound,
    GitHubTransientError,
)
from .ports import GitHubClient, Repository
from .service import OrchestratorCore


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(f"{field} no puede estar vacío")
    return value


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _marker(operation_type: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{operation_type}:{idempotency_key}".encode()).hexdigest()
    return f"<!-- vibecoder-operation:{digest} -->"


class GitHubRegistryService:
    def __init__(self, core: OrchestratorCore, client: GitHubClient):
        self.core = core
        self.repository: Repository = core.repository
        self.client = client

    def validate_identity(self) -> Mapping[str, Any]:
        return self.client.authenticate()

    def validate_repository_access(
        self, repository_id: str, *, require_write: bool = False
    ) -> Mapping[str, bool]:
        repo = self.get_repository(repository_id)
        remote = self.client.get_repository(repo.owner_login, repo.name)
        permissions = remote.get("permissions", {})
        if not isinstance(permissions, Mapping) or not permissions.get("pull"):
            raise GitHubPermissionError("GitHub identity cannot read the registered repository")
        can_write = any(
            bool(permissions.get(name)) for name in ("push", "maintain", "admin")
        )
        if require_write and not can_write:
            raise GitHubPermissionError("GitHub identity cannot write to the registered repository")
        return {str(key): bool(value) for key, value in permissions.items()}

    def observe_repository_identity(self, repository_id: str) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        remote = self.client.get_repository(repo.owner_login, repo.name)
        return {
            "github_id": int(remote["id"]),
            "default_branch": str(remote["default_branch"])
            if remote.get("default_branch") else None,
        }

    def observe_repository_state(self, repository_id: str) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        remote = self.client.get_repository(repo.owner_login, repo.name)
        empty = self.client.is_repository_empty(repo.owner_login, repo.name)
        default_branch = str(remote["default_branch"]) if remote.get("default_branch") else None
        head_sha: str | None = None
        if not empty:
            if not default_branch:
                raise ValidationError("repositorio con commits sin rama por defecto")
            branch = self.client.get_branch(repo.owner_login, repo.name, default_branch)
            commit = branch.get("commit")
            if not isinstance(commit, Mapping) or not commit.get("sha"):
                raise ValidationError("respuesta GitHub sin commit de la rama por defecto")
            head_sha = str(commit["sha"])
        return {
            "github_id": int(remote["id"]),
            "is_empty": empty,
            "default_branch": default_branch,
            "head_sha": head_sha,
        }

    def register_repository(self, project_id: str, owner: str, name: str) -> GitHubRepository:
        self.core.get_project(project_id)
        remote = self.client.get_repository(owner, name)
        is_empty = self.client.is_repository_empty(owner, name)
        owner_data = remote.get("owner")
        if not isinstance(owner_data, Mapping) or not owner_data.get("login"):
            raise ValidationError("respuesta GitHub sin owner.login")
        github_id = int(remote["id"])
        by_project = self.repository.find_one("github_repositories", project_id=project_id)
        by_remote = self.repository.find_one("github_repositories", github_id=github_id)
        if by_remote and str(by_remote["project_id"]) != project_id:
            raise ConflictError("el repositorio GitHub ya pertenece a otro proyecto")
        if by_project and int(by_project["github_id"]) != github_id:
            raise ConflictError("el proyecto ya está asociado a otro repositorio GitHub")

        now = _now()
        values = {
            "github_id": github_id,
            "node_id": str(remote["node_id"]),
            "owner_login": str(owner_data["login"]),
            "name": str(remote["name"]),
            "full_name": str(remote["full_name"]),
            "html_url": str(remote["html_url"]),
            "api_url": str(remote["url"]),
            "default_branch": str(remote["default_branch"])
            if remote.get("default_branch")
            else None,
            "visibility": str(remote.get("visibility", "unknown")),
            "archived": int(bool(remote.get("archived", False))),
            "is_empty": int(is_empty),
            "permissions_json": json.dumps(remote.get("permissions", {}), sort_keys=True),
            "last_synced_at": now,
            "updated_at": now,
        }
        with self.repository.transaction():
            if by_project:
                repository_id = str(by_project["id"])
                self.repository.update("github_repositories", repository_id, values)
            else:
                repository_id = str(uuid4())
                self.repository.insert(
                    "github_repositories",
                    {
                        "id": repository_id,
                        "project_id": project_id,
                        **values,
                        "created_at": now,
                    },
                )
            self.repository.update(
                "projects",
                project_id,
                {"github_repository": str(remote["full_name"]), "updated_at": now},
            )
        return self.get_repository(repository_id)

    def get_repository(self, repository_id: str) -> GitHubRepository:
        row = self.repository.get("github_repositories", repository_id)
        return self._repository_from_row(row)

    def get_project_repository(self, project_id: str) -> GitHubRepository:
        self.core.get_project(project_id)
        row = self.repository.find_one("github_repositories", project_id=project_id)
        if row is None:
            from .errors import NotFoundError

            raise NotFoundError(f"repositorio GitHub no registrado para proyecto: {project_id}")
        return self._repository_from_row(row)

    def list_issues(self, repository_id: str, *, state: str = "open") -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        return self.client.list_issues(repo.owner_login, repo.name, state=state)

    def get_issue(self, repository_id: str, number: int) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        return self.client.get_issue(repo.owner_login, repo.name, number)

    def get_branch(self, repository_id: str, branch: str) -> Mapping[str, Any] | None:
        repo = self.get_repository(repository_id)
        try:
            return self.client.get_branch(repo.owner_login, repo.name, branch)
        except GitHubRepositoryNotFound:
            return None

    def list_pull_requests(
        self, repository_id: str, *, state: str = "all",
        head: str | None = None, base: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        qualified_head = f"{repo.owner_login}:{head}" if head else None
        return self.client.list_pull_requests(
            repo.owner_login, repo.name, state=state, head=qualified_head, base=base
        )

    def get_pull_request(self, repository_id: str, number: int) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        return self.client.get_pull_request(repo.owner_login, repo.name, number)

    def create_pull_request(
        self, repository_id: str, *, title: str, body: str, head: str, base: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        key = _required(idempotency_key, "idempotency_key")
        marker = _marker("CREATE_PULL_REQUEST", key)
        marked_body = f"{body.rstrip()}\n\n{marker}"
        operation = self._prepare_operation(
            repo.id, "CREATE_PULL_REQUEST", key,
            {"title": title, "body": body, "head": head, "base": base},
        )
        candidates = self.list_pull_requests(
            repository_id, state="all", head=head, base=base
        )
        compatible = [item for item in candidates if marker in str(item.get("body") or "")]
        if len(compatible) == 1:
            self._complete(operation.id, compatible[0])
            return compatible[0]
        if len(compatible) > 1 or candidates:
            raise ConflictError("existe un Pull Request incompatible para la misma rama/base")
        try:
            created = self.client.create_pull_request(
                repo.owner_login, repo.name, title, marked_body, head, base
            )
        except GitHubError as exc:
            self._fail(operation.id, exc)
            raise
        self._complete(operation.id, created)
        return created

    def list_comments(self, repository_id: str, issue_number: int) -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        return self.client.list_comments(repo.owner_login, repo.name, issue_number)

    def list_issue_events(
        self, repository_id: str, issue_number: int
    ) -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        return self.client.list_issue_events(repo.owner_login, repo.name, issue_number)

    def list_labels(self, repository_id: str) -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        return self.client.list_labels(repo.owner_login, repo.name)

    def create_issue(
        self, repository_id: str, title: str, body: str, *, idempotency_key: str
    ) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        title = _required(title, "title")
        key = _required(idempotency_key, "idempotency_key")
        marker = _marker("CREATE_ISSUE", key)
        marked_body = f"{body.rstrip()}\n\n{marker}" if body.strip() else marker
        operation = self._prepare_operation(
            repo.id, "CREATE_ISSUE", key, {"title": title, "body": body}
        )
        existing = self._find_marked(self.client.list_issues(repo.owner_login, repo.name, state="all"), marker)
        if existing:
            self._complete(operation.id, existing)
            return existing
        try:
            created = self.client.create_issue(repo.owner_login, repo.name, title, marked_body)
        except GitHubError as exc:
            self._fail(operation.id, exc)
            raise
        self._complete(operation.id, created)
        return created

    def create_comment(
        self, repository_id: str, issue_number: int, body: str, *, idempotency_key: str
    ) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        key = _required(idempotency_key, "idempotency_key")
        marker = _marker("CREATE_COMMENT", key)
        marked_body = f"{body.rstrip()}\n\n{marker}" if body.strip() else marker
        operation = self._prepare_operation(
            repo.id, "CREATE_COMMENT", key, {"issue_number": issue_number, "body": body}
        )
        existing = self._find_marked(
            self.client.list_comments(repo.owner_login, repo.name, issue_number), marker
        )
        if existing:
            self._complete(operation.id, existing)
            return existing
        try:
            created = self.client.create_comment(
                repo.owner_login, repo.name, issue_number, marked_body
            )
        except GitHubError as exc:
            self._fail(operation.id, exc)
            raise
        self._complete(operation.id, created)
        return created

    def ensure_label(
        self, repository_id: str, name: str, color: str, description: str | None = None,
        *, idempotency_key: str,
    ) -> Mapping[str, Any]:
        repo = self.get_repository(repository_id)
        name = _required(name, "name")
        key = _required(idempotency_key, "idempotency_key")
        operation = self._prepare_operation(
            repo.id, "CREATE_LABEL", key,
            {"name": name, "color": color, "description": description},
        )
        existing = self.client.get_label(repo.owner_login, repo.name, name)
        if existing:
            self._complete(operation.id, existing)
            return existing
        try:
            created = self.client.create_label(
                repo.owner_login, repo.name, name, color, description
            )
        except GitHubError as exc:
            self._fail(operation.id, exc)
            raise
        self._complete(operation.id, created)
        return created

    def assign_labels(
        self, repository_id: str, issue_number: int, labels: Sequence[str],
        *, idempotency_key: str,
    ) -> Sequence[Mapping[str, Any]]:
        repo = self.get_repository(repository_id)
        normalized = sorted({_required(label, "label") for label in labels})
        key = _required(idempotency_key, "idempotency_key")
        operation = self._prepare_operation(
            repo.id, "SET_LABELS", key,
            {"issue_number": issue_number, "labels": normalized},
        )
        issue = self.client.get_issue(repo.owner_login, repo.name, issue_number)
        current = sorted(
            str(label.get("name")) for label in issue.get("labels", [])
            if isinstance(label, Mapping) and label.get("name")
        )
        if current == normalized:
            self._complete(operation.id, issue)
            return list(issue.get("labels", []))
        try:
            assigned = self.client.set_issue_labels(
                repo.owner_login, repo.name, issue_number, normalized
            )
        except GitHubError as exc:
            self._fail(operation.id, exc)
            raise
        self._complete(operation.id, {"id": issue["id"], "html_url": issue["html_url"]})
        return assigned

    def get_operation(self, operation_id: str) -> GitHubOperation:
        row = self.repository.get("github_operations", operation_id)
        return self._operation_from_row(row)

    def _prepare_operation(
        self, repository_id: str, operation_type: str, key: str, payload: Mapping[str, Any]
    ) -> GitHubOperation:
        digest = _payload_hash(payload)
        existing = self.repository.find_one(
            "github_operations", repository_id=repository_id,
            operation_type=operation_type, idempotency_key=key,
        )
        if existing:
            operation = self._operation_from_row(existing)
            if operation.payload_hash != digest:
                raise ConflictError("idempotency_key reutilizada con parámetros distintos")
            return operation
        now = _now()
        operation_id = str(uuid4())
        self.repository.insert(
            "github_operations",
            {
                "id": operation_id, "repository_id": repository_id,
                "operation_type": operation_type, "idempotency_key": key,
                "payload_hash": digest, "status": "PENDING", "remote_id": None,
                "remote_url": None, "error_code": None, "error_message": None,
                "created_at": now, "updated_at": now,
            },
        )
        return self.get_operation(operation_id)

    def _complete(self, operation_id: str, remote: Mapping[str, Any]) -> None:
        self.repository.update(
            "github_operations", operation_id,
            {
                "status": "COMPLETED",
                "remote_id": str(remote["id"]) if remote.get("id") is not None else None,
                "remote_url": str(remote["html_url"]) if remote.get("html_url") else None,
                "error_code": None, "error_message": None, "updated_at": _now(),
            },
        )

    def _fail(self, operation_id: str, error: GitHubError) -> None:
        self.repository.update(
            "github_operations", operation_id,
            {
                "status": "UNKNOWN" if isinstance(error, GitHubTransientError) else "FAILED",
                "error_code": error.category,
                "error_message": str(error),
                "updated_at": _now(),
            },
        )

    @staticmethod
    def _find_marked(items: Sequence[Mapping[str, Any]], marker: str) -> Mapping[str, Any] | None:
        return next((item for item in items if marker in str(item.get("body") or "")), None)

    @staticmethod
    def _repository_from_row(row: Mapping[str, Any]) -> GitHubRepository:
        return GitHubRepository(
            id=str(row["id"]), project_id=str(row["project_id"]),
            github_id=int(row["github_id"]), node_id=str(row["node_id"]),
            owner_login=str(row["owner_login"]), name=str(row["name"]),
            full_name=str(row["full_name"]), html_url=str(row["html_url"]),
            api_url=str(row["api_url"]),
            default_branch=str(row["default_branch"]) if row["default_branch"] else None,
            visibility=str(row["visibility"]), archived=bool(row["archived"]),
            is_empty=bool(row["is_empty"]),
            permissions=json.loads(str(row["permissions_json"])),
            last_synced_at=str(row["last_synced_at"]), created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _operation_from_row(row: Mapping[str, Any]) -> GitHubOperation:
        return GitHubOperation(
            id=str(row["id"]), repository_id=str(row["repository_id"]),
            operation_type=str(row["operation_type"]),
            idempotency_key=str(row["idempotency_key"]), payload_hash=str(row["payload_hash"]),
            status=str(row["status"]), remote_id=str(row["remote_id"]) if row["remote_id"] else None,
            remote_url=str(row["remote_url"]) if row["remote_url"] else None,
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=str(row["error_message"]) if row["error_message"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )
