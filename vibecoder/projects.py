"""Recoverable GitHub project registration and per-project authorization."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

import httpx

from .errors import ConflictError, NotFoundError, OperationError
from .github_identity import GitHubFailureCategory, GitHubVerificationError, utc_now
from .git_local import parse_github_remote
from .resolution import ResourceResolver
from .sqlite import SQLiteRepository


_BRANCH = re.compile(r"(?!.*\.\.)(?!.*[~^:?*\[\\])[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class RemoteRepository:
    github_id: int
    node_id: str | None
    owner: str
    name: str
    canonical_name: str
    default_branch: str
    visibility: str


@dataclass(frozen=True)
class RemoteUser:
    github_id: int
    login: str


class GitHubProjectAPI(Protocol):
    def repository(self, token: str, canonical_name: str) -> RemoteRepository: ...
    def branch_sha(self, token: str, canonical_name: str, branch: str) -> str: ...
    def user(self, token: str, login: str) -> RemoteUser: ...


class HttpGitHubProjectAPI:
    def __init__(self, *, api_url: str = "https://api.github.com", timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self.api_url, self.timeout, self.transport = api_url.rstrip("/"), timeout, transport

    def repository(self, token: str, canonical_name: str) -> RemoteRepository:
        payload = self._get(token, f"/repos/{quote(canonical_name, safe='/')}")
        try:
            return RemoteRepository(int(payload["id"]), payload.get("node_id"),
                str(payload["owner"]["login"]), str(payload["name"]), str(payload["full_name"]),
                str(payload["default_branch"]), str(payload.get("visibility", "unknown")))
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubVerificationError(GitHubFailureCategory.UNEXPECTED_RESPONSE,
                                           "GitHub devolvió metadatos de repositorio inesperados") from exc

    def branch_sha(self, token: str, canonical_name: str, branch: str) -> str:
        payload = self._get(token, f"/repos/{quote(canonical_name, safe='/')}/git/ref/heads/{quote(branch, safe='')}")
        try:
            sha = str(payload["object"]["sha"])
            if not _SHA.fullmatch(sha): raise ValueError
            return sha
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubVerificationError(GitHubFailureCategory.UNEXPECTED_RESPONSE,
                                           "GitHub no devolvió un SHA de rama válido") from exc

    def user(self, token: str, login: str) -> RemoteUser:
        payload = self._get(token, f"/users/{quote(login, safe='')}")
        try:
            return RemoteUser(int(payload["id"]), str(payload["login"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubVerificationError(GitHubFailureCategory.UNEXPECTED_RESPONSE,
                                           "GitHub devolvió una identidad de usuario inesperada") from exc

    def _get(self, token: str, path: str) -> Mapping[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vibecoder/0.0.1"}
        try:
            with httpx.Client(base_url=self.api_url, headers=headers, timeout=self.timeout,
                              transport=self.transport) as client:
                response = client.get(path)
        except httpx.TimeoutException as exc:
            raise GitHubVerificationError(GitHubFailureCategory.TIMEOUT, "GitHub no respondió antes del timeout") from exc
        except httpx.NetworkError as exc:
            raise GitHubVerificationError(GitHubFailureCategory.NETWORK, "no se pudo conectar con GitHub") from exc
        accepted = tuple(x.strip() for x in response.headers.get("x-accepted-github-permissions", "").split(",") if x.strip())
        if response.status_code == 401:
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED, "GitHub rechazó el PAT", status_code=401)
        if response.status_code in {403, 429} and response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubVerificationError(GitHubFailureCategory.RATE_LIMIT, "GitHub ha agotado el rate limit", status_code=response.status_code)
        if response.status_code == 403:
            raise GitHubVerificationError(GitHubFailureCategory.ACCESS_DENIED, "GitHub ha denegado el acceso al repositorio", status_code=403, accepted_permissions=accepted)
        if response.status_code == 404:
            raise GitHubVerificationError(GitHubFailureCategory.NOT_FOUND, "el recurso no existe o está fuera del alcance del PAT", status_code=404, accepted_permissions=accepted)
        if response.status_code >= 500:
            raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR, "GitHub ha devuelto un error de servicio", status_code=response.status_code)
        if response.status_code != 200:
            raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR, f"GitHub ha devuelto HTTP {response.status_code}", status_code=response.status_code)
        try:
            value = response.json()
            if not isinstance(value, dict): raise ValueError
            return value
        except ValueError as exc:
            raise GitHubVerificationError(GitHubFailureCategory.UNEXPECTED_RESPONSE, "respuesta GitHub inesperada") from exc


class WorkspacePreparer(Protocol):
    def prepare(self, canonical_name: str, branch: str, sha: str, destination: Path, token: str) -> None: ...


class GitWorkspacePreparer:
    """Clone exact remote state using an ephemeral askpass helper."""
    def __init__(self, executable: str = "git"): self.executable = executable

    def prepare(self, canonical_name: str, branch: str, sha: str, destination: Path, token: str) -> None:
        if destination.exists():
            raise ConflictError("el destino del workspace ya existe")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="askpass-", dir=destination.parent) as helper_dir:
            helper = Path(helper_dir) / "askpass"
            helper.write_text("#!/bin/sh\ncase \"$1\" in *Username*) printf '%s\\n' x-access-token;; *) printf '%s\\n' \"$VIBECODER_GIT_TOKEN\";; esac\n", encoding="utf-8")
            helper.chmod(0o700)
            env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_ASKPASS": str(helper), "VIBECODER_GIT_TOKEN": token,
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
            remote = f"https://github.com/{canonical_name}.git"
            self._run(("clone", "--no-checkout", "--origin", "origin", "--", remote, str(destination)), destination.parent, env)
            self._run(("-C", str(destination), "-c", "core.hooksPath=/dev/null", "checkout", "--detach", sha), destination.parent, env)
            actual = self._run(("-C", str(destination), "rev-parse", "HEAD"), destination.parent, env).strip()
            if actual != sha:
                raise ConflictError("el workspace no coincide con el SHA remoto verificado")
            self._run(("-C", str(destination), "switch", "-C", branch, sha), destination.parent, env)
            self._run(("-C", str(destination), "remote", "set-url", "origin", remote), destination.parent, env)

    def _run(self, args: tuple[str, ...], cwd: Path, env: Mapping[str, str]) -> str:
        result = subprocess.run((self.executable, *args), cwd=cwd, env=dict(env), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                                check=False, shell=False)
        if result.returncode:
            raise OperationError("no se pudo preparar el workspace Git")
        return result.stdout


class ProjectService:
    def __init__(self, repository: SQLiteRepository, identity: Any, api_factory: Callable[[], GitHubProjectAPI],
                 workspace_root: Path, workspace_preparer: WorkspacePreparer | None = None):
        self.repository, self.identity, self.api_factory = repository, identity, api_factory
        self.workspace_root = workspace_root.expanduser().absolute()
        self.workspace_preparer = workspace_preparer or GitWorkspacePreparer()

    def register(self, observation: Mapping[str, Any], *, branch: str | None,
                 ignore_dirty: bool, actor: str) -> dict[str, Any]:
        local = self._validate_observation(observation)
        token, identity = self.identity.credential_for_internal_use()
        api = self.api_factory()
        remote = api.repository(token, local["canonical_name"])
        if remote.canonical_name.casefold() != local["canonical_name"].casefold():
            raise ConflictError("GitHub resolvió un repositorio distinto al origin observado")
        selected = branch or remote.default_branch
        if not _BRANCH.fullmatch(selected): raise ValueError("rama no válida")
        if selected != local["branch"]:
            if branch is None:
                raise OperationError(f"la rama local {local['branch']} no es la rama por defecto {remote.default_branch}; use --branch {local['branch']}")
            raise OperationError("--branch debe coincidir con la rama local actual")
        try:
            remote_sha = api.branch_sha(token, remote.canonical_name, selected)
        except GitHubVerificationError as exc:
            if exc.category is GitHubFailureCategory.NOT_FOUND:
                raise OperationError(
                    "el repositorio remoto todavía no contiene un commit publicado en la rama seleccionada; "
                    "cree y publique un commit inicial antes de registrar el proyecto",
                    details={"branch": selected}) from exc
            raise
        unpublished = bool(local["dirty"] or local["head_sha"] != remote_sha)
        if unpublished and not ignore_dirty:
            relation = self._relation(local, remote_sha)
            raise OperationError("el repositorio local contiene cambios o commits no publicados; use --ignore-dirty para comenzar desde el SHA remoto", details={"relation": relation})
        key = hashlib.sha256(f"{remote.github_id}:{selected}".encode()).hexdigest()
        existing = self.repository.query_one("SELECT p.id,p.base_branch FROM projects p JOIN github_repositories r ON r.id=p.repository_id WHERE r.github_id=?", (remote.github_id,))
        if existing:
            if existing["base_branch"] != selected:
                raise ConflictError("el repositorio ya está registrado con otra rama base")
            return {**self.get(str(existing["id"])), "changed": False}
        op = self.repository.query_one("SELECT * FROM project_registration_operations WHERE idempotency_key=?", (key,))
        op_id = str(op["id"]) if op else str(uuid.uuid4())
        project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"github:{remote.github_id}"))
        workspace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"project-workspace:{project_id}"))
        final = self._workspace_path(project_id)
        snapshot = {"github_id": remote.github_id, "canonical_name": remote.canonical_name,
                    "branch": selected, "sha": remote_sha, "workspace": str(final)}
        now = utc_now()
        if not op:
            self.repository.execute("INSERT INTO project_registration_operations VALUES(?,?,?,'PREPARING',?,NULL,?,?)",
                                    (op_id, None, key, json.dumps(snapshot), now, now))
        stage = self.workspace_root / ".staging" / op_id
        self._ensure_workspace_root()
        try:
            if not final.exists():
                if stage.exists(): shutil.rmtree(stage)
                self.workspace_preparer.prepare(remote.canonical_name, selected, remote_sha, stage, token)
                if not stage.exists(): raise OperationError("la preparación no creó el workspace")
                os.replace(stage, final)
            self._verify_workspace(final, remote_sha)
            repo_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"github-repository:{remote.github_id}"))
            with self.repository.transaction():
                self.repository.execute("INSERT INTO github_repositories VALUES(?,?,?,?,?,?,?,?,?)",
                    (repo_id, remote.github_id, remote.node_id, remote.owner, remote.name,
                     remote.canonical_name, remote.default_branch, now, now))
                self.repository.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, remote.name, repo_id, selected, remote_sha, "ENABLED", now, now))
                self.repository.execute("INSERT INTO project_authorized_users VALUES(?,?,?,?,?)",
                    (project_id, identity["github_user_id"], identity["login"], now, "GLOBAL_GITHUB_IDENTITY"))
                self.repository.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)",
                    (workspace_id, project_id, "PROJECT", str(final), selected, remote_sha, "READY", now, now))
                self.repository.execute("UPDATE project_registration_operations SET project_id=?,status='COMPLETED',error=NULL,updated_at=? WHERE id=?",
                                        (project_id, now, op_id))
            self._audit(actor, "project.add", project_id, "SUCCEEDED", {"repository": remote.canonical_name})
        except Exception as exc:
            self.repository.execute("UPDATE project_registration_operations SET status='FAILED',error=?,updated_at=? WHERE id=?",
                                    (type(exc).__name__, utc_now(), op_id))
            if stage.exists(): shutil.rmtree(stage)
            raise
        return {**self.get(project_id), "changed": True}

    def list(self) -> list[dict[str, Any]]:
        return [self._public(row) for row in self.repository.query_all(self._select() + " ORDER BY r.canonical_name COLLATE NOCASE")]

    def get(self, query: str) -> dict[str, Any]:
        rows = self.list()
        return ResourceResolver(id_of=lambda x: x["id"], name_of=lambda x: x["name"],
                                natural_id_of=lambda x: x["canonical_name"]).resolve(query, rows)

    def authorize(self, project: str, login: str, actor: str) -> dict[str, Any]:
        item = self.get(project); token, _ = self.identity.credential_for_internal_use()
        user = self.api_factory().user(token, self._login(login))
        existing = self.repository.query_one("SELECT 1 FROM project_authorized_users WHERE project_id=? AND github_user_id=?", (item["id"], user.github_id))
        if not existing:
            self.repository.execute("INSERT INTO project_authorized_users VALUES(?,?,?,?,?)",
                                    (item["id"], user.github_id, user.login, utc_now(), actor))
            self._audit(actor, "project.authorize", item["id"], "SUCCEEDED", {"github_user_id": user.github_id, "login": user.login})
        return {"changed": not bool(existing), "user": {"github_user_id": user.github_id, "login": user.login}}

    def deauthorize(self, project: str, login: str, actor: str) -> dict[str, Any]:
        item = self.get(project); token, _ = self.identity.credential_for_internal_use()
        user = self.api_factory().user(token, self._login(login))
        existing = self.repository.query_one("SELECT 1 FROM project_authorized_users WHERE project_id=? AND github_user_id=?", (item["id"], user.github_id))
        if not existing: return {"changed": False, "user": {"github_user_id": user.github_id, "login": user.login}}
        self.repository.remove_project_authorization(item["id"], user.github_id)
        self._audit(actor, "project.deauthorize", item["id"], "SUCCEEDED", {"github_user_id": user.github_id, "login": user.login})
        return {"changed": True, "user": {"github_user_id": user.github_id, "login": user.login}}

    def users(self, project: str) -> dict[str, Any]:
        item = self.get(project)
        users = self.repository.query_all("SELECT github_user_id,login,authorized_at,authorized_by FROM project_authorized_users WHERE project_id=? ORDER BY login COLLATE NOCASE", (item["id"],))
        return {"project": item, "users": users}

    def _validate_observation(self, value: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"canonical_name", "origin_url", "branch", "head_sha", "upstream", "tracking_sha", "staged", "unstaged", "untracked", "ahead", "behind", "dirty"}
        if set(value) != allowed: raise ValueError("observación Git incompleta o contiene campos desconocidos")
        result = dict(value)
        if not isinstance(result["canonical_name"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", result["canonical_name"]): raise ValueError("repositorio observado no válido")
        if not isinstance(result["origin_url"], str) or parse_github_remote(result["origin_url"]).casefold() != result["canonical_name"].casefold(): raise ValueError("origin y repositorio observado no coinciden")
        if not isinstance(result["branch"], str) or not _BRANCH.fullmatch(result["branch"]): raise ValueError("rama observada no válida")
        if not isinstance(result["head_sha"], str) or not _SHA.fullmatch(result["head_sha"]): raise ValueError("HEAD local no válido")
        for field in ("staged", "unstaged", "untracked", "dirty"):
            if not isinstance(result[field], bool): raise ValueError("estado Git no válido")
        if result["dirty"] != (result["staged"] or result["unstaged"] or result["untracked"]): raise ValueError("estado dirty inconsistente")
        for field in ("ahead", "behind"):
            if result[field] is not None and (not isinstance(result[field], int) or isinstance(result[field], bool) or result[field] < 0): raise ValueError("relación Git no válida")
        for field in ("upstream", "tracking_sha"):
            if result[field] is not None and not isinstance(result[field], str): raise ValueError("referencia Git no válida")
        if result["tracking_sha"] is not None and not _SHA.fullmatch(result["tracking_sha"]): raise ValueError("SHA de tracking no válido")
        return result

    def _workspace_path(self, project_id: str) -> Path:
        target = self.workspace_root / project_id
        if target.parent != self.workspace_root: raise OperationError("ruta de workspace fuera del directorio gestionado")
        return target

    def _ensure_workspace_root(self) -> None:
        try:
            info = self.workspace_root.lstat()
        except FileNotFoundError:
            self.workspace_root.mkdir(parents=True, exist_ok=False, mode=0o700)
            info = self.workspace_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OperationError("directorio de workspaces inseguro")
        staging = self.workspace_root / ".staging"
        try:
            stage_info = staging.lstat()
        except FileNotFoundError:
            staging.mkdir(mode=0o700, exist_ok=False)
            stage_info = staging.lstat()
        if stat.S_ISLNK(stage_info.st_mode) or not stat.S_ISDIR(stage_info.st_mode):
            raise OperationError("directorio de staging inseguro")

    def _verify_workspace(self, path: Path, sha: str) -> None:
        if path.is_symlink() or not path.is_dir(): raise OperationError("workspace preparado inseguro")
        git_dir = path / ".git"
        try:
            git_info = git_dir.lstat()
        except FileNotFoundError as exc:
            raise OperationError("workspace sin metadatos Git") from exc
        if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISDIR(git_info.st_mode):
            raise OperationError("metadatos Git del workspace inseguros")
        result = subprocess.run(("git", "-c", "core.hooksPath=/dev/null", "-C", str(path), "rev-parse", "HEAD"),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
        if result.returncode or result.stdout.strip() != sha: raise OperationError("SHA del workspace no verificable")
        config_path = git_dir / "config"
        config_info = config_path.lstat()
        if stat.S_ISLNK(config_info.st_mode) or not stat.S_ISREG(config_info.st_mode):
            raise OperationError("configuración Git del workspace insegura")
        config = config_path.read_text(encoding="utf-8", errors="replace")
        if "github_pat_" in config or "x-access-token@" in config: raise OperationError("se detectó una credencial persistida en el remoto Git")

    def _select(self) -> str:
        return "SELECT p.id,p.name,p.base_branch,p.remote_sha,p.status,p.created_at,p.updated_at,r.github_id,r.owner_login,r.canonical_name,r.default_branch,w.path AS workspace_path FROM projects p JOIN github_repositories r ON r.id=p.repository_id LEFT JOIN workspaces w ON w.project_id=p.id AND w.purpose='PROJECT'"

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]: return dict(row)
    @staticmethod
    def _login(login: str) -> str:
        value = login.strip()
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value): raise ValueError("login GitHub no válido")
        return value
    @staticmethod
    def _relation(local: Mapping[str, Any], remote_sha: str) -> str:
        if local["head_sha"] == remote_sha: return "DIRTY"
        if local.get("tracking_sha") == remote_sha:
            if (local.get("ahead") or 0) and (local.get("behind") or 0): return "DIVERGED"
            if (local.get("ahead") or 0): return "AHEAD"
            if (local.get("behind") or 0): return "BEHIND"
        return "REMOTE_SHA_DIFFERS"
    def _audit(self, actor: str, operation: str, target: str, result: str, detail: Mapping[str, Any]) -> None:
        self.repository.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), actor, operation, "project", target, result, json.dumps(dict(detail)), utc_now()))
