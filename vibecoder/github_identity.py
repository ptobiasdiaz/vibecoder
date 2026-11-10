"""Global GitHub identity verification and persistence."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from queue import Empty, SimpleQueue
from typing import Any, Callable, Mapping, Protocol

import httpx

from .errors import OperationError
from .secret_store import FileSecretStore, SecretStoreError
from .sqlite import SQLiteRepository


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitHubFailureCategory(StrEnum):
    TOKEN_REJECTED = "TOKEN_REJECTED"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    RATE_LIMIT = "RATE_LIMIT"
    SECONDARY_RATE_LIMIT = "SECONDARY_RATE_LIMIT"
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    GITHUB_ERROR = "GITHUB_ERROR"
    UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"


class GitHubVerificationError(OperationError):
    code = "GITHUB_ERROR"

    def __init__(self, category: GitHubFailureCategory, message: str, *,
                 status_code: int | None = None, retry_after: int | None = None,
                 accepted_permissions: tuple[str, ...] = ()):
        self.category = category
        self.status_code = status_code
        self.retry_after = retry_after
        self.accepted_permissions = accepted_permissions
        details: dict[str, object] = {"category": category.value}
        if status_code is not None:
            details["status_code"] = status_code
        if retry_after is not None:
            details["retry_after"] = retry_after
        if accepted_permissions:
            details["accepted_permissions"] = list(accepted_permissions)
        super().__init__(message, details=details)


@dataclass(frozen=True)
class GitHubVerification:
    github_user_id: int
    login: str
    checked_at: str
    diagnostic: Mapping[str, Any] = field(default_factory=dict)


class GitHubVerifier(Protocol):
    def verify(self, token: str) -> GitHubVerification: ...


def parse_accepted_permissions(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


class HttpGitHubVerifier:
    def __init__(self, *, api_url: str = "https://api.github.com", timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def verify(self, token: str) -> GitHubVerification:
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vibecoder/0.0.1"}
        try:
            with httpx.Client(base_url=self.api_url, headers=headers, timeout=self.timeout,
                              transport=self.transport) as client:
                response = client.get("/user")
        except httpx.TimeoutException as exc:
            raise GitHubVerificationError(GitHubFailureCategory.TIMEOUT,
                                          "GitHub no respondió antes del timeout") from exc
        except httpx.NetworkError as exc:
            raise GitHubVerificationError(GitHubFailureCategory.NETWORK,
                                          "no se pudo conectar con GitHub") from exc
        permissions = parse_accepted_permissions(response.headers.get("x-accepted-github-permissions"))
        retry_after = _positive_int(response.headers.get("retry-after"))
        reset_at = _positive_int(response.headers.get("x-ratelimit-reset"))
        message = _response_message(response)
        if response.status_code == 401:
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED,
                "GitHub rechazó el PAT; puede ser inválido, expirado o revocado", status_code=401)
        if response.status_code in {403, 429} and response.headers.get("x-ratelimit-remaining") == "0":
            if retry_after is None and reset_at is not None:
                retry_after = max(0, reset_at - int(time.time()))
            raise GitHubVerificationError(GitHubFailureCategory.RATE_LIMIT,
                "GitHub ha agotado el rate limit primario", status_code=response.status_code,
                retry_after=retry_after, accepted_permissions=permissions)
        if response.status_code in {403, 429} and (retry_after is not None or "secondary rate limit" in message.casefold()):
            retry_after = retry_after if retry_after is not None else 60
            raise GitHubVerificationError(GitHubFailureCategory.SECONDARY_RATE_LIMIT,
                "GitHub ha aplicado un rate limit secundario", status_code=response.status_code,
                retry_after=retry_after, accepted_permissions=permissions)
        if response.status_code == 403:
            raise GitHubVerificationError(GitHubFailureCategory.ACCESS_DENIED,
                "GitHub ha denegado el acceso", status_code=403,
                accepted_permissions=permissions)
        if response.status_code == 404:
            raise GitHubVerificationError(GitHubFailureCategory.NOT_FOUND,
                "el recurso GitHub no existe o no está incluido en el alcance del PAT",
                status_code=404, accepted_permissions=permissions)
        if response.status_code >= 500:
            raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR,
                "GitHub ha devuelto un error de servicio", status_code=response.status_code)
        if response.status_code != 200:
            raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR,
                f"GitHub ha devuelto HTTP {response.status_code}", status_code=response.status_code,
                accepted_permissions=permissions)
        try:
            payload = response.json()
            github_id, login = payload["id"], payload["login"]
            if not isinstance(github_id, int) or github_id <= 0 or not isinstance(login, str) or not login.strip():
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubVerificationError(GitHubFailureCategory.UNEXPECTED_RESPONSE,
                "GitHub devolvió una respuesta de identidad inesperada") from exc
        return GitHubVerification(github_id, login, utc_now(), {
            "api_status": 200,
            "rate_limit_remaining": response.headers.get("x-ratelimit-remaining"),
        })


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else None
        return parsed if parsed is not None and parsed >= 0 else None
    except ValueError:
        return None


def _response_message(response: httpx.Response) -> str:
    try:
        value = response.json()
        return str(value.get("message", "")) if isinstance(value, dict) else ""
    except ValueError:
        return ""


class IdentityChangeBlocked(OperationError):
    code = "IDENTITY_CHANGE_BLOCKED"


class GitHubIdentityService:
    def __init__(self, repository: SQLiteRepository, secrets: FileSecretStore,
                 verifier_factory: Callable[[], GitHubVerifier] = HttpGitHubVerifier):
        self.repository = repository
        self.secrets = secrets
        self.verifier_factory = verifier_factory
        self._recover_secrets()

    def _recover_secrets(self) -> None:
        current = self.repository.query_one("SELECT credential_ref FROM github_identity WHERE singleton=1")
        current_id = current["credential_ref"] if current else None
        rows = self.repository.query_all("SELECT id,storage_ref,kind,status FROM secret_metadata")
        now = utc_now()
        for row in rows:
            if row["kind"] == "GITHUB_PAT" and row["id"] != current_id:
                self.repository.execute("UPDATE secret_metadata SET status='RETIRED',updated_at=? WHERE id=?",
                                        (now, row["id"]))
                try:
                    self.secrets.delete(str(row["storage_ref"]))
                except OperationError:
                    pass
        referenced = {str(row["storage_ref"]) for row in self.repository.query_all(
            "SELECT storage_ref FROM secret_metadata WHERE status='ACTIVE'")}
        self.secrets.cleanup_unreferenced(referenced)

    def login(self, token: str, actor: str) -> dict[str, Any]:
        if not isinstance(token, str) or not token or token != token.strip() or any(ch.isspace() for ch in token):
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED,
                                          "formato de PAT inválido")
        try:
            verification = self.verifier_factory().verify(token)
        except GitHubVerificationError as exc:
            _audit(self.repository, actor, "github.login", "github_identity", None,
                   "FAILED", {"category": exc.category.value})
            raise
        current = self.repository.query_one("SELECT * FROM github_identity WHERE singleton=1")
        project_count = int(self.repository.query_one("SELECT count(*) AS n FROM projects")["n"])
        if current and int(current["github_user_id"]) != verification.github_user_id and project_count:
            raise IdentityChangeBlocked(
                "no se puede cambiar la identidad GitHub mientras existan proyectos registrados")
        new_storage_ref = self.secrets.write(token)
        secret_id = str(uuid.uuid4())
        old_secret = None
        now = verification.checked_at
        try:
            with self.repository.transaction():
                self.repository.execute("INSERT INTO secret_metadata VALUES(?,?,?,?,?,?)",
                    (secret_id, "GITHUB_PAT", new_storage_ref, "ACTIVE", now, now))
                if current:
                    old_secret = self.repository.query_one(
                        "SELECT id,storage_ref FROM secret_metadata WHERE id=?", (current["credential_ref"],))
                    self.repository.execute(
                        "UPDATE github_identity SET github_user_id=?,login=?,credential_ref=?,status='ONLINE',configured_at=?,checked_at=?,diagnosis_json=?,consecutive_failures=0,next_check_at=? WHERE singleton=1",
                        (verification.github_user_id, verification.login, secret_id, now, now,
                         json.dumps(dict(verification.diagnostic)), _after(now, 300)))
                else:
                    self.repository.execute(
                        "INSERT INTO github_identity VALUES(1,?,?,?,'ONLINE',?,?,?,0,?)",
                        (verification.github_user_id, verification.login, secret_id, now, now,
                         json.dumps(dict(verification.diagnostic)), _after(now, 300)))
                _audit(self.repository, actor, "github.login", "github_identity",
                       str(verification.github_user_id), "SUCCEEDED",
                       {"login": verification.login})
        except Exception:
            self.secrets.delete(new_storage_ref)
            raise
        if old_secret:
            self.repository.execute("UPDATE secret_metadata SET status='RETIRED',updated_at=? WHERE id=?",
                                    (utc_now(), old_secret["id"]))
            try:
                self.secrets.delete(str(old_secret["storage_ref"]))
            except OperationError:
                # The new reference is already durable. A retired orphan is safer
                # than reporting that the validated rotation failed.
                pass
        return self.status()

    def status(self) -> dict[str, Any]:
        row = self.repository.query_one("SELECT * FROM github_identity WHERE singleton=1")
        if not row:
            return {"configured": False, "github_user_id": None, "login": None,
                    "connectivity": "UNCONFIGURED", "configured_at": None,
                    "checked_at": None, "diagnostic": {}, "available_for_projects": False}
        return {"configured": True, "github_user_id": int(row["github_user_id"]),
                "login": row["login"], "connectivity": row["status"],
                "configured_at": row["configured_at"], "checked_at": row["checked_at"],
                "diagnostic": json.loads(row["diagnosis_json"]),
                "available_for_projects": row["status"] == "ONLINE"}

    def credential_for_internal_use(self) -> tuple[str, dict[str, Any]]:
        """Return the PAT only to daemon-owned GitHub/Git adapters.

        This method must never be exposed by the control protocol or plugin ports.
        """
        row = self.repository.query_one("SELECT * FROM github_identity WHERE singleton=1")
        if not row:
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED,
                                          "GitHub no está configurado")
        if row["status"] != "ONLINE":
            raise GitHubVerificationError(GitHubFailureCategory.ACCESS_DENIED,
                                          "la identidad GitHub no está disponible")
        secret = self.repository.query_one(
            "SELECT storage_ref FROM secret_metadata WHERE id=? AND status='ACTIVE'",
            (row["credential_ref"],))
        if not secret:
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED,
                                          "la referencia del PAT no está disponible")
        try:
            token = self.secrets.read(str(secret["storage_ref"]))
        except SecretStoreError as exc:
            raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED,
                                          "el PAT almacenado no está disponible") from exc
        return token, {"github_user_id": int(row["github_user_id"]), "login": str(row["login"])}

    def verify_current(self, actor: str = "daemon") -> dict[str, Any]:
        row = self.repository.query_one("SELECT * FROM github_identity WHERE singleton=1")
        if not row:
            return self.status()
        secret = self.repository.query_one("SELECT storage_ref FROM secret_metadata WHERE id=? AND status='ACTIVE'", (row["credential_ref"],))
        if not secret:
            self._record_failure(GitHubVerificationError(
                GitHubFailureCategory.TOKEN_REJECTED, "la referencia del PAT no está disponible"))
            return self.status()
        try:
            token = self.secrets.read(str(secret["storage_ref"]))
        except SecretStoreError:
            self._record_failure(GitHubVerificationError(
                GitHubFailureCategory.TOKEN_REJECTED, "el PAT almacenado no está disponible"))
            return self.status()
        try:
            result = self.verifier_factory().verify(token)
        except GitHubVerificationError as exc:
            self._record_failure(exc)
        else:
            if result.github_user_id != int(row["github_user_id"]):
                self._record_failure(GitHubVerificationError(
                    GitHubFailureCategory.TOKEN_REJECTED,
                    "la credencial verificada pertenece a otra identidad"))
            else:
                self.repository.execute(
                    "UPDATE github_identity SET login=?,status='ONLINE',checked_at=?,diagnosis_json=?,consecutive_failures=0,next_check_at=? WHERE singleton=1",
                    (result.login, result.checked_at, json.dumps(dict(result.diagnostic)),
                     _after(result.checked_at, 300)))
        return self.status()

    def _record_failure(self, error: GitHubVerificationError) -> None:
        row = self.repository.query_one("SELECT consecutive_failures FROM github_identity WHERE singleton=1")
        failures = int(row["consecutive_failures"]) + 1
        status = "AUTH_FAILED" if error.category is GitHubFailureCategory.TOKEN_REJECTED else "OFFLINE"
        delay = error.retry_after if error.retry_after is not None else min(900, 15 * (2 ** min(failures - 1, 6)))
        now = utc_now()
        diagnosis = {"category": error.category.value, "message": str(error)}
        diagnosis.update(error.details)
        self.repository.execute(
            "UPDATE github_identity SET status=?,checked_at=?,diagnosis_json=?,consecutive_failures=?,next_check_at=? WHERE singleton=1",
            (status, now, json.dumps(diagnosis), failures, _after(now, delay)))

    def due(self, now: str | None = None) -> bool:
        row = self.repository.query_one("SELECT next_check_at FROM github_identity WHERE singleton=1")
        return bool(row and (row["next_check_at"] is None or row["next_check_at"] <= (now or utc_now())))


def _after(iso_time: str, seconds: int) -> str:
    return (datetime.fromisoformat(iso_time) + timedelta(seconds=seconds)).isoformat()


def _audit(repository: SQLiteRepository, actor: str, operation: str, target_type: str,
           target_id: str | None, result: str, detail: Mapping[str, Any]) -> None:
    repository.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), actor, operation, target_type, target_id, result,
         json.dumps(dict(detail), ensure_ascii=False), utc_now()))


class ConnectivityMonitor:
    """Single non-blocking probe with main-thread persistence and bounded backoff."""
    def __init__(self, service: GitHubIdentityService):
        self.service = service
        self._results: SimpleQueue[tuple[GitHubVerification | None, GitHubVerificationError | None, int]] = SimpleQueue()
        self._active = False

    def cycle(self) -> None:
        self._drain()
        if self._active or not self.service.due():
            return
        row = self.service.repository.query_one("SELECT github_user_id,credential_ref FROM github_identity WHERE singleton=1")
        if not row:
            return
        secret = self.service.repository.query_one("SELECT storage_ref FROM secret_metadata WHERE id=? AND status='ACTIVE'", (row["credential_ref"],))
        if not secret:
            self.service.verify_current()
            return
        try:
            token = self.service.secrets.read(str(secret["storage_ref"]))
        except SecretStoreError:
            self.service._record_failure(GitHubVerificationError(
                GitHubFailureCategory.TOKEN_REJECTED, "el PAT almacenado no está disponible"))
            return
        expected_id = int(row["github_user_id"])
        self._active = True
        def probe() -> None:
            try:
                result = self.service.verifier_factory().verify(token)
                self._results.put((result, None, expected_id))
            except GitHubVerificationError as exc:
                self._results.put((None, exc, expected_id))
            except Exception:
                self._results.put((None, GitHubVerificationError(
                    GitHubFailureCategory.UNEXPECTED_RESPONSE,
                    "fallo inesperado durante la verificación de GitHub"), expected_id))
        threading.Thread(target=probe, name="github-connectivity", daemon=True).start()

    def _drain(self) -> None:
        try:
            result, error, expected_id = self._results.get_nowait()
        except Empty:
            return
        self._active = False
        current = self.service.repository.query_one("SELECT github_user_id FROM github_identity WHERE singleton=1")
        if not current or int(current["github_user_id"]) != expected_id:
            return
        if error:
            self.service._record_failure(error)
        elif result and result.github_user_id == expected_id:
            self.service.repository.execute(
                "UPDATE github_identity SET login=?,status='ONLINE',checked_at=?,diagnosis_json=?,consecutive_failures=0,next_check_at=? WHERE singleton=1",
                (result.login, result.checked_at, json.dumps(dict(result.diagnostic)), _after(result.checked_at, 300)))
        else:
            self.service._record_failure(GitHubVerificationError(
                GitHubFailureCategory.TOKEN_REJECTED, "la credencial pertenece a otra identidad"))
