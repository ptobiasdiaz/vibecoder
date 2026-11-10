"""Safe Codex executable detection and manifest-driven managed installation."""
from __future__ import annotations
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
import tarfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import httpx
from .errors import OperationError

SUPPORTED_VERSIONS = frozenset({"0.167.0"})
SUPPORTED_ARCHITECTURES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
VERSION_RE = re.compile(r"codex-cli\s+(\d+\.\d+\.\d+)\Z")


@dataclass(frozen=True)
class CodexExecutable:
    path: Path
    version: str
    architecture: str
    sha256: str
    source: str

    def public(self) -> dict[str, str]:
        return {"path": str(self.path), "version": self.version, "architecture": self.architecture,
                "sha256": self.sha256, "source": self.source}


class CodexInstallationManager:
    def __init__(self, managed_root: Path, manifest_path: Path | None = None,
                 *, transport: httpx.BaseTransport | None = None):
        self.root = managed_root.absolute(); self.manifest_path = manifest_path
        self.transport = transport

    def detect(self, explicit: Path | None = None) -> CodexExecutable:
        candidates = [explicit] if explicit else [self.root / "current" / "codex", Path("/usr/bin/codex"), Path("/usr/local/bin/codex")]
        errors = []
        for candidate in candidates:
            if candidate is None or not candidate.exists(): continue
            try: return self.verify(candidate, managed=self.root in candidate.absolute().parents)
            except OperationError as exc: errors.append(str(exc))
        raise OperationError("no se encontró un ejecutable Codex confiable y compatible",
                             details={"category": "EXECUTABLE_MISSING", "diagnostics": errors})

    def verify(self, path: Path, *, managed: bool = False) -> CodexExecutable:
        path = path.absolute()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR:
            raise OperationError("ejecutable Codex inseguro")
        if not managed and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
            raise OperationError("Codex externo debe pertenecer a root y no ser escribible por grupo/otros")
        arch = SUPPORTED_ARCHITECTURES.get(platform.machine().casefold())
        if not arch: raise OperationError("arquitectura no soportada", details={"category": "UNSUPPORTED_ARCHITECTURE"})
        result = subprocess.run((str(path), "--version"), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10, check=False, env={"PATH": "/usr/bin:/bin", "LANG": "C"}, close_fds=True)
        match = VERSION_RE.fullmatch(result.stdout.strip()) if result.returncode == 0 else None
        if not match or match.group(1) not in SUPPORTED_VERSIONS:
            raise OperationError("versión Codex incompatible", details={"category": "INCOMPATIBLE_VERSION",
                "observed": match.group(1) if match else None, "supported": sorted(SUPPORTED_VERSIONS)})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return CodexExecutable(path, match.group(1), arch, digest, "managed" if managed else "system")

    def install(self) -> CodexExecutable:
        if self.manifest_path is None or not self.manifest_path.is_file() or self.manifest_path.is_symlink():
            raise OperationError("no existe un manifiesto verificado para instalar Codex",
                                 details={"category": "MANIFEST_REQUIRED"})
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        arch = SUPPORTED_ARCHITECTURES.get(platform.machine().casefold())
        artifact = manifest.get("artifacts", {}).get(arch) if isinstance(manifest, dict) else None
        if (not isinstance(artifact, dict) or
                set(artifact) - {"url", "sha256", "version", "format", "companions"} or
                not {"url", "sha256", "version", "format"} <= set(artifact)):
            raise OperationError("manifiesto Codex no contiene un artefacto válido")
        if artifact["version"] not in SUPPORTED_VERSIONS or artifact["format"] not in {"binary", "tar.gz"}:
            raise OperationError("artefacto Codex incompatible")
        url = str(artifact["url"])
        if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"])):
            raise OperationError("URL o digest de artefacto inválido")
        self._secure_root()
        version_dir = self.root / str(artifact["version"])
        if version_dir.exists():
            return self.verify(version_dir / "codex", managed=True)
        with tempfile.TemporaryDirectory(prefix="codex-install-", dir=self.root) as temporary:
            download = Path(temporary) / "download"
            digest = hashlib.sha256()
            try:
                with httpx.Client(timeout=60, follow_redirects=True,
                                  transport=self.transport) as client, client.stream("GET", url) as response:
                    response.raise_for_status()
                    with download.open("xb") as output:
                        for chunk in response.iter_bytes(): digest.update(chunk); output.write(chunk)
                        output.flush(); os.fsync(output.fileno())
            except (httpx.HTTPError, OSError) as exc:
                raise OperationError("descarga de Codex interrumpida", details={"category": "DOWNLOAD_FAILED"}) from exc
            if digest.hexdigest() != artifact["sha256"]:
                raise OperationError("el digest del ejecutable Codex no coincide", details={"category": "DIGEST_MISMATCH"})
            target = Path(temporary) / "codex"
            if artifact["format"] == "binary": os.replace(download, target)
            else: self._extract_archive(download, target)
            target.chmod(0o755)
            staged = Path(temporary) / str(artifact["version"]); staged.mkdir(); os.replace(target, staged / "codex")
            companions = artifact.get("companions", {})
            if not isinstance(companions, dict): raise OperationError("companions Codex no válidos")
            for name, companion in companions.items():
                if name != "codex-code-mode-host" or not isinstance(companion, dict) or set(companion) != {"url", "sha256", "format"}:
                    raise OperationError("companion Codex no válido")
                companion_url, companion_digest = str(companion["url"]), str(companion["sha256"])
                if (not companion_url.startswith("https://") or
                        not re.fullmatch(r"[0-9a-f]{64}", companion_digest) or
                        companion["format"] not in {"binary", "tar.gz"}):
                    raise OperationError("artefacto companion Codex inválido")
                companion_download = Path(temporary) / (name + ".download")
                calculated = hashlib.sha256()
                try:
                    with httpx.Client(timeout=60, follow_redirects=True,
                                      transport=self.transport) as client, client.stream("GET", companion_url) as response:
                        response.raise_for_status()
                        with companion_download.open("xb") as output:
                            for chunk in response.iter_bytes(): calculated.update(chunk); output.write(chunk)
                            output.flush(); os.fsync(output.fileno())
                except (httpx.HTTPError, OSError) as exc:
                    raise OperationError("descarga de companion Codex interrumpida", details={"category": "DOWNLOAD_FAILED"}) from exc
                if calculated.hexdigest() != companion_digest:
                    raise OperationError("el digest del companion Codex no coincide", details={"category": "DIGEST_MISMATCH"})
                companion_target = Path(temporary) / name
                if companion["format"] == "binary": os.replace(companion_download, companion_target)
                else: self._extract_archive(companion_download, companion_target)
                companion_target.chmod(0o755); os.replace(companion_target, staged / name)
            os.replace(staged, version_dir)
            version_dir.chmod(0o755)
        current_tmp = self.root / ".current.tmp"
        current_tmp.unlink(missing_ok=True); current_tmp.symlink_to(version_dir.name); os.replace(current_tmp, self.root / "current")
        return self.verify(version_dir / "codex", managed=True)

    def _extract_archive(self, archive: Path, target: Path) -> None:
        try:
            with tarfile.open(archive, mode="r:gz") as bundle:
                members = bundle.getmembers()
                regular = [m for m in members if m.isfile()]
                if (len(members) != 1 or len(regular) != 1 or "/" in regular[0].name or
                        not regular[0].name.startswith("codex-") or regular[0].size > 512 * 1024 * 1024):
                    raise OperationError("archivo Codex contiene entradas inseguras")
                source = bundle.extractfile(regular[0])
                if source is None: raise OperationError("archivo Codex no contiene ejecutable")
                with target.open("xb") as output: shutil.copyfileobj(source, output, length=1024 * 1024)
        except (tarfile.TarError, OSError) as exc:
            raise OperationError("no se pudo extraer el artefacto Codex") from exc

    def _secure_root(self) -> None:
        created = False
        try: info = self.root.lstat()
        except FileNotFoundError:
            self.root.mkdir(parents=True, mode=0o700); info = self.root.lstat(); created = True
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022):
            raise OperationError("directorio gestionado de Codex inseguro")
        if created or stat.S_IMODE(info.st_mode) != 0o711:
            self.root.chmod(0o711)
