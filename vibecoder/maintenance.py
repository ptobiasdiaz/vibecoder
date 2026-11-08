"""Backups, restauración y seguridad de upgrades para M05.4."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .admin import AdministrationService, AtomicJsonFile
from .errors import ConflictError, ValidationError
from .sqlite import SQLiteRepository
from .supervisor import SupervisorStore


BACKUP_FORMAT_VERSION = 1
RUNTIME_VERSION = "0.4.0"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MaintenanceService:
    """Capa reutilizable; no ejecuta GitHub, Codex ni comandos remotos."""

    def __init__(self, administration: AdministrationService, *, backup_root: Path | None = None,
                 failure_hook: Callable[[str], None] | None = None):
        self.admin = administration
        self.config = administration.config
        self.backup_root = (backup_root or self.config.database.parent / "backups").resolve()
        self.recovery_root = (self.config.database.parent / "recovery").resolve()
        self.journal_path = self.recovery_root / "restore-journal.json"
        self.failure_hook = failure_hook

    def _require_admin(self) -> None:
        self.admin.require_admin()

    def _record(self, operation: str, status: str, reference: str | None,
                details: Mapping[str, Any] | None = None) -> None:
        with self.admin._repo() as repo:
            repo.insert("maintenance_records", {"id": str(uuid4()),
                "operation_type": operation, "status": status, "reference": reference,
                "details_json": json.dumps(dict(details or {}), sort_keys=True),
                "started_at": _now(), "finished_at": _now()})

    def _schema_version(self, database: Path) -> int:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])
        finally:
            connection.close()

    def _supported_schema(self) -> int:
        migrations = Path(__file__).with_name("migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")
        return max(int(path.name[:4]) for path in migrations)

    def quiescence(self, *, require_pause: bool = True, offline_confirmed: bool = False) -> dict[str, Any]:
        if not offline_confirmed:
            raise ConflictError("confirme explícitamente que el supervisor está detenido")
        if not self.config.database.exists():
            raise ConflictError("base de datos inexistente")
        connection = sqlite3.connect(f"file:{self.config.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            running = int(connection.execute(
                "SELECT COUNT(*) FROM supervisor_jobs WHERE state='RUNNING'"
            ).fetchone()[0])
            leases = int(connection.execute(
                "SELECT COUNT(*) FROM supervisor_jobs WHERE lease_token IS NOT NULL"
            ).fetchone()[0])
            pause = connection.execute(
                "SELECT pause_all FROM supervisor_controls WHERE scope_type='GLOBAL' AND scope_id='global'"
            ).fetchone()
            unknown = int(connection.execute(
                "SELECT COUNT(*) FROM supervisor_jobs WHERE state='UNKNOWN'"
            ).fetchone()[0])
            active_domain = 0
            for query in (
                "SELECT COUNT(*) FROM execution_attempts WHERE status='RUNNING'",
                "SELECT COUNT(*) FROM tasks WHERE status='RUNNING'",
                "SELECT COUNT(*) FROM task_execution_workflows WHERE status IN ('PREPARING','RUNNING','PUSHING','CREATING_PR')",
                "SELECT COUNT(*) FROM pull_request_corrections WHERE status IN ('RUNNING','PUSHING')",
                "SELECT COUNT(*) FROM publication_operations WHERE status='RUNNING'",
                "SELECT COUNT(*) FROM task_execution_operations WHERE status='RUNNING'",
            ):
                active_domain += int(connection.execute(query).fetchone()[0])
        finally:
            connection.close()
        paused = bool(pause and pause[0])
        if running or leases or active_domain:
            raise ConflictError("existen ejecuciones, jobs activos o leases; no es seguro continuar")
        if require_pause and not paused:
            raise ConflictError("se requiere pausa global persistente")
        return {"offline_confirmed": True, "paused": paused, "running": running,
                "leases": leases, "active_domain": active_domain,
                "unknown": unknown, "quiescent": True}

    def _components(self) -> list[tuple[str, Path, bool]]:
        accounts_json = self.config.accounts
        values: list[tuple[str, Path, bool]] = [
            ("config/supervisor.json", self.admin.config_path, True),
            ("database/vibecoder.db", self.config.database, True),
            ("data/secrets", self.admin.secrets.root, True),
            ("data/accounts", self.config.database.parent / "accounts", True),
            ("data/workspaces", self.config.task_workspace_root, True),
            ("data/state", self.config.database.parent / "state", False),
        ]
        if accounts_json is not None:
            values.insert(1, ("config/accounts.json", accounts_json, True))
        return values

    def _safe_tree(self, source: Path) -> None:
        if source.is_symlink():
            raise ValidationError(f"componente enlazado no permitido: {source.name}")
        if source.is_dir():
            for root, directories, files in os.walk(source, followlinks=False):
                for name in [*directories, *files]:
                    path = Path(root) / name
                    if path.is_symlink():
                        raise ValidationError(f"enlace no permitido en backup: {path.relative_to(source)}")
                    if not path.is_dir() and not path.is_file():
                        raise ValidationError("tipo de archivo no soportado en backup")

    def _copy_component(self, source: Path, target: Path) -> None:
        self._safe_tree(source)
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _source_size(self, source: Path) -> int:
        """Return the real payload size while applying the same safety policy."""
        self._safe_tree(source)
        if source.is_file():
            return source.stat().st_size
        return sum(path.stat().st_size for path in source.rglob("*") if path.is_file())

    def _database_backup(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.config.database)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close(); source.close()
        os.chmod(target, 0o600)

    def _file_manifest(self, payload: Path) -> list[dict[str, Any]]:
        entries = []
        for path in sorted(payload.rglob("*")):
            if path.is_symlink(): raise ValidationError("el backup contiene un enlace")
            if path.is_file():
                if path.stat().st_nlink != 1:
                    raise ValidationError("el backup contiene hardlinks")
                entries.append({"path": path.relative_to(payload).as_posix(),
                                "size": path.stat().st_size, "sha256": _sha256(path),
                                "mode": stat.S_IMODE(path.stat().st_mode)})
        return entries

    def create_backup(self, *, offline_confirmed: bool, backup_id: str | None = None,
                      require_pause: bool = True, purpose: str = "manual",
                      audit: bool = True) -> dict[str, Any]:
        self._require_admin()
        quiescence = self.quiescence(require_pause=require_pause, offline_confirmed=offline_confirmed)
        backup_id = backup_id or f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        if not backup_id.startswith("backup-") or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in backup_id):
            raise ValidationError("identificador de backup inválido")
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(self.backup_root, 0o700)
        final = self.backup_root / backup_id
        if final.exists(): raise ConflictError("el backup ya existe")
        staging = self.backup_root / f".{backup_id}.{uuid4().hex}.tmp"
        staging.mkdir(mode=0o700); payload = staging / "payload"; payload.mkdir(mode=0o700)
        if audit: self.admin._audit("BACKUP_START", "BACKUP", backup_id, detail=f"purpose={purpose}")
        try:
            total = sum(self._source_size(path) for _, path, _ in self._components()
                        if path.exists())
            if shutil.disk_usage(self.backup_root).free < max(total * 2, 1024 * 1024):
                raise ConflictError("espacio insuficiente para backup")
            components = []
            for relative, source, required in self._components():
                if not source.exists():
                    if required: raise ConflictError(f"componente obligatorio ausente: {relative}")
                    continue
                target = payload / relative
                if relative == "database/vibecoder.db": self._database_backup(target)
                else: self._copy_component(source, target)
                components.append(relative)
                if self.failure_hook: self.failure_hook(relative)
            manifest = {"format_version": BACKUP_FORMAT_VERSION, "backup_id": backup_id,
                        "created_at": _now(), "runtime_version": RUNTIME_VERSION,
                        "schema_version": self._schema_version(payload / "database/vibecoder.db"),
                        "purpose": purpose, "consistent": True, "includes_secrets": True,
                        "quiescence": quiescence, "components": components,
                        "files": self._file_manifest(payload)}
            manifest_path = staging / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(manifest_path, 0o600)
            self.verify(staging, allow_staging=True)
            os.replace(staging, final)
            if audit:
                self._record("BACKUP", "COMPLETED", backup_id,
                             {"purpose": purpose, "files": len(manifest["files"])})
                self.admin._audit("BACKUP_FINISH", "BACKUP", backup_id,
                                  detail=f"files={len(manifest['files'])}")
            return manifest
        except Exception as exc:
            if staging.exists(): shutil.rmtree(staging)
            if audit: self.admin._audit("BACKUP_FAIL", "BACKUP", backup_id, "FAILED", type(exc).__name__)
            raise

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_root.exists(): return []
        values = []
        for path in sorted(self.backup_root.iterdir()):
            if path.is_dir() and not path.name.startswith(".") and (path / "manifest.json").is_file():
                values.append(json.loads((path / "manifest.json").read_text(encoding="utf-8")))
        return values

    def _resolve_backup(self, reference: str | Path) -> Path:
        path = Path(reference)
        if not path.is_absolute(): path = self.backup_root / path
        path = path.resolve(strict=True)
        if path.parent != self.backup_root or path.is_symlink():
            raise ValidationError("backup fuera del almacén autorizado")
        return path

    def inspect(self, reference: str | Path) -> dict[str, Any]:
        path = self._resolve_backup(reference)
        return json.loads((path / "manifest.json").read_text(encoding="utf-8"))

    def verify(self, reference: str | Path, *, allow_staging: bool = False) -> dict[str, Any]:
        path = Path(reference).resolve(strict=True) if allow_staging else self._resolve_backup(reference)
        manifest_path = path / "manifest.json"; payload = path / "payload"
        if manifest_path.is_symlink() or not manifest_path.is_file() or not payload.is_dir():
            raise ValidationError("backup incompleto")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != BACKUP_FORMAT_VERSION or not manifest.get("consistent"):
            raise ValidationError("formato de backup incompatible o inconsistente")
        expected = {str(item["path"]): item for item in manifest.get("files", [])}
        actual = {item["path"]: item for item in self._file_manifest(payload)}
        if set(expected) != set(actual): raise ValidationError("lista de archivos no coincide")
        for name, item in expected.items():
            if (item["sha256"] != actual[name]["sha256"]
                    or int(item["size"]) != actual[name]["size"]
                    or int(item["mode"]) != actual[name]["mode"]):
                raise ValidationError(f"checksum inválido: {name}")
        database = payload / "database/vibecoder.db"
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally: connection.close()
        if integrity != "ok" or foreign: raise ValidationError("SQLite del backup no es íntegro")
        schema = self._schema_version(database); supported = self._supported_schema()
        if schema > supported: raise ConflictError("backup requiere un esquema más reciente")
        return {"valid": True, "backup_id": manifest["backup_id"],
                "schema_version": schema, "restorable": schema <= supported,
                "files": len(actual), "includes_secrets": bool(manifest.get("includes_secrets"))}

    def _journal(self, values: Mapping[str, Any]) -> None:
        self.recovery_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        AtomicJsonFile(self.journal_path).update(lambda _raw: dict(values))
        os.chmod(self.journal_path, 0o600)

    def maintenance_status(self) -> dict[str, Any]:
        journal = json.loads(self.journal_path.read_text(encoding="utf-8")) if self.journal_path.exists() else None
        return {"restore_journal": journal, "barrier": bool(journal and journal.get("phase") != "COMPLETE"),
                "backups": len(self.list_backups())}

    def _replace_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid4().hex}.restore")
        shutil.copy2(source, temp); os.replace(temp, target)

    def _replace_directory(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{target.name}.{uuid4().hex}.restore"
        shutil.copytree(source, temp, copy_function=shutil.copy2)
        old = target.parent / f".{target.name}.{uuid4().hex}.old"
        if target.exists(): os.replace(target, old)
        os.replace(temp, target)
        if old.exists(): shutil.rmtree(old)

    def _apply(self, backup: Path, *, restore_id: str) -> None:
        payload = backup / "payload"
        mappings: list[tuple[Path, Path]] = [
            (payload / "config/supervisor.json", self.admin.config_path),
            (payload / "database/vibecoder.db", self.config.database),
            (payload / "data/secrets", self.admin.secrets.root),
            (payload / "data/accounts", self.config.database.parent / "accounts"),
            (payload / "data/workspaces", self.config.task_workspace_root),
            (payload / "data/state", self.config.database.parent / "state"),
        ]
        if self.config.accounts is not None:
            mappings.insert(1, (payload / "config/accounts.json", self.config.accounts))
        for source, target in mappings:
            if not source.exists(): continue
            if source.is_dir(): self._replace_directory(source, target)
            else: self._replace_file(source, target)
            self._journal({"restore_id": restore_id, "phase": "SWAPPING",
                           "backup": backup.name, "last_component": source.relative_to(payload).as_posix(),
                           "updated_at": _now()})
            if self.failure_hook: self.failure_hook(source.relative_to(payload).as_posix())

    def restore(self, reference: str | Path, *, offline_confirmed: bool,
                confirmed: bool) -> dict[str, Any]:
        self._require_admin()
        if not confirmed: raise ConflictError("la restauración requiere confirmación explícita")
        if self.journal_path.exists():
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if journal.get("phase") not in {"COMPLETE", "ROLLED_BACK"}:
                raise ConflictError("existe una restauración interrumpida; use maintenance reconcile")
        self.quiescence(require_pause=True, offline_confirmed=offline_confirmed)
        backup = self._resolve_backup(reference); self.verify(backup)
        previous = self.create_backup(offline_confirmed=True, require_pause=True,
                                      purpose="pre-restore")
        restore_id = f"restore-{uuid4().hex[:12]}"
        self._journal({"restore_id": restore_id, "phase": "PREPARED", "backup": backup.name,
                       "previous_backup": previous["backup_id"], "created_at": _now()})
        self.admin._audit("RESTORE_START", "BACKUP", backup.name, detail=restore_id)
        try:
            self._apply(backup, restore_id=restore_id)
            # Migrate only the staged/restored local database; no external effects.
            with SQLiteRepository(self.config.database) as repo:
                SupervisorStore(repo).set_pause("GLOBAL", "global", all_work=True,
                    inference=True, publication=True, actor="maintenance",
                    reason="restauración pendiente de reconciliación")
            config_file = AtomicJsonFile(self.admin.config_path)
            config_file.update(lambda raw: {**raw, "operational_state": "CONFIGURED"})
            self.admin._refresh(); self.config = self.admin.config
            verification = self.admin.doctor()
            if any(item["level"] == "ERROR" for item in verification["checks"]):
                raise ConflictError("validación posterior a restauración fallida")
            result = {"restore_id": restore_id, "phase": "COMPLETE", "backup": backup.name,
                      "previous_backup": previous["backup_id"], "barrier": True,
                      "operational_state": "CONFIGURED", "completed_at": _now()}
            self._journal(result)
            self._record("RESTORE", "COMPLETED", backup.name,
                         {"restore_id": restore_id, "barrier": True})
            self.admin._audit("RESTORE_FINISH", "BACKUP", backup.name, detail=restore_id)
            return result
        except Exception as exc:
            try:
                AtomicJsonFile(self.admin.config_path).update(
                    lambda raw: {**raw, "operational_state": "CONFIGURED"})
            except Exception: pass
            self._journal({"restore_id": restore_id, "phase": "FAILED", "backup": backup.name,
                           "previous_backup": previous["backup_id"], "error": type(exc).__name__,
                           "updated_at": _now()})
            raise

    def reconcile_restore(self, *, rollback: bool, offline_confirmed: bool) -> dict[str, Any]:
        self._require_admin(); self.quiescence(require_pause=False, offline_confirmed=offline_confirmed)
        if not self.journal_path.exists(): return {"action": "none"}
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if journal.get("phase") in {"COMPLETE", "ROLLED_BACK"}: return journal
        if not rollback: raise ConflictError("solo se admite rollback conservador de una restauración interrumpida")
        previous = self._resolve_backup(str(journal["previous_backup"])); self.verify(previous)
        self._apply(previous, restore_id=str(journal["restore_id"]))
        with SQLiteRepository(self.config.database) as repo:
            SupervisorStore(repo).set_pause("GLOBAL", "global", all_work=True,
                inference=True, publication=True, actor="maintenance",
                reason="rollback de restauración pendiente de reconciliación")
        AtomicJsonFile(self.admin.config_path).update(lambda raw: {**raw, "operational_state": "CONFIGURED"})
        result = {**journal, "phase": "ROLLED_BACK", "barrier": True, "updated_at": _now()}
        self._journal(result); return result

    def prepare_upgrade(self, *, offline_confirmed: bool) -> dict[str, Any]:
        self._require_admin()
        with self.admin._repo() as repo:
            SupervisorStore(repo).set_pause("GLOBAL", "global", all_work=True,
                inference=True, publication=True, actor="maintenance", reason="upgrade preparado")
        return self.create_backup(offline_confirmed=offline_confirmed, purpose="pre-upgrade")

    def package_upgrade_backup(self, previous_version: str | None) -> dict[str, Any]:
        """Hook postinst: debhelper ha detenido la unidad; nunca hace red."""
        self._require_admin()
        if not previous_version or not self.config.database.exists(): return {"skipped": True}
        return self.create_backup(offline_confirmed=True, require_pause=False,
                                  purpose=f"package-upgrade-from-{previous_version}", audit=False)

    def finish_upgrade(self, previous_version: str | None) -> dict[str, Any]:
        self._require_admin()
        if not self.config.database.exists(): raise ConflictError("base de datos ausente tras upgrade")
        schema = self._schema_version(self.config.database)
        if schema != self._supported_schema():
            raise ConflictError("esquema no coincide con el runtime instalado")
        connection = sqlite3.connect(f"file:{self.config.database}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally: connection.close()
        if integrity != "ok" or foreign: raise ConflictError("integridad posterior al upgrade fallida")
        if previous_version:
            self._record("PACKAGE_UPGRADE", "COMPLETED", previous_version,
                         {"runtime_version": RUNTIME_VERSION, "schema_version": schema})
            self.admin._audit("PACKAGE_UPGRADE_FINISH", "PACKAGE", previous_version,
                              detail=f"schema={schema}")
        return {"previous_version": previous_version, "runtime_version": RUNTIME_VERSION,
                "schema_version": schema, "integrity": "ok"}

