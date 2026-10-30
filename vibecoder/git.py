"""Adaptador Git local seguro para workspaces aislados."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import ConflictError, ValidationError


_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitChange:
    path: str
    index_status: str
    worktree_status: str


@dataclass(frozen=True)
class GitStatus:
    branch: str | None
    head: str | None
    changes: tuple[GitChange, ...]


class GitAdapter:
    """Invoca Git sin shell y solo sobre rutas validadas."""

    def __init__(self, executable: str = "git"):
        self.executable = executable

    def clone(self, remote: str, destination: Path) -> None:
        destination = destination.resolve(strict=False)
        if destination.exists():
            raise ConflictError(f"el workspace ya existe: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(("clone", "--no-checkout", "--", remote, str(destination)))

    def verify_remote(self, workspace: Path, expected: str) -> None:
        actual = self._run(("remote", "get-url", "origin"), cwd=workspace).stdout.strip()
        if actual != expected:
            raise ConflictError("el workspace pertenece a otro remoto")

    def default_branch(self, workspace: Path) -> str | None:
        symbolic = self._run(
            ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
            cwd=workspace,
            allow=(0, 1),
        )
        if symbolic.returncode == 0:
            return symbolic.stdout.strip().removeprefix("origin/") or None
        branches = self._run(
            ("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"),
            cwd=workspace,
        ).stdout.splitlines()
        candidates = [item.removeprefix("origin/") for item in branches if item != "origin/HEAD"]
        for preferred in ("main", "master"):
            if preferred in candidates:
                return preferred
        return sorted(candidates)[0] if len(candidates) == 1 else None

    def create_branch(self, workspace: Path, branch: str, base: str | None) -> None:
        self._validate_branch(branch)
        if self.branch_exists(workspace, branch):
            current = self.current_branch(workspace)
            if current != branch:
                raise ConflictError(f"la rama local ya existe: {branch}")
            return
        if base:
            self._run(("switch", "-c", branch, f"origin/{base}"), cwd=workspace)
        else:
            self._run(("switch", "--orphan", branch), cwd=workspace)

    def create_empty_base(
        self,
        workspace: Path,
        branch: str,
        *,
        message: str = "chore: initialize repository for bootstrap review",
    ) -> str:
        """Crea de forma reproducible un commit raíz vacío exclusivamente local."""
        self._validate_branch(branch)
        if self.branch_exists(workspace, branch):
            self._run(("switch", branch), cwd=workspace)
            status = self.status(workspace)
            if status.head is None or status.changes:
                raise ConflictError("la rama base local existente no es reutilizable")
            return status.head
        self._run(("switch", "--orphan", branch), cwd=workspace)
        self._run(
            (
                "-c", "user.name=vibecoder",
                "-c", "user.email=vibecoder@localhost",
                "-c", "core.hooksPath=/dev/null",
                "commit", "--allow-empty", "--no-gpg-sign", "--no-verify", "-m", message,
            ),
            cwd=workspace,
            extra_env={
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
            },
        )
        return self._run(("rev-parse", "HEAD"), cwd=workspace).stdout.strip()

    def create_branch_from_local(self, workspace: Path, branch: str, base: str) -> None:
        self._validate_branch(branch)
        self._validate_branch(base)
        if self.branch_exists(workspace, branch):
            if self.current_branch(workspace) != branch:
                raise ConflictError(f"la rama local ya existe: {branch}")
            return
        self._run(("switch", "-c", branch, base), cwd=workspace)

    def branch_exists(self, workspace: Path, branch: str) -> bool:
        self._validate_branch(branch)
        result = self._run(
            ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
            cwd=workspace,
            allow=(0, 1),
        )
        return result.returncode == 0

    def current_branch(self, workspace: Path) -> str | None:
        result = self._run(
            ("symbolic-ref", "--quiet", "--short", "HEAD"), cwd=workspace, allow=(0, 1)
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def status(self, workspace: Path) -> GitStatus:
        raw = self._run(
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd=workspace
        ).stdout
        changes: list[GitChange] = []
        items = raw.split("\0")
        index = 0
        while index < len(items) and items[index]:
            item = items[index]
            code, path = item[:2], item[3:]
            if code[0] in {"R", "C"}:
                index += 1
                path = items[index]
            changes.append(GitChange(path, code[0], code[1]))
            index += 1
        head_result = self._run(("rev-parse", "--verify", "HEAD"), cwd=workspace, allow=(0, 128))
        return GitStatus(
            branch=self.current_branch(workspace),
            head=head_result.stdout.strip() if head_result.returncode == 0 else None,
            changes=tuple(changes),
        )

    def diff(self, workspace: Path) -> str:
        tracked = self._run(("diff", "--no-ext-diff", "--binary", "HEAD"), cwd=workspace, allow=(0, 128))
        if tracked.returncode == 128:
            tracked = self._run(("diff", "--no-ext-diff", "--binary", "--cached"), cwd=workspace)
        return tracked.stdout

    def commit_all(
        self,
        workspace: Path,
        message: str,
        *,
        author_name: str = "vibecoder",
        author_email: str = "vibecoder@localhost",
    ) -> str:
        status = self.status(workspace)
        if not status.changes:
            if status.head:
                return status.head
            raise ConflictError("no hay cambios para crear el commit inicial")
        self._run(("add", "--all", ":/"), cwd=workspace)
        self._run(
            (
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "-c", "core.hooksPath=/dev/null",
                "commit", "--no-gpg-sign", "--no-verify", "-m", message,
            ),
            cwd=workspace,
        )
        return self._run(("rev-parse", "HEAD"), cwd=workspace).stdout.strip()

    def is_ancestor(self, workspace: Path, commit: str) -> bool:
        result = self._run(
            ("merge-base", "--is-ancestor", commit, "HEAD"), cwd=workspace, allow=(0, 1)
        )
        return result.returncode == 0

    def parent(self, workspace: Path, commit: str) -> str | None:
        result = self._run(
            ("rev-parse", f"{commit}^"), cwd=workspace, allow=(0, 128)
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def tracked_file_at_head(self, workspace: Path, path: str) -> str | None:
        if not path or path.startswith(("/", "../")) or "\\" in path:
            raise ValidationError("ruta Git no válida")
        result = self._run(
            ("show", f"HEAD:{path}"), cwd=workspace, allow=(0, 128)
        )
        return result.stdout if result.returncode == 0 else None

    def push_branch(
        self,
        workspace: Path,
        local_ref: str,
        remote_branch: str,
        *,
        token: str | None,
    ) -> None:
        """Push sin force, prompts ni token en URL, argv o configuración persistente."""
        self._validate_branch(local_ref)
        self._validate_branch(remote_branch)
        status = self.status(workspace)
        if status.changes:
            raise ConflictError("el workspace tiene cambios sin confirmar")
        env: dict[str, str] = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        if token is None:
            self._run(
                (
                    "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
                    "push", "--porcelain", "--no-verify", "origin",
                    f"{local_ref}:refs/heads/{remote_branch}",
                ),
                cwd=workspace,
                extra_env=env,
            )
            return
        if not token.strip():
            raise ValidationError("token GitHub vacío")
        with tempfile.TemporaryDirectory(prefix="vibecoder-askpass-") as directory:
            script = Path(directory) / "askpass"
            script.write_text(
                "#!/bin/sh\ncase \"$1\" in *Username*) printf '%s\\n' x-access-token;; "
                "*) printf '%s\\n' \"$VIBECODER_GIT_TOKEN\";; esac\n",
                encoding="utf-8",
            )
            script.chmod(0o700)
            env.update(
                {
                    "GIT_ASKPASS": str(script),
                    "VIBECODER_GIT_TOKEN": token,
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            try:
                self._run(
                    (
                        "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
                        "push", "--porcelain", "--no-verify", "origin",
                        f"{local_ref}:refs/heads/{remote_branch}",
                    ),
                    cwd=workspace,
                    extra_env=env,
                    redactions=(token,),
                )
            finally:
                script.unlink(missing_ok=True)

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        allow: tuple[int, ...] = (0,),
        extra_env: dict[str, str] | None = None,
        redactions: Sequence[str] = (),
    ) -> subprocess.CompletedProcess[str]:
        if cwd is not None:
            cwd = cwd.resolve(strict=True)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(extra_env or {})}
        result = subprocess.run(
            (self.executable, *args),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode not in allow:
            message = result.stderr.strip() or result.stdout.strip() or "git falló"
            for secret in redactions:
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            raise GitError(message)
        return result

    @staticmethod
    def validate_branch(branch: str) -> None:
        if (
            not _BRANCH.fullmatch(branch)
            or ".." in branch
            or "//" in branch
            or branch.endswith(("/", "."))
            or "/." in branch
        ):
            raise ValidationError("nombre de rama no válido")

    _validate_branch = validate_branch


class WorkspaceManager:
    """Asigna una ruta estable y confinada a cada proyecto."""

    def __init__(self, root: Path, *, forbidden_roots: Sequence[Path] = ()):
        self.root = root.expanduser().resolve(strict=False)
        self.forbidden_roots = tuple(path.resolve(strict=False) for path in forbidden_roots)
        if self.root.is_symlink():
            raise ValidationError("la raíz de workspaces no puede ser un enlace simbólico")
        if any(self._within(self.root, forbidden) for forbidden in self.forbidden_roots):
            raise ValidationError("la raíz de workspaces no puede estar dentro de una ruta prohibida")
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, project_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", project_id):
            raise ValidationError("identificador de proyecto no válido para workspace")
        candidate = self.root / project_id
        self.assert_safe(candidate)
        return candidate

    def assert_safe(self, path: Path) -> Path:
        candidate = path.resolve(strict=False)
        if candidate.parent != self.root or not self._within(candidate, self.root):
            raise ValidationError("workspace fuera de la raíz aislada")
        if path.exists() and (path.is_symlink() or candidate != path.absolute()):
            raise ValidationError("workspace enlazado o inconsistente")
        return candidate

    def archive(self, workspace: Path) -> Path:
        workspace = self.assert_safe(workspace)
        if not workspace.exists():
            raise ValidationError("workspace inexistente")
        archive_root = self.root / ".archive"
        if archive_root.is_symlink():
            raise ValidationError("el archivo de workspaces no puede ser un enlace simbólico")
        archive_root.mkdir(exist_ok=True)
        if archive_root.resolve() != archive_root.absolute():
            raise ValidationError("el archivo de workspaces escapó de la raíz")
        target = archive_root / workspace.name
        if target.exists():
            raise ConflictError("ya existe un workspace archivado para el proyecto")
        shutil.move(str(workspace), str(target))
        return target

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
