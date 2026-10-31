"""M04.5: supervisor local persistente y conservador de workflows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError, field_validator

from .domain import SupervisorJob, SupervisorJobState, WorkCategory
from .errors import ConflictError, CoreError, ValidationError
from .github import (
    GitHubAuthenticationError, GitHubPermissionError, GitHubRateLimitError,
    GitHubTransientError,
)
from .sqlite import SQLiteRepository
from .service import OrchestratorCore
from .bootstrap import BootstrapService
from .github import GitHubConfig, GitHubHttpClient
from .github_service import GitHubRegistryService
from .issues import IssueCoordinationService
from .reviews import PullRequestReviewService
from .runtime_config import load_account_configs
from .tasks import TaskExecutionService


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="microseconds")


class PollIntervals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: float = Field(60, ge=5, le=86400)
    pull_requests: float = Field(60, ge=5, le=86400)
    approvals: float = Field(90, ge=5, le=86400)
    tasks: float = Field(10, ge=1, le=86400)
    recovery: float = Field(120, ge=5, le=86400)
    idle: float = Field(5, ge=.1, le=3600)


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    global_active: int = Field(1, ge=1, le=32)
    per_project: int = Field(1, ge=1, le=16)
    per_agent: int = Field(1, ge=1, le=8)
    per_account: int = Field(1, ge=1, le=8)
    github_active: int = Field(1, ge=1, le=16)
    jobs_per_cycle: int = Field(10, ge=1, le=1000)
    lease_seconds: int = Field(900, ge=30, le=86400)
    max_attempts: int = Field(5, ge=1, le=100)
    execution_timeout: int = Field(900, ge=10, le=86400)
    shutdown_grace: int = Field(30, ge=0, le=3600)
    backoff_initial: int = Field(10, ge=1, le=3600)
    backoff_max: int = Field(900, ge=1, le=86400)


class ProjectRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: Path
    allowed_paths: tuple[str, ...] = ("src", "tests", "doc", "README.md")
    enabled: bool = True

    @field_validator("workspace")
    @classmethod
    def absolute_workspace(cls, value: Path) -> Path:
        value = value.expanduser().resolve()
        if not value.is_dir():
            raise ValueError(f"workspace inexistente: {value}")
        return value


class SupervisorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: Path
    accounts: Path | None = None
    task_workspace_root: Path
    projects: dict[str, ProjectRuntime] = Field(default_factory=dict)
    authorized_users: tuple[str, ...] = ()
    github_token_env: str = "VIBECODER_GITHUB_TOKEN"
    intervals: PollIntervals = Field(default_factory=PollIntervals)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    inference_enabled: bool = True
    publication_enabled: bool = False
    remote_reads_enabled: bool = True
    runner_id: str = Field(default_factory=lambda: f"local-{uuid4()}")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database", "task_workspace_root", "accounts")
    @classmethod
    def absolute_path(cls, value: Path | None, info: Any) -> Path | None:
        if value is None:
            return None
        path = value.expanduser().resolve()
        if info.field_name == "accounts" and not path.is_file():
            raise ValueError(f"accounts.json inexistente: {path}")
        return path

    @classmethod
    def load(cls, path: Path) -> "SupervisorConfig":
        return cls.model_validate_json(path.resolve().read_text(encoding="utf-8"))


class JobSpec(BaseModel):
    job_type: str
    category: WorkCategory
    source_type: str
    source_id: str
    project_id: str | None = None
    priority: int = 100
    payload: dict[str, Any] = Field(default_factory=dict)
    recurring_seconds: float | None = None


class SupervisorStore:
    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def schedule(self, spec: JobSpec, *, max_attempts: int) -> SupervisorJob:
        existing = self._active(spec.job_type, spec.source_type, spec.source_id)
        if existing:
            return self.job(existing)
        now, job_id = _stamp(), str(uuid4())
        payload = dict(spec.payload)
        if spec.recurring_seconds is not None:
            payload["recurring_seconds"] = spec.recurring_seconds
        self.repository.insert("supervisor_jobs", {
            "id": job_id, "project_id": spec.project_id, "job_type": spec.job_type,
            "category": spec.category.value, "source_type": spec.source_type,
            "source_id": spec.source_id, "state": "PENDING", "priority": spec.priority,
            "eligible_at": now, "attempts": 0, "max_attempts": max_attempts,
            "operation_key": f"{spec.job_type}:{spec.source_type}:{spec.source_id}",
            "payload_json": json.dumps(payload, sort_keys=True),
            "last_error_category": None, "last_error_message": None,
            "next_action": None, "runner_id": None, "lease_token": None,
            "lease_acquired_at": None, "lease_heartbeat_at": None,
            "lease_expires_at": None, "started_at": None, "finished_at": None,
            "created_at": now, "updated_at": now,
        })
        return self.get(job_id)

    def get(self, job_id: str) -> SupervisorJob:
        return self.job(self.repository.get("supervisor_jobs", job_id))

    def list(self, *, state: SupervisorJobState | None = None) -> list[SupervisorJob]:
        rows = self.repository.list("supervisor_jobs", **({"state": state.value} if state else {}))
        return [self.job(row) for row in rows]

    def ready(self, now: datetime | None = None) -> list[SupervisorJob]:
        stamp = _stamp(now)
        rows = self.repository.connection.execute(
            "SELECT * FROM supervisor_jobs WHERE state IN ('PENDING','RETRY_WAIT') "
            "AND eligible_at <= ? ORDER BY priority, eligible_at, created_at", (stamp,),
        ).fetchall()
        return [self.job(dict(row)) for row in rows]

    def claim(self, job_id: str, config: SupervisorConfig, now: datetime | None = None) -> SupervisorJob | None:
        current_time, token = now or _now(), str(uuid4())
        with self.repository.transaction():
            row = self.repository.get("supervisor_jobs", job_id)
            if row["state"] not in {"PENDING", "RETRY_WAIT"} or str(row["eligible_at"]) > _stamp(current_time):
                return None
            job = self.job(row)
            blocked = self._control_block(job)
            if blocked or not self._capacity(job, config.limits):
                return None
            self.repository.update("supervisor_jobs", job.id, {
                "state": "RUNNING", "attempts": job.attempts + 1,
                "runner_id": config.runner_id, "lease_token": token,
                "lease_acquired_at": _stamp(current_time), "lease_heartbeat_at": _stamp(current_time),
                "lease_expires_at": _stamp(current_time + timedelta(seconds=config.limits.lease_seconds)),
                "started_at": _stamp(current_time), "finished_at": None, "updated_at": _stamp(current_time),
            })
        return self.get(job.id)

    def renew(self, job_id: str, lease_token: str, seconds: int) -> SupervisorJob:
        job = self.get(job_id)
        if job.state is not SupervisorJobState.RUNNING or job.lease_token != lease_token:
            raise ConflictError("lease no vigente")
        now = _now()
        self.repository.update("supervisor_jobs", job_id, {
            "lease_heartbeat_at": _stamp(now),
            "lease_expires_at": _stamp(now + timedelta(seconds=seconds)), "updated_at": _stamp(now),
        })
        return self.get(job_id)

    def finish(self, job_id: str, lease_token: str, *, recurring: bool = False) -> SupervisorJob:
        job = self._leased(job_id, lease_token)
        now = _now()
        seconds = float(job.payload.get("recurring_seconds", 0)) if recurring else 0
        state = "PENDING" if recurring else "SUCCEEDED"
        self.repository.update("supervisor_jobs", job.id, {
            "state": state, "eligible_at": _stamp(now + timedelta(seconds=seconds)),
            "last_error_category": None, "last_error_message": None, "next_action": None,
            "runner_id": None, "lease_token": None, "lease_acquired_at": None,
            "lease_heartbeat_at": None, "lease_expires_at": None,
            "finished_at": _stamp(now), "updated_at": _stamp(now),
        })
        return self.get(job.id)

    def fail(self, job_id: str, lease_token: str, exc: BaseException, limits: RuntimeLimits) -> SupervisorJob:
        job = self._leased(job_id, lease_token)
        category, retryable, uncertain, retry_after = classify_error(exc)
        now = _now()
        if uncertain:
            state, next_action, eligible = "UNKNOWN", "reconciliar antes de reintentar", now
        elif not retryable or job.attempts >= job.max_attempts:
            state, next_action, eligible = "BLOCKED", "intervención requerida", now
        else:
            delay = retry_after or min(limits.backoff_max, limits.backoff_initial * (2 ** max(0, job.attempts - 1)))
            jitter = int(hashlib.sha256(job.id.encode()).hexdigest()[:2], 16) % max(1, int(delay * .2) + 1)
            state, next_action, eligible = "RETRY_WAIT", "reintento conservador", now + timedelta(seconds=delay + jitter)
        self.repository.update("supervisor_jobs", job.id, {
            "state": state, "eligible_at": _stamp(eligible), "last_error_category": category,
            "last_error_message": str(exc)[:2000], "next_action": next_action,
            "runner_id": None, "lease_token": None, "lease_acquired_at": None,
            "lease_heartbeat_at": None, "lease_expires_at": None,
            "finished_at": _stamp(now), "updated_at": _stamp(now),
        })
        return self.get(job.id)

    def recover_expired(self, now: datetime | None = None) -> list[SupervisorJob]:
        stamp, recovered = _stamp(now), []
        rows = self.repository.connection.execute(
            "SELECT id FROM supervisor_jobs WHERE state='RUNNING' AND lease_expires_at < ?", (stamp,),
        ).fetchall()
        for row in rows:
            self.repository.update("supervisor_jobs", str(row["id"]), {
                "state": "UNKNOWN", "last_error_category": "expired_lease",
                "last_error_message": "reserva expirada; el efecto externo es indeterminado",
                "next_action": "reconciliar workflow original", "runner_id": None,
                "lease_token": None, "lease_acquired_at": None, "lease_heartbeat_at": None,
                "lease_expires_at": None, "finished_at": stamp, "updated_at": stamp,
            })
            recovered.append(self.get(str(row["id"])))
        return recovered

    def schedule_recoveries(self, *, max_attempts: int) -> list[SupervisorJob]:
        jobs = []
        for unknown in self.list(state=SupervisorJobState.UNKNOWN):
            jobs.append(self.schedule(JobSpec(
                job_type="RECONCILE_JOB", category=WorkCategory.RECOVERY,
                source_type="SUPERVISOR_JOB", source_id=unknown.id,
                project_id=unknown.project_id, priority=0,
                payload={"original_job_type": unknown.job_type,
                         "original_source_type": unknown.source_type,
                         "original_source_id": unknown.source_id},
            ), max_attempts=max_attempts))
        return jobs

    def set_pause(self, scope_type: str, scope_id: str, *, all_work: bool, inference: bool,
                  publication: bool, actor: str, reason: str | None = None) -> None:
        if scope_type not in {"GLOBAL", "PROJECT"}:
            raise ValidationError("scope_type debe ser GLOBAL o PROJECT")
        control_id, now = f"{scope_type}:{scope_id}", _stamp()
        values = {"pause_all": int(all_work), "pause_inference": int(inference),
                  "pause_publication": int(publication), "reason": reason,
                  "actor": actor, "updated_at": now}
        if self.repository.find_one("supervisor_controls", scope_type=scope_type, scope_id=scope_id):
            self.repository.update("supervisor_controls", control_id, values)
        else:
            self.repository.insert("supervisor_controls", {"id": control_id, "scope_type": scope_type,
                "scope_id": scope_id, **values, "created_at": now})

    def activity(self, event_type: str, message: str, *, job: SupervisorJob | None = None,
                 level: str = "INFO", duration_ms: int | None = None,
                 error_category: str | None = None, details: Mapping[str, Any] | None = None) -> None:
        self.repository.insert("supervisor_activity", {
            "id": str(uuid4()), "job_id": job.id if job else None,
            "project_id": job.project_id if job else None, "event_type": event_type,
            "level": level, "operation": job.job_type if job else None,
            "state": job.state.value if job else None, "duration_ms": duration_ms,
            "error_category": error_category, "message": message[:2000],
            "details_json": json.dumps(dict(details), sort_keys=True) if details else None,
            "created_at": _stamp(),
        })

    def _active(self, job_type: str, source_type: str, source_id: str) -> Mapping[str, Any] | None:
        row = self.repository.connection.execute(
            "SELECT * FROM supervisor_jobs WHERE job_type=? AND source_type=? AND source_id=? "
            "AND state IN ('PENDING','RUNNING','RETRY_WAIT','UNKNOWN') ORDER BY created_at LIMIT 1",
            (job_type, source_type, source_id),
        ).fetchone()
        return dict(row) if row else None

    def _leased(self, job_id: str, token: str) -> SupervisorJob:
        job = self.get(job_id)
        if job.state is not SupervisorJobState.RUNNING or job.lease_token != token:
            raise ConflictError("el trabajo no pertenece a esta reserva")
        return job

    def _control_block(self, job: SupervisorJob) -> bool:
        return self.control_reason(job.project_id, job.category) is not None

    def control_reason(self, project_id: str | None, category: WorkCategory) -> str | None:
        controls = self.repository.list("supervisor_controls")
        relevant = [row for row in controls if row["scope_type"] == "GLOBAL" or
                    (row["scope_type"] == "PROJECT" and row["scope_id"] == project_id)]
        for row in relevant:
            if row["pause_all"]: return str(row["reason"] or "operación pausada")
            if row["pause_inference"] and category is WorkCategory.CODEX:
                return str(row["reason"] or "inferencia pausada")
            if row["pause_publication"] and category is WorkCategory.REMOTE_WRITE:
                return str(row["reason"] or "publicación pausada")
        return None

    def _capacity(self, job: SupervisorJob, limits: RuntimeLimits) -> bool:
        active = [self.job(row) for row in self.repository.list("supervisor_jobs", state="RUNNING")]
        if len(active) >= limits.global_active: return False
        if job.project_id and sum(item.project_id == job.project_id for item in active) >= limits.per_project: return False
        for key, limit in (("agent_id", limits.per_agent), ("account_id", limits.per_account),
                           ("workspace", 1), ("pr_id", 1)):
            value = job.payload.get(key)
            if value is not None and sum(item.payload.get(key) == value for item in active) >= limit: return False
        if job.category in {WorkCategory.REMOTE_READ, WorkCategory.REMOTE_WRITE}:
            if sum(item.category in {WorkCategory.REMOTE_READ, WorkCategory.REMOTE_WRITE} for item in active) >= limits.github_active:
                return False
        return True

    @staticmethod
    def job(row: Mapping[str, Any]) -> SupervisorJob:
        return SupervisorJob(id=str(row["id"]), project_id=str(row["project_id"]) if row["project_id"] else None,
            job_type=str(row["job_type"]), category=WorkCategory(str(row["category"])),
            source_type=str(row["source_type"]), source_id=str(row["source_id"]),
            state=SupervisorJobState(str(row["state"])), priority=int(row["priority"]),
            eligible_at=str(row["eligible_at"]), attempts=int(row["attempts"]), max_attempts=int(row["max_attempts"]),
            operation_key=str(row["operation_key"]), payload=json.loads(str(row["payload_json"])),
            last_error_category=str(row["last_error_category"]) if row["last_error_category"] else None,
            last_error_message=str(row["last_error_message"]) if row["last_error_message"] else None,
            next_action=str(row["next_action"]) if row["next_action"] else None,
            runner_id=str(row["runner_id"]) if row["runner_id"] else None,
            lease_token=str(row["lease_token"]) if row["lease_token"] else None,
            lease_expires_at=str(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]))


class Scheduler:
    """Deriva intenciones; los workflows siguen siendo la autoridad."""
    def __init__(self, repository: SQLiteRepository, config: SupervisorConfig):
        self.repository, self.config = repository, config

    def plan(self) -> list[JobSpec]:
        specs: list[JobSpec] = []
        enabled = set(self.config.projects) if self.config.projects else {
            str(row["id"]) for row in self.repository.list("projects") if row["status"] == "ACTIVE"
        }
        for project in self.repository.list("projects"):
            project_id = str(project["id"])
            if project["status"] == "ACTIVE" and project_id in enabled and self.config.remote_reads_enabled:
                specs.append(JobSpec(job_type="ISSUE_POLL", category=WorkCategory.REMOTE_READ,
                    source_type="PROJECT", source_id=project_id, project_id=project_id, priority=40,
                    recurring_seconds=self.config.intervals.issues))
        for workflow in self.repository.list("issue_workflows"):
            project_id, workflow_id = str(workflow["project_id"]), str(workflow["id"])
            if project_id not in enabled or workflow["status"] in {"COMPLETED", "BLOCKED"}: continue
            specs.append(JobSpec(job_type="ISSUE_WORKFLOW_POLL", category=WorkCategory.REMOTE_READ,
                source_type="ISSUE_WORKFLOW", source_id=workflow_id, project_id=project_id,
                recurring_seconds=self.config.intervals.issues))
        for workflow in self.repository.list("bootstrap_workflows"):
            project_id, workflow_id = str(workflow["project_id"]), str(workflow["id"])
            if project_id not in enabled or workflow["status"] in {"APPROVED", "BLOCKED"}: continue
            specs.append(JobSpec(job_type="BOOTSTRAP_POLL", category=WorkCategory.REMOTE_READ,
                source_type="BOOTSTRAP_WORKFLOW", source_id=workflow_id, project_id=project_id,
                recurring_seconds=self.config.intervals.issues))
        for comment in self.repository.list("bootstrap_comments"):
            workflow = self.repository.get("bootstrap_workflows", str(comment["workflow_id"]))
            if comment["status"] == "RECEIVED":
                specs.append(JobSpec(job_type="BOOTSTRAP_PROCESS", category=WorkCategory.CODEX,
                    source_type="BOOTSTRAP_WORKFLOW", source_id=str(comment["workflow_id"]),
                    project_id=str(workflow["project_id"]), priority=20))
            elif comment["status"] == "RUNNING":
                specs.append(JobSpec(job_type="RECONCILE_BOOTSTRAP", category=WorkCategory.RECOVERY,
                    source_type="BOOTSTRAP_WORKFLOW", source_id=str(comment["workflow_id"]),
                    project_id=str(workflow["project_id"]), priority=5))
            elif comment["status"] == "RESPONSE_PENDING":
                specs.append(JobSpec(job_type="BOOTSTRAP_PUBLISH", category=WorkCategory.REMOTE_WRITE,
                    source_type="BOOTSTRAP_WORKFLOW", source_id=str(comment["workflow_id"]),
                    project_id=str(workflow["project_id"]), priority=12))
        for comment in self.repository.list("issue_comments"):
            if comment["status"] == "RECEIVED":
                workflow = self.repository.get("issue_workflows", str(comment["workflow_id"]))
                specs.append(JobSpec(job_type="ISSUE_PROCESS", category=WorkCategory.CODEX,
                    source_type="ISSUE_WORKFLOW", source_id=str(comment["workflow_id"]),
                    project_id=str(workflow["project_id"]), priority=20))
            elif comment["status"] == "RESPONSE_PENDING":
                workflow = self.repository.get("issue_workflows", str(comment["workflow_id"]))
                specs.append(JobSpec(job_type="ISSUE_PUBLISH", category=WorkCategory.REMOTE_WRITE,
                    source_type="ISSUE_WORKFLOW", source_id=str(comment["workflow_id"]),
                    project_id=str(workflow["project_id"]), priority=12))
        for revision in self.repository.list("plan_revisions"):
            workflow = self.repository.get("issue_workflows", str(revision["workflow_id"]))
            if str(workflow.get("active_plan_revision_id") or "") != str(revision["id"]):
                continue
            if revision["status"] == "CONFIRMED":
                specs.append(JobSpec(job_type="PLAN_APPROVAL_CHECK", category=WorkCategory.REMOTE_READ,
                    source_type="PLAN_REVISION", source_id=str(revision["id"]), project_id=str(workflow["project_id"]),
                    recurring_seconds=self.config.intervals.approvals))
            elif revision["status"] == "APPROVED":
                pending = [row for row in self.repository.list("planned_tasks", plan_revision_id=str(revision["id"]))
                           if not row["materialized_task_id"]]
                if pending:
                    specs.append(JobSpec(job_type="PLAN_MATERIALIZE", category=WorkCategory.LOCAL_WRITE,
                        source_type="PLAN_REVISION", source_id=str(revision["id"]), project_id=str(workflow["project_id"]), priority=15))
        execution_by_task = {str(row["task_id"]): row for row in self.repository.list("task_execution_workflows")}
        active_specialists = {str(row["agent_id"]) for row in self.repository.list("specialist_agents")
                              if row["status"] == "ACTIVE"}
        for task in self.repository.list("tasks"):
            if (task["task_type"] == "PLANNED_WORK" and task["status"] == "PENDING"
                    and str(task["id"]) not in execution_by_task
                    and str(task["agent_id"]) in active_specialists
                    and self._dependencies_satisfied(str(task["id"]))):
                specs.append(JobSpec(job_type="TASK_PREPARE", category=WorkCategory.LOCAL_WRITE,
                    source_type="TASK", source_id=str(task["id"]), project_id=str(task["project_id"]), priority=30,
                    payload={"agent_id": task["agent_id"]}))
        stage = {"WORKSPACE_READY": ("TASK_EXECUTE", WorkCategory.CODEX), "GENERATED": ("TASK_VALIDATE", WorkCategory.LOCAL_WRITE),
                 "VALIDATED": ("TASK_COMMIT", WorkCategory.LOCAL_WRITE), "COMMITTED": ("TASK_PUBLISH", WorkCategory.REMOTE_WRITE),
                 "BRANCH_PUBLISHED": ("TASK_CREATE_PR", WorkCategory.REMOTE_WRITE)}
        for execution in self.repository.list("task_execution_workflows"):
            if execution["status"] in {"WAITING_DEPENDENCIES", "READY"}:
                specs.append(JobSpec(job_type="TASK_PREPARE", category=WorkCategory.LOCAL_WRITE,
                    source_type="TASK", source_id=str(execution["task_id"]),
                    project_id=str(execution["project_id"]), priority=30,
                    payload={"agent_id": execution["agent_id"], "account_id": execution["account_id"],
                             "workspace": execution["workspace_path"]},
                    recurring_seconds=self.config.intervals.tasks))
            elif execution["status"] in stage:
                kind, category = stage[str(execution["status"])]
                specs.append(JobSpec(job_type=kind, category=category, source_type="TASK_EXECUTION",
                    source_id=str(execution["id"]), project_id=str(execution["project_id"]), priority=25,
                    payload={"agent_id": execution["agent_id"], "account_id": execution["account_id"],
                             "workspace": execution["workspace_path"]}))
            elif execution["status"] in {"UNKNOWN", "PREPARING", "RUNNING", "PUSHING", "CREATING_PR"}:
                specs.append(JobSpec(job_type="RECONCILE_TASK", category=WorkCategory.RECOVERY,
                    source_type="TASK_EXECUTION", source_id=str(execution["id"]), project_id=str(execution["project_id"]), priority=5))
        supervised = {str(row["execution_id"]) for row in self.repository.list("pull_request_supervisions")}
        for execution in self.repository.list("task_execution_workflows", status="PR_OPEN"):
            if str(execution["id"]) not in supervised:
                specs.append(JobSpec(job_type="PR_START", category=WorkCategory.REMOTE_READ,
                    source_type="TASK_EXECUTION", source_id=str(execution["id"]), project_id=str(execution["project_id"]), priority=20))
        for supervision in self.repository.list("pull_request_supervisions"):
            if supervision["status"] not in {"MERGED", "CLOSED_UNMERGED", "BLOCKED", "UNKNOWN"}:
                specs.append(JobSpec(job_type="PR_POLL", category=WorkCategory.REMOTE_READ,
                    source_type="PR_SUPERVISION", source_id=str(supervision["id"]), project_id=str(supervision["project_id"]),
                    payload={"pr_id": supervision["pull_request_id"]}, recurring_seconds=self.config.intervals.pull_requests))
            elif supervision["status"] == "MERGED":
                execution = self.repository.get("task_execution_workflows", str(supervision["execution_id"]))
                if execution["status"] != "MERGED":
                    specs.append(JobSpec(job_type="MERGE_COMPLETE", category=WorkCategory.REMOTE_READ,
                        source_type="PR_SUPERVISION", source_id=str(supervision["id"]),
                        project_id=str(supervision["project_id"]), priority=10,
                        payload={"pr_id": supervision["pull_request_id"]}))
        for event in self.repository.list("pull_request_events"):
            if event["status"] == "RECEIVED":
                supervision = self.repository.get("pull_request_supervisions", str(event["supervision_id"]))
                specs.append(JobSpec(job_type="PR_PROCESS", category=WorkCategory.CODEX,
                    source_type="PR_EVENT", source_id=str(event["id"]), project_id=str(supervision["project_id"]), priority=20,
                    payload={"agent_id": event["recipient_agent_id"], "pr_id": supervision["pull_request_id"]}))
            elif event["status"] == "RESPONSE_PENDING":
                supervision = self.repository.get("pull_request_supervisions", str(event["supervision_id"]))
                specs.append(JobSpec(job_type="PR_RESPONSE", category=WorkCategory.REMOTE_WRITE,
                    source_type="PR_EVENT", source_id=str(event["id"]), project_id=str(supervision["project_id"]),
                    priority=12, payload={"pr_id": supervision["pull_request_id"]}))
        correction_stages = {"AUTHORIZED": ("CORRECTION_EXECUTE", WorkCategory.CODEX),
            "GENERATED": ("CORRECTION_VALIDATE", WorkCategory.LOCAL_WRITE), "VALIDATED": ("CORRECTION_COMMIT", WorkCategory.LOCAL_WRITE),
            "COMMITTED": ("CORRECTION_PUBLISH", WorkCategory.REMOTE_WRITE), "PUBLISHED": ("CORRECTION_REPLY", WorkCategory.REMOTE_WRITE)}
        for correction in self.repository.list("pull_request_corrections"):
            if correction["status"] in correction_stages:
                kind, category = correction_stages[str(correction["status"])]
                supervision = self.repository.get("pull_request_supervisions", str(correction["supervision_id"]))
                execution = self.repository.get("task_execution_workflows", str(correction["execution_id"]))
                specs.append(JobSpec(job_type=kind, category=category, source_type="CORRECTION",
                    source_id=str(correction["id"]), project_id=str(supervision["project_id"]), priority=18,
                    payload={"agent_id": execution["agent_id"], "account_id": execution["account_id"],
                             "workspace": execution["workspace_path"], "pr_id": supervision["pull_request_id"]}))
        return specs

    def _dependencies_satisfied(self, task_id: str) -> bool:
        planned = self.repository.find_one("planned_tasks", materialized_task_id=task_id)
        if planned is None:
            return False
        dependencies = self.repository.list(
            "planned_task_dependencies", plan_revision_id=str(planned["plan_revision_id"]),
            task_id=str(planned["id"]),
        )
        for dependency in dependencies:
            source = self.repository.get("planned_tasks", str(dependency["depends_on_task_id"]))
            materialized = source.get("materialized_task_id")
            if not materialized:
                return False
            execution = self.repository.find_one("task_execution_workflows", task_id=str(materialized))
            if execution is None or execution["status"] != "MERGED":
                return False
        return True

    def sync(self, store: SupervisorStore) -> list[SupervisorJob]:
        specs = self.plan()
        desired = {f"{spec.job_type}:{spec.source_type}:{spec.source_id}" for spec in specs}
        now = _stamp()
        for job in store.list():
            if (job.state in {SupervisorJobState.PENDING, SupervisorJobState.RETRY_WAIT}
                    and job.operation_key not in desired):
                self.repository.update("supervisor_jobs", job.id, {
                    "state": "CANCELLED", "next_action": "intención obsoleta; workflow avanzado",
                    "finished_at": now, "updated_at": now,
                })
        return [store.schedule(spec, max_attempts=self.config.limits.max_attempts) for spec in specs]


Handler = Callable[[SupervisorJob], Any | Awaitable[Any]]


class Dispatcher:
    def __init__(self, handlers: Mapping[str, Handler] | None = None):
        self.handlers = dict(handlers or {})

    async def dispatch(self, job: SupervisorJob) -> Any:
        handler = self.handlers.get(job.job_type)
        if handler is None:
            raise ConflictError(f"no hay ejecutor configurado para {job.job_type}")
        result = handler(job)
        return await result if isinstance(result, Awaitable) else result


class WorkflowDispatcher(Dispatcher):
    """Adapta trabajos a M04.2/M04.3/M04.4; no replica sus autorizaciones."""
    def __init__(self, repository: SQLiteRepository, config: SupervisorConfig):
        super().__init__()
        self.repository, self.config = repository, config

    async def dispatch(self, job: SupervisorJob) -> Any:
        project = self.config.projects.get(job.project_id or "")
        if project is None:
            raise ConflictError("falta configuración runtime del proyecto")
        token = os.environ.get(self.config.github_token_env)
        if job.category in {WorkCategory.REMOTE_READ, WorkCategory.REMOTE_WRITE} and not token:
            raise ConflictError(f"falta variable {self.config.github_token_env}")
        accounts = load_account_configs(self.config.accounts) if self.config.accounts else {}
        core = OrchestratorCore(self.repository)
        with GitHubHttpClient(GitHubConfig(token=token or "offline")) as client:
            github = GitHubRegistryService(core, client)
            issues = IssueCoordinationService(core, github, accounts, project.workspace,
                authorized_users=self.config.authorized_users)
            tasks = TaskExecutionService(core, github, accounts, self.config.task_workspace_root,
                project_workspace=project.workspace)
            reviews = PullRequestReviewService(core, github, accounts, project.workspace,
                authorized_users=self.config.authorized_users)
            bootstrap = BootstrapService(core, github, accounts,
                authorized_users=self.config.authorized_users)
            kind, source = job.job_type, job.source_id
            if kind == "RECONCILE_JOB":
                original = self.repository.get("supervisor_jobs", source)
                source_type, source_id = str(original["source_type"]), str(original["source_id"])
                if source_type == "TASK_EXECUTION": result = tasks.reconcile(source_id)
                elif source_type == "PR_SUPERVISION": result = reviews.reconcile(source_id)
                elif source_type == "BOOTSTRAP_WORKFLOW": result = bootstrap.recover(source_id)
                elif source_type == "ISSUE_WORKFLOW": result = issues.recover(source_id)
                elif source_type == "PR_EVENT":
                    event = self.repository.get("pull_request_events", source_id)
                    result = reviews.reconcile(str(event["supervision_id"]))
                elif source_type == "CORRECTION":
                    correction = self.repository.get("pull_request_corrections", source_id)
                    result = reviews.reconcile(str(correction["supervision_id"]))
                elif WorkCategory(str(original["category"])) is WorkCategory.REMOTE_READ:
                    result = {"safe_to_poll_again": True}
                else:
                    raise ConflictError("la operación indeterminada requiere reconciliación humana")
                self.repository.update("supervisor_jobs", source, {
                    "state": "CANCELLED", "next_action": "reconciliado mediante workflow original",
                    "finished_at": _stamp(), "updated_at": _stamp(),
                })
                return result
            if kind == "BOOTSTRAP_POLL": return bootstrap.poll(source)
            if kind == "BOOTSTRAP_PROCESS": return await bootstrap.process_round(source)
            if kind == "BOOTSTRAP_PUBLISH": return bootstrap.retry_publications(source)
            if kind == "RECONCILE_BOOTSTRAP": return bootstrap.recover(source)
            if kind == "ISSUE_POLL": return issues.discover(source)
            if kind == "ISSUE_WORKFLOW_POLL": return issues.poll(source)
            if kind == "ISSUE_PROCESS": return await issues.process_round(source)
            if kind == "ISSUE_PUBLISH": return issues.recover(source)
            if kind == "PLAN_APPROVAL_CHECK": return issues.check_approval(source)
            if kind == "PLAN_MATERIALIZE": return issues.materialize(source)
            if kind == "TASK_PREPARE": return tasks.prepare(source, allowed_paths=project.allowed_paths)
            if kind == "TASK_EXECUTE": return await tasks.run(source)
            if kind == "TASK_VALIDATE": return tasks.validate(source)
            if kind == "TASK_COMMIT": return tasks.commit(source)
            if kind == "TASK_PUBLISH": return tasks.publish(source, token=token or "", authorized=True)
            if kind == "TASK_CREATE_PR": return tasks.create_pr(source, authorized=True)
            if kind == "RECONCILE_TASK": return tasks.reconcile(source)
            if kind == "PR_START": return reviews.start(source)
            if kind == "PR_POLL": return reviews.poll(source)
            if kind == "MERGE_COMPLETE": return reviews.complete_merge(source)
            if kind == "PR_PROCESS": return await reviews.process_event(source)
            if kind == "PR_RESPONSE": return reviews.publish_response(source)
            if kind == "CORRECTION_EXECUTE": return await reviews.run_correction(source)
            if kind == "CORRECTION_VALIDATE": return reviews.validate_correction(source)
            if kind == "CORRECTION_COMMIT": return reviews.commit_correction(source)
            if kind == "CORRECTION_PUBLISH": return reviews.publish_correction(source, token=token or "", authorized=True)
            if kind == "CORRECTION_REPLY": return reviews.reply_correction(source)
        raise ConflictError(f"tipo de trabajo no soportado: {job.job_type}")


class Supervisor:
    _REMOTE_READ_PREREQUISITES = {
        "TASK_PREPARE",
        "TASK_EXECUTE",
        "CORRECTION_EXECUTE",
    }

    def __init__(self, repository: SQLiteRepository, config: SupervisorConfig,
                 dispatcher: Dispatcher | None = None):
        self.repository, self.config = repository, config
        self.store, self.scheduler = SupervisorStore(repository), Scheduler(repository, config)
        self.dispatcher = dispatcher or WorkflowDispatcher(repository, config)
        self.stopping = asyncio.Event()

    def dry_run(self) -> list[dict[str, Any]]:
        return [{"job_type": item.job_type, "category": item.category.value,
                 "source": f"{item.source_type}:{item.source_id}", "project_id": item.project_id,
                 "blocked": self._runtime_block(item)} for item in self.scheduler.plan()]

    async def once(self) -> dict[str, int]:
        recovered = self.store.recover_expired()
        self.store.schedule_recoveries(max_attempts=self.config.limits.max_attempts)
        scheduled = self.scheduler.sync(self.store)
        completed = blocked = 0
        for candidate in self.store.ready()[:self.config.limits.jobs_per_cycle]:
            if self.stopping.is_set(): break
            reason = self._runtime_block(candidate)
            if reason: continue
            job = self.store.claim(candidate.id, self.config)
            if not job: continue
            started = _now()
            try:
                await asyncio.wait_for(self.dispatcher.dispatch(job), timeout=self.config.limits.execution_timeout)
                self.store.finish(job.id, str(job.lease_token), recurring=self._still_recurring(job))
                completed += 1
                self.store.activity("job.completed", "trabajo completado", job=job,
                    duration_ms=int((_now() - started).total_seconds() * 1000))
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)): raise
                result = self.store.fail(job.id, str(job.lease_token), exc, self.config.limits)
                blocked += result.state in {SupervisorJobState.BLOCKED, SupervisorJobState.UNKNOWN}
                self.store.activity("job.failed", str(exc), job=job, level="ERROR",
                    error_category=result.last_error_category,
                    duration_ms=int((_now() - started).total_seconds() * 1000))
        return {"scheduled": len(scheduled), "completed": completed, "blocked": blocked,
                "recovered_unknown": len(recovered)}

    async def reconcile_once(self) -> dict[str, int]:
        recovered = self.store.recover_expired()
        jobs = self.store.schedule_recoveries(max_attempts=self.config.limits.max_attempts)
        completed = blocked = 0
        for candidate in jobs:
            if candidate.state not in {SupervisorJobState.PENDING, SupervisorJobState.RETRY_WAIT}: continue
            job = self.store.claim(candidate.id, self.config)
            if not job: continue
            try:
                await self.dispatcher.dispatch(job)
                self.store.finish(job.id, str(job.lease_token)); completed += 1
            except BaseException as exc:
                result = self.store.fail(job.id, str(job.lease_token), exc, self.config.limits)
                blocked += result.state in {SupervisorJobState.BLOCKED, SupervisorJobState.UNKNOWN}
        return {"expired": len(recovered), "scheduled": len(jobs),
                "completed": completed, "blocked": blocked}

    async def run(self) -> None:
        while not self.stopping.is_set():
            await self.once()
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=self.config.intervals.idle)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self.stopping.set()

    def _runtime_block(self, job: JobSpec | SupervisorJob) -> str | None:
        if job.category is WorkCategory.CODEX and not self.config.inference_enabled:
            return "inferencia pausada por configuración"
        publishes_response = job.job_type in {"BOOTSTRAP_PROCESS", "ISSUE_PROCESS", "PR_PROCESS"}
        if (job.category is WorkCategory.REMOTE_WRITE or publishes_response) and not self.config.publication_enabled:
            return "publicaciones remotas pausadas por configuración"
        if (job.category is WorkCategory.REMOTE_READ
                or job.job_type in self._REMOTE_READ_PREREQUISITES) \
                and not self.config.remote_reads_enabled:
            return "lecturas remotas pausadas por configuración"
        project = self.config.projects.get(job.project_id or "")
        if project is not None and not project.enabled:
            return "proyecto deshabilitado"
        return self.store.control_reason(job.project_id, job.category)

    def _still_recurring(self, job: SupervisorJob) -> bool:
        if "recurring_seconds" not in job.payload:
            return False
        return any(spec.job_type == job.job_type and spec.source_type == job.source_type
                   and spec.source_id == job.source_id for spec in self.scheduler.plan())


def classify_error(exc: BaseException) -> tuple[str, bool, bool, int | None]:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout_unknown", False, True, None
    if isinstance(exc, GitHubRateLimitError):
        value = getattr(exc, "retry_after", None)
        return "rate_limit", True, False, int(value) if value else None
    if isinstance(exc, GitHubTransientError): return "transient", True, False, None
    if isinstance(exc, GitHubAuthenticationError): return "authentication", False, False, None
    if isinstance(exc, GitHubPermissionError): return "authorization", False, False, None
    if isinstance(exc, (ConflictError, ValidationError, PydanticValidationError)):
        return "configuration", False, False, None
    if isinstance(exc, CoreError): return "permanent", False, False, None
    return "unknown", False, True, None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config-check"); sub.add_parser("dry-run"); sub.add_parser("status")
    jobs = sub.add_parser("jobs"); jobs.add_argument("--state", choices=[item.value for item in SupervisorJobState])
    sub.add_parser("once"); sub.add_parser("run"); sub.add_parser("reconcile")
    for name in ("pause", "resume"):
        cmd = sub.add_parser(name); cmd.add_argument("--project-id"); cmd.add_argument("--inference", action="store_true")
        cmd.add_argument("--publication", action="store_true"); cmd.add_argument("--actor", required=True); cmd.add_argument("--reason")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try: config = SupervisorConfig.load(args.config)
    except (OSError, PydanticValidationError, ValueError) as exc:
        print(f"configuración inválida: {exc}", file=sys.stderr); return 2
    if args.command == "config-check": print("OK"); return 0
    logging.basicConfig(level=getattr(logging, config.log_level), format="%(message)s")
    with SQLiteRepository(config.database) as repository:
        supervisor = Supervisor(repository, config)
        if args.command == "dry-run": print(json.dumps(supervisor.dry_run(), indent=2, ensure_ascii=False)); return 0
        if args.command == "status":
            counts = {state.value: len(supervisor.store.list(state=state)) for state in SupervisorJobState}
            print(json.dumps({"runner_id": config.runner_id, "jobs": counts,
                "controls": repository.list("supervisor_controls")}, indent=2)); return 0
        if args.command == "jobs":
            state = SupervisorJobState(args.state) if args.state else None
            print(json.dumps([job.__dict__ for job in supervisor.store.list(state=state)], indent=2, default=str)); return 0
        if args.command in {"pause", "resume"}:
            paused = args.command == "pause"; scope = "PROJECT" if args.project_id else "GLOBAL"; scope_id = args.project_id or "global"
            supervisor.store.set_pause(scope, scope_id, all_work=paused and not (args.inference or args.publication),
                inference=paused and args.inference, publication=paused and args.publication,
                actor=args.actor, reason=args.reason); print("OK"); return 0
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        for signame in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(signame, supervisor.stop)
            except NotImplementedError: pass
        if args.command == "once": print(json.dumps(loop.run_until_complete(supervisor.once()))); return 0
        if args.command == "reconcile": print(json.dumps(loop.run_until_complete(supervisor.reconcile_once()))); return 0
        loop.run_until_complete(supervisor.run()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
