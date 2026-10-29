CREATE TABLE generation_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    bootstrap_workflow_id TEXT NOT NULL UNIQUE REFERENCES bootstrap_workflows(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL UNIQUE REFERENCES github_repositories(id) ON DELETE RESTRICT,
    approved_by TEXT NOT NULL,
    approved_event_id INTEGER NOT NULL,
    specification_hash TEXT NOT NULL,
    workspace_path TEXT NOT NULL UNIQUE,
    remote_url TEXT NOT NULL,
    base_branch TEXT,
    base_commit TEXT,
    bootstrap_branch TEXT NOT NULL,
    task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id TEXT UNIQUE REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'PREPARING_WORKSPACE', 'GENERATING', 'VALIDATING',
        'READY_TO_PUBLISH', 'BLOCKED', 'FAILED', 'ARCHIVED'
    )),
    instruction_versions_json TEXT NOT NULL,
    generated_instruction_versions_json TEXT NOT NULL,
    validation_result_json TEXT,
    commit_hash TEXT UNIQUE,
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status != 'READY_TO_PUBLISH' OR commit_hash IS NOT NULL)
);

CREATE INDEX generation_workflows_by_status
ON generation_workflows(status, created_at);
