"""Persistent conservative scheduler for the 0.0.1 workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .github_identity import utc_now
from .sqlite import SQLiteRepository


class PersistentScheduler:
    SAFE_RETRY_KINDS = frozenset({"POLL_PROJECT", "GENERATE_PLAN", "PUBLISH_PLAN", "POLL_PULL_REQUESTS"})

    def __init__(self, repository: SQLiteRepository, workflows: Any, *, runner_id: str | None = None,
                 lease_seconds: int = 120):
        self.repository, self.workflows = repository, workflows
        self.runner_id = runner_id or f"daemon:{uuid.uuid4()}"
        self.lease_seconds = lease_seconds

    def ensure_poll_jobs(self) -> None:
        now = utc_now()
        for row in self.repository.query_all("SELECT id FROM projects WHERE status='ENABLED'"):
            key = f"poll-project:{row['id']}"
            self.repository.execute("INSERT OR IGNORE INTO scheduled_jobs VALUES(?,?,?,?,?,'PENDING',0,?,0,?,?,?)",
                (str(uuid.uuid4()),row["id"],"POLL_PROJECT","project",row["id"],now,key,now,now))

    def recover(self) -> int:
        now=utc_now(); rows=self.repository.query_all("SELECT j.id,j.kind FROM scheduled_jobs j JOIN job_leases l ON l.job_id=j.id WHERE j.state='RUNNING' AND l.expires_at<?",(now,))
        for row in rows:
            # A crashed local poll can safely be repeated; provider and publication
            # jobs rely on their own idempotency records. Task execution cannot.
            state="PENDING" if row["kind"] in self.SAFE_RETRY_KINDS else "UNKNOWN"
            self.repository.execute("UPDATE scheduled_jobs SET state=?,updated_at=? WHERE id=?",(state,now,row["id"]))
            self.repository.execute("DELETE FROM job_leases WHERE job_id=?",(row["id"],))
            if state=="UNKNOWN": self._barrier(f"job interrumpido con efecto incierto: {row['id']}")
        return len(rows)

    def cycle(self) -> dict[str, Any] | None:
        if self._blocked(): return None
        self.ensure_poll_jobs(); job=self._claim()
        if not job:return None
        try:
            result=self._dispatch(job)
        except Exception as exc:
            current=self.repository.query_one("SELECT attempts FROM scheduled_jobs WHERE id=?",(job["id"],)) or {"attempts":1}
            if job["kind"] in self.SAFE_RETRY_KINDS and int(current["attempts"])<5 and not self.repository.query_one("SELECT 1 FROM recovery_barriers WHERE status='ACTIVE'"):
                self.repository.execute("DELETE FROM job_leases WHERE job_id=?",(job["id"],));eligible=(datetime.now(timezone.utc)+timedelta(seconds=min(300,15*(2**(int(current['attempts'])-1))))).isoformat();self.repository.execute("UPDATE scheduled_jobs SET state='PENDING',eligible_at=?,updated_at=? WHERE id=?",(eligible,utc_now(),job["id"]));state="PENDING"
            else:
                self._finish(job,"FAILED",type(exc).__name__);state="FAILED"
            return {"job_id":job["id"],"state":state,"error":type(exc).__name__}
        self._finish(job,"COMPLETED",None)
        if job["kind"]=="POLL_PROJECT":
            # Recurring polling is a new job, never mutation of a completed one.
            next_at=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
            key=f"poll-project:{job['project_id']}:{next_at}"
            now=utc_now();self.repository.execute("INSERT INTO scheduled_jobs VALUES(?,?,?,?,?,'PENDING',0,?,0,?,?,?)",
                (str(uuid.uuid4()),job["project_id"],"POLL_PROJECT","project",job["project_id"],next_at,key,now,now))
        return {"job_id":job["id"],"state":"COMPLETED","result":result}

    def status(self) -> dict[str, Any]:
        counts={str(x["state"]):int(x["n"]) for x in self.repository.query_all("SELECT state,count(*) n FROM scheduled_jobs GROUP BY state")}
        return {"runner_id":self.runner_id,"jobs":counts,"leases":int(self.repository.query_one("SELECT count(*) n FROM job_leases")["n"]),
                "unknown_external_operations":int(self.repository.query_one("SELECT count(*) n FROM external_operations WHERE status='UNKNOWN'")["n"])}

    def reconcile(self, actor: str) -> dict[str, int]:
        # Reconciliation never guesses remote outcomes. It only releases jobs
        # whose corresponding durable operation has confirmed success.
        remote=self.workflows.reconcile_unknown();changed=0
        for row in self.repository.query_all("SELECT j.id FROM scheduled_jobs j JOIN external_operations o ON o.source_id=j.source_id WHERE j.state='UNKNOWN' AND o.status='SUCCEEDED'"):
            self.repository.execute("UPDATE scheduled_jobs SET state='COMPLETED',updated_at=? WHERE id=?",(utc_now(),row["id"]));changed+=1
        self.repository.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),actor,"maintenance.reconcile","scheduler",None,"SUCCEEDED",f'{{"changed":{changed}}}',utc_now()))
        return {"changed":changed,"remote_confirmed":int(remote.get("confirmed",0))}

    def _claim(self):
        now=utc_now(); expires=(datetime.now(timezone.utc)+timedelta(seconds=self.lease_seconds)).isoformat(); token=str(uuid.uuid4())
        with self.repository.transaction():
            row=self.repository.query_one("SELECT * FROM scheduled_jobs WHERE state='PENDING' AND eligible_at<=? ORDER BY priority DESC,created_at,id LIMIT 1",(now,))
            if not row:return None
            self.repository.execute("UPDATE scheduled_jobs SET state='RUNNING',attempts=attempts+1,updated_at=? WHERE id=?",(now,row["id"]))
            self.repository.execute("INSERT INTO job_leases VALUES(?,?,?,?,?,?)",(row["id"],self.runner_id,token,now,now,expires))
        return row

    def _dispatch(self,job):
        if job["kind"]=="POLL_PROJECT":return self.workflows.poll(str(job["project_id"]))
        if job["kind"]=="GENERATE_PLAN":return self.workflows.generate_plan(str(job["source_id"]))
        if job["kind"]=="PUBLISH_PLAN":return self.workflows.publish_plan(str(job["source_id"]))
        if job["kind"]=="EXECUTE_TASK":return self.workflows.execute_task(str(job["source_id"]))
        if job["kind"]=="APPLY_CORRECTION":return self.workflows.apply_correction(str(job["source_id"]))
        raise ValueError(f"tipo de job no soportado: {job['kind']}")

    def _finish(self,job,state,error):
        now=utc_now();self.repository.execute("DELETE FROM job_leases WHERE job_id=?",(job["id"],));self.repository.execute("UPDATE scheduled_jobs SET state=?,updated_at=? WHERE id=?",(state,now,job["id"]))
        if state=="FAILED":self.repository.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),"scheduler","job.failed","scheduled_job",job["id"],"FAILED",f'{{"error":"{error}"}}',now))

    def _blocked(self):
        paused=self.repository.query_one("SELECT 1 FROM operational_pauses WHERE released_at IS NULL LIMIT 1")
        barrier=self.repository.query_one("SELECT 1 FROM recovery_barriers WHERE status='ACTIVE' LIMIT 1")
        github=self.repository.query_one("SELECT status FROM github_identity WHERE singleton=1")
        return bool(paused or barrier or not github or github["status"]!="ONLINE")

    def _barrier(self,reason):self.repository.execute("INSERT INTO recovery_barriers VALUES(?,?, 'ACTIVE',?,NULL,NULL)",(str(uuid.uuid4()),reason,utc_now()))
