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
    "generation_workflows": frozenset(
        {
            "id", "project_id", "bootstrap_workflow_id", "repository_id",
            "approved_by", "approved_event_id", "specification_hash",
            "workspace_path", "remote_url", "base_branch", "bootstrap_branch",
            "base_commit", "git_configuration_id", "git_configuration_hash",
            "task_id", "session_id", "status", "instruction_versions_json",
            "generated_instruction_versions_json", "validation_result_json",
            "commit_hash", "blocked_reason", "created_at", "updated_at",
        }
    ),
    "git_configurations": frozenset(
        {
            "id", "workflow_id", "repository_id", "revision", "status",
            "schema_version", "config_json", "configuration_hash",
            "source_comment_id", "observed_github_id", "observed_is_empty",
            "observed_default_branch", "observed_head_sha", "specification_hash",
            "approved_by", "approved_event_id", "applied_at", "created_at",
            "updated_at",
        }
    ),
    "publication_workflows": frozenset(
        {
            "id", "project_id", "bootstrap_workflow_id", "git_configuration_id",
            "generation_id", "repository_id", "status", "base_branch", "base_sha",
            "published_branch", "published_sha", "pull_request_id",
            "pull_request_number", "pull_request_url", "merge_commit_sha",
            "remote_state_json", "blocked_reason", "activated_at", "created_at",
            "updated_at",
        }
    ),
    "publication_operations": frozenset(
        {
            "id", "publication_id", "operation_type", "idempotency_key", "status",
            "expected_ref", "expected_sha", "remote_id", "error_category",
            "error_message", "started_at", "finished_at",
        }
    ),
    "specialist_agents": frozenset(
        {
            "agent_id", "project_id", "logical_id", "display_name", "status",
            "assigned_account_id", "active_revision_id", "created_by", "created_at",
            "updated_at",
        }
    ),
    "agent_configuration_revisions": frozenset(
        {
            "id", "agent_id", "version", "status", "role",
            "responsibilities_json", "capabilities_json", "restrictions_json",
            "instructions_path", "instructions_hash", "configuration_hash",
            "source_commit", "created_by", "authorized_by", "created_at", "updated_at",
        }
    ),
    "agent_audit_events": frozenset(
        {
            "id", "agent_id", "project_id", "operation", "actor",
            "previous_status", "new_status", "previous_version", "new_version",
            "result", "detail", "created_at",
        }
    ),
    "issue_workflows": frozenset(
        {
            "id", "project_id", "repository_id", "github_issue_id",
            "github_issue_node_id", "issue_number", "issue_url", "title", "body",
            "author_login", "github_state", "labels_json", "principal_agent_id",
            "status", "approval_label", "active_plan_revision_id", "last_polled_at",
            "blocked_reason", "created_at", "updated_at",
        }
    ),
    "issue_comments": frozenset(
        {
            "id", "workflow_id", "github_comment_id", "github_node_id",
            "author_login", "author_type", "body", "remote_created_at",
            "recipient_agent_id", "status", "rejection_reason", "task_id",
            "session_id", "response_text", "response_github_comment_id",
            "created_at", "updated_at",
        }
    ),
    "issue_agent_sessions": frozenset(
        {"id", "workflow_id", "agent_id", "session_id", "account_id",
         "workspace_path", "instruction_versions_json", "created_at", "updated_at"}
    ),
    "plan_revisions": frozenset(
        {
            "id", "workflow_id", "revision", "status", "schema_version",
            "plan_json", "plan_hash", "proposed_by_agent_id",
            "published_comment_id", "published_at", "approved_by",
            "approved_event_id", "approved_at", "specialist_snapshot_json",
            "frozen_context_json", "blocked_reason", "created_at", "updated_at",
        }
    ),
    "planned_tasks": frozenset(
        {
            "id", "plan_revision_id", "logical_id", "description",
            "expected_result", "assigned_agent_id", "acceptance_criteria_json",
            "restrictions_json", "materialized_task_id", "created_at",
        }
    ),
    "planned_task_dependencies": frozenset(
        {"plan_revision_id", "task_id", "depends_on_task_id"}
    ),
    "plan_approval_events": frozenset(
        {"id", "plan_revision_id", "github_event_id", "actor_login",
         "remote_created_at", "created_at"}
    ),
    "task_execution_workflows": frozenset(
        {
            "id", "project_id", "issue_workflow_id", "plan_revision_id",
            "planned_task_id", "task_id", "repository_id", "agent_id",
            "agent_revision_id", "agent_configuration_hash", "instructions_hash",
            "account_id", "status", "workspace_path", "remote_url", "base_branch",
            "base_sha", "work_branch", "session_id", "instruction_versions_json",
            "authorization_snapshot_json", "context_hash", "allowed_paths_json",
            "validation_commands_json", "validation_authorized_by", "result_text",
            "validation_result_json", "commit_sha", "published_sha",
            "pull_request_id", "pull_request_number", "pull_request_url",
            "merge_commit_sha", "remote_state_json", "blocked_reason",
            "created_at", "updated_at",
        }
    ),
    "task_execution_operations": frozenset(
        {"id", "execution_id", "operation_type", "idempotency_key", "status",
         "expected_ref", "expected_sha", "remote_id", "error_category",
         "error_message", "started_at", "finished_at"}
    ),
    "pull_request_supervisions": frozenset(
        {"id", "execution_id", "project_id", "repository_id",
         "pull_request_number", "pull_request_id", "status", "review_state",
         "observed_head_sha", "observed_base_sha", "merge_commit_sha",
         "blocked_reason", "last_polled_at", "created_at", "updated_at"}
    ),
    "pull_request_events": frozenset(
        {"id", "supervision_id", "remote_kind", "remote_id", "remote_node_id",
         "author_id", "author_login", "author_type", "body", "review_state",
         "commit_sha", "path", "remote_created_at", "remote_updated_at",
         "authorized", "recipient_agent_id", "status", "rejection_reason",
         "task_id", "session_id", "response_text", "response_comment_id",
         "created_at", "updated_at"}
    ),
    "pull_request_agent_sessions": frozenset(
        {"id", "supervision_id", "agent_id", "session_id", "account_id",
         "instruction_versions_json", "created_at", "updated_at"}
    ),
    "pull_request_corrections": frozenset(
        {"id", "supervision_id", "execution_id", "source_event_ids_json",
         "description", "scope", "authorized_by", "authorized_at", "status",
         "previous_sha", "new_sha", "task_id", "session_id", "result_text",
         "validation_result_json", "commit_sha", "response_text",
         "response_comment_id", "blocked_reason", "created_at", "updated_at"}
    ),
    "supervisor_jobs": frozenset(
        {"id", "project_id", "job_type", "category", "source_type", "source_id",
         "state", "priority", "eligible_at", "attempts", "max_attempts",
         "operation_key", "payload_json", "last_error_category",
         "last_error_message", "next_action", "runner_id", "lease_token",
         "lease_acquired_at", "lease_heartbeat_at", "lease_expires_at",
         "started_at", "finished_at", "created_at", "updated_at"}
    ),
    "supervisor_controls": frozenset(
        {"id", "scope_type", "scope_id", "pause_all", "pause_inference",
         "pause_publication", "reason", "actor", "created_at", "updated_at"}
    ),
    "supervisor_activity": frozenset(
        {"id", "job_id", "project_id", "event_type", "level", "operation",
         "state", "duration_ms", "error_category", "message", "details_json",
         "created_at"}
    ),
}

