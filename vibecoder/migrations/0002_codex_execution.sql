ALTER TABLE tasks ADD COLUMN prompt TEXT;

ALTER TABLE execution_attempts RENAME TO execution_attempts_v1;

CREATE TABLE execution_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'CANCELLED')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_category TEXT,
    error_message TEXT,
    error_details_json TEXT,
    session_id TEXT REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    codex_thread_id TEXT,
    codex_turn_id TEXT,
    result_text TEXT,
    UNIQUE (task_id, attempt_number)
);

INSERT INTO execution_attempts (
    id, task_id, attempt_number, status, started_at, finished_at,
    error_code, error_category, error_message, error_details_json
)
SELECT id, task_id, attempt_number, status, started_at, finished_at,
       error_code, error_category, error_message, error_details_json
FROM execution_attempts_v1;

DROP TABLE execution_attempts_v1;

CREATE INDEX attempts_by_status ON execution_attempts(status, started_at);

