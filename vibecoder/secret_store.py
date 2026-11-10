"""Atomic file-backed secrets owned by the daemon."""
from __future__ import annotations

import os
import re
import stat
import uuid
from pathlib import Path

from .errors import OperationError

_REFERENCE = re.compile(r"^[0-9a-f]{32}\.secret$")


class SecretStoreError(OperationError):
    code = "SECRET_STORE_ERROR"


class FileSecretStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._prepare_root()

    def _prepare_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SecretStoreError("directorio de secretos inseguro")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SecretStoreError("propietario o permisos inseguros en secretos")
        os.chmod(self.root, 0o700)

    def write(self, value: str) -> str:
        if not value or "\x00" in value:
            raise SecretStoreError("secreto vacío o inválido")
        reference = f"{uuid.uuid4().hex}.secret"
        final = self._path(reference)
        temporary = self.root / f".{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            payload = value.encode("utf-8")
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, final)
            os.chmod(final, 0o600)
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            raise
        return reference

    def read(self, reference: str) -> str:
        path = self._path(reference)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise SecretStoreError("secreto no disponible") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise SecretStoreError("archivo de secreto inseguro")
            return os.read(fd, 1024 * 1024).decode("utf-8")
        finally:
            os.close(fd)

    def delete(self, reference: str) -> None:
        path = self._path(reference)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SecretStoreError("se rechaza eliminar una ruta de secreto insegura")
        path.unlink()

    def exists(self, reference: str) -> bool:
        try:
            info = self._path(reference).lstat()
            return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        except FileNotFoundError:
            return False

    def cleanup_unreferenced(self, referenced: set[str]) -> list[str]:
        removed: list[str] = []
        for path in self.root.iterdir():
            if path.name in referenced:
                continue
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and (
                _REFERENCE.fullmatch(path.name) or (path.name.startswith(".") and path.name.endswith(".tmp"))
            ):
                path.unlink()
                removed.append(path.name)
        return removed

    def _path(self, reference: str) -> Path:
        if not _REFERENCE.fullmatch(reference):
            raise SecretStoreError("referencia de secreto inválida")
        return self.root / reference