_PRIMARY_KEYS = {
    "specialist_agents": "agent_id",
    "planned_task_dependencies": "task_id",
}

_ORDER_COLUMNS = {
    "execution_attempts": "started_at",
    "publication_operations": "started_at",
    "planned_task_dependencies": "plan_revision_id",
    "task_execution_operations": "started_at",
    "pull_request_events": "remote_created_at",
    "pull_request_corrections": "created_at",
    "supervisor_jobs": "eligible_at",
    "supervisor_activity": "created_at",
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
        primary_key = _PRIMARY_KEYS.get(table, "id")
        assignments = ", ".join(f"{column} = ?" for column in values)
        try:
            cursor = self.connection.execute(
                f"UPDATE {table} SET {assignments} WHERE {primary_key} = ?",
                (*values.values(), entity_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(str(exc)) from exc
        if cursor.rowcount != 1:
            raise NotFoundError(f"{table} no encontrado: {entity_id}")

    def get(self, table: str, entity_id: str) -> dict[str, Any]:
        self._validate_table(table)
        primary_key = _PRIMARY_KEYS.get(table, "id")
        row = self.connection.execute(
            f"SELECT * FROM {table} WHERE {primary_key} = ?", (entity_id,)
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
        order_column = _ORDER_COLUMNS.get(table, "created_at")
        primary_key = _PRIMARY_KEYS.get(table, "id")
        suffix = f" ORDER BY {order_column}, {primary_key}"
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

