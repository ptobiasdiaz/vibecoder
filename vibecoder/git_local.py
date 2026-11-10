"""Read-only inspection of a user's Git checkout.

This module is intentionally CLI-side.  It never fetches, updates refs or writes
configuration; the daemon treats its result as an observation, not authority.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import OperationError


class LocalGitError(OperationError):
    code = "LOCAL_GIT_ERROR"


_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+\Z")


@dataclass(frozen=True)
class LocalGitObservation:
    canonical_name: str
    origin_url: str
    branch: str
    head_sha: str
    upstream: str | None
    tracking_sha: str | None
    staged: bool
    unstaged: bool
    untracked: bool
    ahead: int | None
    behind: int | None

    @property
    def dirty(self) -> bool:
        return self.staged or self.unstaged or self.untracked

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["dirty"] = self.dirty
        return value


def parse_github_remote(value: str) -> str:
    remote = value.strip()
    if not remote or any(ch in remote for ch in "\r\n\0"):
        raise LocalGitError("el remoto origin no es una URL GitHub válida")
    scp = re.fullmatch(r"git@github\.com:([^/]+)/(.+)", remote, re.IGNORECASE)
    if scp:
        owner, repository = scp.groups()
    else:
        parsed = urlsplit(remote)
        if parsed.scheme not in {"https", "ssh"} or (parsed.hostname or "").casefold() != "github.com":
            raise LocalGitError("origin debe identificar inequívocamente un repositorio de github.com")
        if parsed.username not in {None, "git"} or parsed.password or parsed.query or parsed.fragment:
            raise LocalGitError("origin contiene credenciales o componentes no admitidos")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2:
            raise LocalGitError("origin debe tener formato owner/repository")
        owner, repository = parts
    repository = repository.removesuffix(".git")
    if not _COMPONENT.fullmatch(owner) or not _COMPONENT.fullmatch(repository):
        raise LocalGitError("owner o nombre de repositorio GitHub no válido")
    return f"{owner}/{repository}"


class LocalGitInspector:
    def __init__(self, executable: str = "git"):
        self.executable = executable

    def inspect(self, path: Path) -> LocalGitObservation:
        requested = path.expanduser().resolve(strict=True)
        root = Path(self._run(requested, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
        branch = self._run(root, "symbolic-ref", "--quiet", "--short", "HEAD", allow=(0, 1)).strip()
        if not branch:
            raise LocalGitError("detached HEAD no puede registrarse; seleccione una rama local")
        head = self._run(root, "rev-parse", "--verify", "HEAD").strip()
        origin = self._run(root, "remote", "get-url", "origin").strip()
        canonical = parse_github_remote(origin)
        status = self._run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        staged = unstaged = untracked = False
        for entry in filter(None, status.split("\0")):
            code = entry[:2]
            untracked |= code == "??"
            staged |= code[0] not in {" ", "?", "!"}
            unstaged |= code[1] not in {" ", "?", "!"}
        upstream = self._run(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", allow=(0, 128)).strip() or None
        tracking_sha = None
        ahead = behind = None
        if upstream:
            tracking_sha = self._run(root, "rev-parse", "--verify", upstream, allow=(0, 128)).strip() or None
            counts = self._run(root, "rev-list", "--left-right", "--count", f"{upstream}...HEAD", allow=(0, 128)).strip().split()
            if len(counts) == 2:
                behind, ahead = map(int, counts)
        return LocalGitObservation(canonical, origin, branch, head, upstream, tracking_sha,
                                   staged, unstaged, untracked, ahead, behind)

    def _run(self, cwd: Path, *args: str, allow: tuple[int, ...] = (0,)) -> str:
        try:
            result = subprocess.run(
                (self.executable, "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", *args),
                cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False, shell=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                     "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_CONFIG_GLOBAL": "/dev/null"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalGitError("no se pudo inspeccionar el repositorio Git") from exc
        if result.returncode not in allow:
            message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "operación Git fallida"
            raise LocalGitError(message)
        return result.stdout
