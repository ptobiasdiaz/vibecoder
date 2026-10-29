"""Implementación SQLite versionada de la persistencia de M01."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import ConflictError, NotFoundError


_COLUMNS = {
    "codex_accounts": frozenset(
        {"id", "name", "auth_type", "credential_ref", "status", "created_at", "updated_at"}
    ),
    "projects": frozenset(
        {
            "id",
            "name",
            "github_repository",
            "codex_account_id",
            "status",
            "created_at",
            "updated_at",
        }
    ),
    "agents": frozenset(
        {
            "id",
            "project_id",
            "name",
            "agent_type",
            "config_ref",
            "documentation_ref",
            "status",
            "created_at",
            "updated_at",
        }
    ),
    "agent_sessions": frozenset(
        {
            "id",
            "agent_id",
            "codex_thread_id",
            "codex_account_id",
            "persistence_state",
            "last_activity_at",
            "created_at",
            "updated_at",
        }
    ),
    "tasks": frozenset(
        {
            "id",
            "project_id",
            "agent_id",
            "task_type",
            "status",
            "external_ref",
            "idempotency_key",
            "created_at",
            "updated_at",
            "prompt",
        }
    ),
    "execution_attempts": frozenset(
        {
            "id",
            "task_id",
            "attempt_number",
            "status",
            "started_at",
            "finished_at",
            "error_code",
            "error_category",
            "error_message",
            "error_details_json",
            "session_id",
            "codex_thread_id",
            "codex_turn_id",
            "result_text",
        }
    ),
    "github_repositories": frozenset(
        {
            "id", "project_id", "github_id", "node_id", "owner_login", "name",
            "full_name", "html_url", "api_url", "default_branch", "visibility",
            "archived", "is_empty", "permissions_json", "last_synced_at",
            "created_at", "updated_at",
        }
    ),
    "github_operations": frozenset(
        {
            "id", "repository_id", "operation_type", "idempotency_key",
            "payload_hash", "status", "remote_id", "remote_url", "error_code",
            "error_message", "created_at", "updated_at",
        }
    ),
    "bootstrap_workflows": frozenset(
        {
            "id", "project_id", "repository_id", "github_issue_id",
            "github_issue_node_id", "issue_number", "issue_url",
            "principal_agent_id", "status", "approval_label", "approved_by",
            "approved_event_id", "approved_at", "frozen_specification",
            "last_polled_at", "created_at", "updated_at",
        }
    ),
    "bootstrap_comments": frozenset(
        {
            "id", "workflow_id", "github_comment_id", "github_node_id",
            "author_login", "author_type", "body", "remote_created_at", "status",
            "rejection_reason", "task_id", "response_text",
            "response_github_comment_id", "created_at", "updated_at",
        }
    ),
}


class SQLiteRepository:
    """Repositorio pequeño y explícito; el dominio no importa ``sqlite3``."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self.connection = sqlite3.connect(self.database, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row[0] for row in self.connection.execute("SELECT version FROM schema_migrations")
        }
        migration_dir = Path(__file__).with_name("migrations")
        for migration in sorted(migration_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            sql = migration.read_text(encoding="utf-8")
            escaped_time = self.connection.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            ).fetchone()[0]
            self.connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT INTO schema_migrations(version, applied_at) VALUES ({version}, '{escaped_time}');\n"
                + "COMMIT;"
            )

    def insert(self, table: str, values: dict[str, object]) -> None:
        self._validate_columns(table, values)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        try:
            self.connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(str(exc)) from exc

    def update(self, table: str, entity_id: str, values: dict[str, object]) -> None:
        self._validate_columns(table, values)
        assignments = ", ".join(f"{column} = ?" for column in values)
        try:
            cursor = self.connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*values.values(), entity_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(str(exc)) from exc
        if cursor.rowcount != 1:
            raise NotFoundError(f"{table} no encontrado: {entity_id}")

    def get(self, table: str, entity_id: str) -> dict[str, Any]:
        self._validate_table(table)
        row = self.connection.execute(
            f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{table} no encontrado: {entity_id}")
        return dict(row)

    def find_one(self, table: str, **criteria: object) -> dict[str, Any] | None:
        rows = self.list(table, limit=1, **criteria)
        return rows[0] if rows else None

    def list(
        self, table: str, *, limit: int | None = None, **criteria: object
    ) -> list[dict[str, Any]]:
        self._validate_table(table)
        self._validate_columns(table, criteria)
        where = ""
        params: list[object] = []
        if criteria:
            where = " WHERE " + " AND ".join(f"{column} = ?" for column in criteria)
            params.extend(criteria.values())
        order_column = "started_at" if table == "execution_attempts" else "created_at"
        suffix = f" ORDER BY {order_column}, id"
        if limit is not None:
            suffix += " LIMIT ?"
            params.append(limit)
        return [
            dict(row)
            for row in self.connection.execute(f"SELECT * FROM {table}{where}{suffix}", params)
        ]

    def next_attempt_number(self, task_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(attempt_number), 0) + 1 "
            "FROM execution_attempts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _validate_table(table: str) -> None:
        if table not in _COLUMNS:
            raise ValueError(f"tabla no permitida: {table}")

    @staticmethod
    def _validate_columns(table: str, values: dict[str, object]) -> None:
        SQLiteRepository._validate_table(table)
        invalid = set(values) - _COLUMNS[table]
        if invalid:
            raise ValueError(f"columnas no permitidas para {table}: {sorted(invalid)}")

