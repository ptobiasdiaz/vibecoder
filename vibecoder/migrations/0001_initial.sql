PRAGMA foreign_keys = ON;

CREATE TABLE codex_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    auth_type TEXT NOT NULL CHECK (auth_type IN ('CHATGPT', 'API_KEY', 'ACCESS_TOKEN')),
    credential_ref TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'UNAVAILABLE', 'AUTH_REQUIRED', 'QUOTA_EXHAUSTED', 'DISABLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    github_repository TEXT UNIQUE,
    codex_account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('UNINITIALIZED', 'BOOTSTRAPPING', 'DESIGNING', 'READY', 'ACTIVE', 'DISABLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    agent_type TEXT NOT NULL CHECK (agent_type IN ('PRINCIPAL', 'SPECIALIZED')),
    config_ref TEXT NOT NULL,
    documentation_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE', 'UNAVAILABLE')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE UNIQUE INDEX one_principal_per_project
ON agents(project_id) WHERE agent_type = 'PRINCIPAL';

CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    codex_thread_id TEXT UNIQUE,
    codex_account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    persistence_state TEXT NOT NULL CHECK (persistence_state IN (
        'NOT_CREATED', 'THREAD_CREATED_UNCONFIRMED', 'THREAD_PERSISTED',
        'RECOVERABLE', 'UNAVAILABLE', 'LOST'
    )),
    last_activity_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (persistence_state = 'NOT_CREATED' AND codex_thread_id IS NULL)
        OR (persistence_state != 'NOT_CREATED')
    )
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'RUNNING', 'WAITING', 'WAITING_QUOTA', 'WAITING_AUTH',
        'COMPLETED', 'FAILED', 'CANCELLED'
    )),
    external_ref TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX unique_task_external_ref
ON tasks(project_id, external_ref) WHERE external_ref IS NOT NULL;

CREATE UNIQUE INDEX unique_task_idempotency_key
ON tasks(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX tasks_pending_by_project
ON tasks(project_id, status, created_at);

CREATE TABLE execution_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_category TEXT,
    error_message TEXT,
    error_details_json TEXT,
    UNIQUE (task_id, attempt_number)
);

CREATE TRIGGER session_account_matches_project_insert
BEFORE INSERT ON agent_sessions
BEGIN
    SELECT CASE WHEN NEW.codex_account_id != (
        SELECT p.codex_account_id FROM agents a
        JOIN projects p ON p.id = a.project_id
        WHERE a.id = NEW.agent_id
    ) THEN RAISE(ABORT, 'session account does not match project account') END;
END;

CREATE TRIGGER session_account_matches_project_update
BEFORE UPDATE OF agent_id, codex_account_id ON agent_sessions
BEGIN
    SELECT CASE WHEN NEW.codex_account_id != (
        SELECT p.codex_account_id FROM agents a
        JOIN projects p ON p.id = a.project_id
        WHERE a.id = NEW.agent_id
    ) THEN RAISE(ABORT, 'session account does not match project account') END;
END;

CREATE TRIGGER task_agent_matches_project_insert
BEFORE INSERT ON tasks
BEGIN
    SELECT CASE WHEN NEW.project_id != (
        SELECT project_id FROM agents WHERE id = NEW.agent_id
    ) THEN RAISE(ABORT, 'task agent does not belong to project') END;
END;

CREATE TRIGGER task_agent_matches_project_update
BEFORE UPDATE OF project_id, agent_id ON tasks
BEGIN
    SELECT CASE WHEN NEW.project_id != (
        SELECT project_id FROM agents WHERE id = NEW.agent_id
    ) THEN RAISE(ABORT, 'task agent does not belong to project') END;
END;

