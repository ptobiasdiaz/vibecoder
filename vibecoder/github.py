"""Adaptador HTTP para GitHub REST API, sin conocimiento del dominio persistente."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError


class GitHubError(RuntimeError):
    category = "github"

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class GitHubAuthenticationError(GitHubError):
    category = "authentication"


class GitHubPermissionError(GitHubError):
    category = "permission"


class GitHubRepositoryNotFound(GitHubError):
    category = "repository_not_found"


class GitHubRateLimitError(GitHubError):
    category = "rate_limit"


class GitHubTransientError(GitHubError):
    category = "transient"


class GitHubPermanentError(GitHubError):
    category = "permanent"


class GitHubProtocolError(GitHubError):
    category = "protocol"


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(repr=False)
    api_url: str = "https://api.github.com"
    timeout_seconds: float = 15.0
    max_read_retries: int = 2
    user_agent: str = "vibecoder/0.4"

    def validate(self) -> None:
        if not self.token.strip():
            raise ValueError("GitHub token is required")
        if not self.api_url.startswith(("https://", "http://")):
            raise ValueError("GitHub api_url must be HTTP(S)")
        if self.timeout_seconds <= 0 or self.max_read_retries < 0:
            raise ValueError("invalid GitHub timeout/retry configuration")


class _RemoteModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GitHubIdentity(_RemoteModel):
    id: int
    login: str
    node_id: str
    html_url: str


class RepositoryPermissions(_RemoteModel):
    admin: bool = False
    maintain: bool = False
    push: bool = False
    triage: bool = False
    pull: bool = False


class RepositoryData(_RemoteModel):
    id: int
    node_id: str
    name: str
    full_name: str
    html_url: str
    url: str
    default_branch: str | None = None
    visibility: str = "unknown"
    archived: bool = False
    owner: Mapping[str, Any]
    permissions: RepositoryPermissions = Field(default_factory=RepositoryPermissions)


class IssueData(_RemoteModel):
    id: int
    node_id: str
    number: int
    title: str
    body: str | None = None
    state: str
    html_url: str
    labels: list[Mapping[str, Any]] = Field(default_factory=list)
    pull_request: Mapping[str, Any] | None = None


class CommentData(_RemoteModel):
    id: int
    node_id: str
    body: str
    html_url: str
    user: Mapping[str, Any]
    created_at: str
    updated_at: str


class LabelData(_RemoteModel):
    id: int
    node_id: str
    name: str
    color: str
    description: str | None = None


class IssueEventData(_RemoteModel):
    id: int
    node_id: str | None = None
    event: str
    actor: Mapping[str, Any] | None = None
    label: Mapping[str, Any] | None = None
    created_at: str


class GitHubHttpClient:
    """Cliente síncrono pequeño; las escrituras nunca se reintentan ciegamente."""

    def __init__(
        self,
        config: GitHubConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        config.validate()
        self.config = config
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=config.api_url.rstrip("/"),
            timeout=config.timeout_seconds,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {config.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": config.user_agent,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def authenticate(self) -> Mapping[str, Any]:
        return self._validated("GET", "/user", GitHubIdentity)

    def get_repository(self, owner: str, name: str) -> Mapping[str, Any]:
        return self._validated("GET", self._repo_path(owner, name), RepositoryData, repo=True)

    def is_repository_empty(self, owner: str, name: str) -> bool:
        response = self._request(
            "GET", f"{self._repo_path(owner, name)}/commits", params={"per_page": 1},
            allow_status={409}, repo=True,
        )
        if response.status_code == 409:
            return True
        data = self._json(response)
        if not isinstance(data, list):
            raise GitHubProtocolError("GitHub commits response is not a list")
        return not data

    def list_issues(self, owner: str, name: str, *, state: str = "open") -> Sequence[Mapping[str, Any]]:
        items = self._validated_list(
            "GET", f"{self._repo_path(owner, name)}/issues", IssueData,
            params={"state": state, "per_page": 100}, repo=True,
        )
        return [item for item in items if item.get("pull_request") is None]

    def get_issue(self, owner: str, name: str, number: int) -> Mapping[str, Any]:
        return self._validated("GET", f"{self._repo_path(owner, name)}/issues/{number}", IssueData, repo=True)

    def create_issue(self, owner: str, name: str, title: str, body: str) -> Mapping[str, Any]:
        return self._validated(
            "POST", f"{self._repo_path(owner, name)}/issues", IssueData,
            json={"title": title, "body": body}, repo=True,
        )

    def list_comments(self, owner: str, name: str, issue_number: int) -> Sequence[Mapping[str, Any]]:
        return self._validated_list(
            "GET", f"{self._repo_path(owner, name)}/issues/{issue_number}/comments",
            CommentData, params={"per_page": 100}, repo=True,
        )

    def create_comment(self, owner: str, name: str, issue_number: int, body: str) -> Mapping[str, Any]:
        return self._validated(
            "POST", f"{self._repo_path(owner, name)}/issues/{issue_number}/comments",
            CommentData, json={"body": body}, repo=True,
        )

    def list_issue_events(
        self, owner: str, name: str, issue_number: int
    ) -> Sequence[Mapping[str, Any]]:
        return self._validated_list(
            "GET", f"{self._repo_path(owner, name)}/issues/{issue_number}/events",
            IssueEventData, params={"per_page": 100}, repo=True,
        )

    def list_labels(self, owner: str, name: str) -> Sequence[Mapping[str, Any]]:
        return self._validated_list(
            "GET", f"{self._repo_path(owner, name)}/labels", LabelData,
            params={"per_page": 100}, repo=True,
        )

    def get_label(self, owner: str, name: str, label: str) -> Mapping[str, Any] | None:
        path = f"{self._repo_path(owner, name)}/labels/{quote(label, safe='')}"
        response = self._request("GET", path, allow_status={404}, repo=True)
        if response.status_code == 404:
            return None
        return self._parse_model(response, LabelData)

    def create_label(
        self, owner: str, name: str, label: str, color: str, description: str | None
    ) -> Mapping[str, Any]:
        return self._validated(
            "POST", f"{self._repo_path(owner, name)}/labels", LabelData,
            json={"name": label, "color": color, "description": description}, repo=True,
        )

    def set_issue_labels(
        self, owner: str, name: str, issue_number: int, labels: Sequence[str]
    ) -> Sequence[Mapping[str, Any]]:
        return self._validated_list(
            "POST", f"{self._repo_path(owner, name)}/issues/{issue_number}/labels",
            LabelData, json={"labels": list(labels)}, repo=True,
        )

    @staticmethod
    def _repo_path(owner: str, name: str) -> str:
        if not owner.strip() or not name.strip() or "/" in owner or "/" in name:
            raise ValueError("invalid GitHub owner/repository")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def _validated(self, method: str, path: str, model: type[_RemoteModel], **kwargs: Any) -> Mapping[str, Any]:
        return self._parse_model(self._request(method, path, **kwargs), model)

    def _validated_list(self, method: str, path: str, model: type[_RemoteModel], **kwargs: Any) -> Sequence[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        next_path: str | None = path
        next_kwargs = kwargs
        while next_path is not None:
            response = self._request(method, next_path, **next_kwargs)
            data = self._json(response)
            if not isinstance(data, list):
                raise GitHubProtocolError("GitHub response is not a list")
            try:
                items.extend(model.model_validate(item).model_dump() for item in data)
            except PydanticValidationError as exc:
                raise GitHubProtocolError("incomplete or unexpected GitHub response") from exc
            next_link = response.links.get("next")
            next_path = str(next_link["url"]) if next_link else None
            next_kwargs = {}
        return items

    def _parse_model(self, response: httpx.Response, model: type[_RemoteModel]) -> Mapping[str, Any]:
        try:
            return model.model_validate(self._json(response)).model_dump()
        except PydanticValidationError as exc:
            raise GitHubProtocolError("incomplete or unexpected GitHub response") from exc

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubProtocolError("GitHub response is not valid JSON") from exc

    def _request(
        self, method: str, path: str, *, allow_status: set[int] | None = None,
        repo: bool = False, **kwargs: Any,
    ) -> httpx.Response:
        retries = self.config.max_read_retries if method == "GET" else 0
        for attempt in range(retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < retries:
                    self._sleep(0.1 * (2**attempt))
                    continue
                raise GitHubTransientError("GitHub transport failed") from exc
            if allow_status and response.status_code in allow_status:
                return response
            if response.status_code in {502, 503, 504} and attempt < retries:
                self._sleep(0.1 * (2**attempt))
                continue
            self._raise_for_status(response, repo=repo)
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, repo: bool) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 401:
            raise GitHubAuthenticationError("GitHub authentication rejected", status_code=status)
        if status == 429 or (
            status == 403 and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            raise GitHubRateLimitError("GitHub rate limit reached", status_code=status)
        if status == 403:
            raise GitHubPermissionError("GitHub permission denied", status_code=status)
        if status == 404 and repo:
            raise GitHubRepositoryNotFound("GitHub repository or resource is inaccessible", status_code=status)
        if status in {500, 502, 503, 504}:
            raise GitHubTransientError("GitHub service failed", status_code=status)
        raise GitHubPermanentError(f"GitHub request failed with status {status}", status_code=status)
