"""SQLite schema-v1 adapter for the incompatible vibecoder 0.0.1 line."""
from __future__ import annotations
import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any
from .errors import ConflictError, NotFoundError, ValidationError

SCHEMA_VERSION = 1
PRODUCT_LINE = "vibecoder-0.0.1"
SCHEMA_FILE = Path(__file__).with_name("migrations") / "0001_schema.sql"

class DatabaseKind(StrEnum):
    NEW = "NEW"
    CURRENT = "CURRENT"
    LEGACY_0_4 = "LEGACY_0_4"
    UNKNOWN = "UNKNOWN"
    CORRUPT = "CORRUPT"

class IncompatibleDatabaseError(ValidationError):
    def __init__(self, kind: DatabaseKind, message: str):
        self.kind = kind
        super().__init__(message)

def classify_database(path: str | Path) -> DatabaseKind:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return DatabaseKind.NEW
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            check = connection.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                return DatabaseKind.CORRUPT
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "schema_metadata" in tables:
                row = connection.execute("SELECT schema_version,product_line FROM schema_metadata WHERE singleton=1").fetchone()
                return DatabaseKind.CURRENT if row == (SCHEMA_VERSION, PRODUCT_LINE) else DatabaseKind.UNKNOWN
            markers = {"codex_accounts", "administrative_identities", "maintenance_records", "schema_migrations"}
            return DatabaseKind.LEGACY_0_4 if tables & markers else DatabaseKind.UNKNOWN
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return DatabaseKind.CORRUPT

class SQLiteRepository:
    def __init__(self, path: str | Path, *, initialize: bool = True):
        self.path = Path(path)
        kind = classify_database(self.path)
        if kind is DatabaseKind.NEW and not initialize:
            raise IncompatibleDatabaseError(kind, "base de datos no inicializada")
        if kind not in {DatabaseKind.NEW, DatabaseKind.CURRENT}:
            messages = {
                DatabaseKind.LEGACY_0_4: "base de vibecoder 0.4.0 rechazada; M06 no admite migración",
                DatabaseKind.UNKNOWN: "esquema SQLite desconocido",
                DatabaseKind.CORRUPT: "base SQLite corrupta",
            }
            raise IncompatibleDatabaseError(kind, messages[kind])
        if kind is DatabaseKind.NEW:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if kind is DatabaseKind.NEW:
            try:
                self.connection.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.connection.close()
                raise

    @property
    def schema_version(self) -> int:
        row = self.query_one("SELECT schema_version FROM schema_metadata WHERE singleton=1")
        assert row is not None
        return int(row["schema_version"])

    def close(self) -> None:
        self.connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        try:
            self.connection.execute(sql, tuple(parameters))
        except sqlite3.IntegrityError as exc:
            raise ConflictError(str(exc)) from exc

    def query_one(self, sql: str, parameters: Sequence[object] = ()) -> dict[str, Any] | None:
        row = self.connection.execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def query_all(self, sql: str, parameters: Sequence[object] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, tuple(parameters)).fetchall()]

    def remove_project_authorization(self, project_id: str, github_user_id: int) -> None:
        if not self.query_one("SELECT 1 FROM project_authorized_users WHERE project_id=? AND github_user_id=?", (project_id, github_user_id)):
            raise NotFoundError("autorización no encontrada")
        self.execute("DELETE FROM project_authorized_users WHERE project_id=? AND github_user_id=?", (project_id, github_user_id))

    def integrity_check(self) -> list[str]:
        return [str(row[0]) for row in self.connection.execute("PRAGMA integrity_check")]
