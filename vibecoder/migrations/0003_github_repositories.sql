CREATE TABLE github_repositories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    github_id INTEGER NOT NULL UNIQUE,
    node_id TEXT NOT NULL UNIQUE,
    owner_login TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL UNIQUE,
    html_url TEXT NOT NULL,
    api_url TEXT NOT NULL,
    default_branch TEXT,
    visibility TEXT NOT NULL,
    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
    is_empty INTEGER NOT NULL CHECK (is_empty IN (0, 1)),
    permissions_json TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE github_operations (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMPLETED', 'UNKNOWN', 'FAILED')),
    remote_id TEXT,
    remote_url TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (repository_id, operation_type, idempotency_key)
);

CREATE INDEX github_operations_by_status
ON github_operations(status, created_at);
