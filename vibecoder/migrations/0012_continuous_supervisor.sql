CREATE TABLE supervisor_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('REMOTE_READ', 'CODEX', 'LOCAL_WRITE', 'REMOTE_WRITE', 'RECOVERY')),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'BLOCKED', 'UNKNOWN', 'CANCELLED')),
    priority INTEGER NOT NULL,
    eligible_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
    operation_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    last_error_category TEXT,
    last_error_message TEXT,
    next_action TEXT,
    runner_id TEXT,
    lease_token TEXT,
    lease_acquired_at TEXT,
    lease_heartbeat_at TEXT,
    lease_expires_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX supervisor_one_active_job
ON supervisor_jobs(job_type, source_type, source_id)
WHERE state IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'UNKNOWN');

CREATE INDEX supervisor_jobs_ready
ON supervisor_jobs(state, eligible_at, priority, created_at);

CREATE TABLE supervisor_controls (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('GLOBAL', 'PROJECT')),
    scope_id TEXT NOT NULL,
    pause_all INTEGER NOT NULL CHECK (pause_all IN (0, 1)),
    pause_inference INTEGER NOT NULL CHECK (pause_inference IN (0, 1)),
    pause_publication INTEGER NOT NULL CHECK (pause_publication IN (0, 1)),
    reason TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (scope_type, scope_id)
);

CREATE TABLE supervisor_activity (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES supervisor_jobs(id) ON DELETE SET NULL,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
    operation TEXT,
    state TEXT,
    duration_ms INTEGER,
    error_category TEXT,
    message TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX supervisor_activity_recent ON supervisor_activity(created_at, level);
