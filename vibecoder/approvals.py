"""Provider-neutral runtime approval persistence and state machine."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .domain import RuntimeApprovalDecision, RuntimeApprovalStatus
from .errors import ConflictError, NotFoundError, ValidationError
from .github_identity import utc_now
from .sqlite import SQLiteRepository

KINDS = frozenset({"COMMAND", "FILE_CHANGE", "PERMISSIONS"})
DECISIONS = frozenset({"APPROVE", "DENY"})
MAX_DETAILS = 16 * 1024


def ensure_approval_schema(repository: SQLiteRepository) -> None:
    repository.execute("""CREATE TABLE IF NOT EXISTS runtime_approval_requests (
        id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id),
        session_id TEXT NOT NULL REFERENCES provider_sessions(id),
        execution_id TEXT NOT NULL REFERENCES provider_executions(id),
        provider_request_ref TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('COMMAND','FILE_CHANGE','PERMISSIONS')),
        summary TEXT NOT NULL, details_json TEXT NOT NULL,
        allowed_decisions_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','DECISION_RECORDED','DELIVERING','APPROVED','DENIED','EXPIRED','UNKNOWN')),
        decision TEXT CHECK(decision IN ('APPROVE','DENY')), actor TEXT,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT,
        delivered_at TEXT, resolved_at TEXT, diagnostic TEXT,
        UNIQUE(worker_id,execution_id,provider_request_ref))""")
    repository.execute("CREATE INDEX IF NOT EXISTS runtime_approval_pending ON runtime_approval_requests(status,created_at)")


class RuntimeApprovalService:
    def __init__(self, repository: SQLiteRepository):
        self.repository = repository
        ensure_approval_schema(repository)

    def recover(self) -> None:
        now = utc_now()
        self.repository.execute(
            "UPDATE runtime_approval_requests SET status='UNKNOWN',"
            "diagnostic='daemon restarted before provider confirmation',resolved_at=? "
            "WHERE status IN ('PENDING','DECISION_RECORDED','DELIVERING')",
            (now,),
        )

    def expire_due(self) -> None:
        """Materialize elapsed human-decision deadlines without provider activity."""
        now = utc_now()
        self.repository.execute(
            "UPDATE runtime_approval_requests SET status='EXPIRED',decision='DENY',"
            "resolved_at=?,diagnostic='human decision timeout' "
            "WHERE status='PENDING' AND expires_at<=?",
            (now, now),
        )

    def register(self, worker_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        required = {"session_id", "execution_id", "provider_request_ref", "kind", "summary", "details", "allowed_decisions", "timeout_seconds"}
        if set(data) != required: raise ValidationError("campos de aprobación no válidos")
        kind, summary = data["kind"], data["summary"]
        allowed = data["allowed_decisions"]
        if kind not in KINDS or not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise ValidationError("solicitud de aprobación no válida")
        if not isinstance(allowed, list) or not allowed or not set(allowed) <= DECISIONS:
            raise ValidationError("decisiones permitidas no válidas")
        details = json.dumps(data["details"], ensure_ascii=False, sort_keys=True)
        if len(details.encode()) > MAX_DETAILS: raise ValidationError("detalles de aprobación demasiado grandes")
        timeout = int(data["timeout_seconds"])
        if not 1 <= timeout <= 900: raise ValidationError("expiración no válida")
        execution = self.repository.query_one("SELECT session_id,worker_id FROM provider_executions WHERE id=?", (data["execution_id"],))
        if not execution or execution["worker_id"] != worker_id or execution["session_id"] != data["session_id"]:
            raise ValidationError("aprobación no correlacionada con la ejecución")
        existing = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE worker_id=? AND execution_id=? AND provider_request_ref=?", (worker_id, data["execution_id"], data["provider_request_ref"]))
        if existing: return self._public(existing)
        now = datetime.now(timezone.utc); approval_id = str(uuid.uuid4())
        self.repository.execute("INSERT INTO runtime_approval_requests VALUES(?,?,?,?,?,?,?,?,?,'PENDING',NULL,NULL,?,?,NULL,NULL,NULL,NULL)",
            (approval_id, worker_id, data["session_id"], data["execution_id"], data["provider_request_ref"], kind,
             summary.strip(), details, json.dumps(sorted(set(allowed))), now.isoformat(), (now + timedelta(seconds=timeout)).isoformat()))
        return self.get(approval_id)

    def list(self, *, pending: bool = False, worker_id: str | None = None) -> list[dict[str, Any]]:
        self.expire_due()
        sql, params = "SELECT * FROM runtime_approval_requests WHERE 1=1", []
        if pending: sql += " AND status='PENDING'" 
        if worker_id: sql += " AND worker_id=?"; params.append(worker_id)
        sql += " ORDER BY created_at"
        return [self._public(row) for row in self.repository.query_all(sql, params)]

    def get(self, approval_id: str) -> dict[str, Any]:
        self.expire_due()
        row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=?", (approval_id,))
        if not row: raise NotFoundError("solicitud de aprobación no encontrada")
        return self._public(row)

    def decide(self, approval_id: str, decision: str, actor: str) -> dict[str, Any]:
        if decision not in DECISIONS: raise ValidationError("decisión no válida")
        row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=?", (approval_id,))
        if not row: raise NotFoundError("solicitud de aprobación no encontrada")
        if row["decision"]:
            if row["decision"] != decision: raise ConflictError("la solicitud ya tiene una decisión contradictoria")
            return self._public(row)
        if row["status"] != "PENDING": raise ConflictError("la solicitud ya no admite decisiones")
        now = utc_now()
        self.repository.execute("UPDATE runtime_approval_requests SET status='DECISION_RECORDED',decision=?,actor=?,decided_at=? WHERE id=?", (decision, actor, now, approval_id))
        return self.get(approval_id)

    def claim(self, worker_id: str, approval_id: str) -> dict[str, Any]:
        row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=? AND worker_id=?", (approval_id, worker_id))
        if not row: raise NotFoundError("solicitud de aprobación no encontrada")
        if row["status"] == "PENDING" and datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            now = utc_now()
            self.repository.execute("UPDATE runtime_approval_requests SET status='EXPIRED',decision='DENY',resolved_at=?,diagnostic='human decision timeout' WHERE id=?", (now, approval_id))
            row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=?", (approval_id,))
        elif row["status"] == "DECISION_RECORDED":
            self.repository.execute("UPDATE runtime_approval_requests SET status='DELIVERING',delivered_at=? WHERE id=?", (utc_now(), approval_id))
            row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=?", (approval_id,))
        return self._public(row or {})

    def confirm(self, worker_id: str, approval_id: str, delivered: bool, diagnostic: str | None = None) -> dict[str, Any]:
        row = self.repository.query_one("SELECT * FROM runtime_approval_requests WHERE id=? AND worker_id=?", (approval_id, worker_id))
        if not row: raise NotFoundError("solicitud de aprobación no encontrada")
        if row["status"] == "EXPIRED": return self._public(row)
        if row["status"] != "DELIVERING": raise ConflictError("solicitud no pendiente de confirmación")
        status = ("APPROVED" if row["decision"] == "APPROVE" else "DENIED") if delivered else "UNKNOWN"
        self.repository.execute("UPDATE runtime_approval_requests SET status=?,resolved_at=?,diagnostic=? WHERE id=?", (status, utc_now(), diagnostic, approval_id))
        return self.get(approval_id)

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for source, target in (("details_json", "details"), ("allowed_decisions_json", "allowed_decisions")):
            if source in result: result[target] = json.loads(result.pop(source))
        return result
