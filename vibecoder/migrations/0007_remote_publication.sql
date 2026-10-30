CREATE TABLE publication_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE RESTRICT,
    bootstrap_workflow_id TEXT NOT NULL UNIQUE REFERENCES bootstrap_workflows(id) ON DELETE RESTRICT,
    git_configuration_id TEXT NOT NULL UNIQUE REFERENCES git_configurations(id) ON DELETE RESTRICT,
    generation_id TEXT NOT NULL UNIQUE REFERENCES generation_workflows(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'READY', 'INITIALIZING_BASE', 'BASE_READY', 'PUSHING_BRANCH',
        'BRANCH_PUBLISHED', 'CREATING_PR', 'WAITING_FOR_MERGE',
        'MERGED', 'ACTIVATED', 'BLOCKED', 'UNKNOWN'
    )),
    base_branch TEXT NOT NULL,
    base_sha TEXT,
    published_branch TEXT NOT NULL,
    published_sha TEXT NOT NULL,
    pull_request_id INTEGER,
    pull_request_number INTEGER,
    pull_request_url TEXT,
    merge_commit_sha TEXT,
    remote_state_json TEXT,
    blocked_reason TEXT,
    activated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE publication_operations (
    id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publication_workflows(id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED', 'UNKNOWN')),
    expected_ref TEXT,
    expected_sha TEXT,
    remote_id TEXT,
    error_category TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (publication_id, operation_type, idempotency_key)
);

CREATE INDEX publication_workflows_by_status
ON publication_workflows(status, created_at);

CREATE INDEX publication_operations_by_publication
ON publication_operations(publication_id, started_at);
