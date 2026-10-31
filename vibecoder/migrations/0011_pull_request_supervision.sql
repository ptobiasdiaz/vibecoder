CREATE TABLE pull_request_supervisions (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL UNIQUE REFERENCES task_execution_workflows(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE RESTRICT,
    pull_request_number INTEGER NOT NULL,
    pull_request_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'MONITORING', 'REVIEW_PENDING', 'CHANGES_REQUESTED', 'CORRECTING',
        'WAITING_FOR_REVIEW', 'APPROVED', 'MERGED', 'CLOSED_UNMERGED',
        'BLOCKED', 'UNKNOWN'
    )),
    review_state TEXT NOT NULL,
    observed_head_sha TEXT NOT NULL,
    observed_base_sha TEXT,
    merge_commit_sha TEXT,
    blocked_reason TEXT,
    last_polled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (repository_id, pull_request_number)
);

CREATE TABLE pull_request_events (
    id TEXT PRIMARY KEY,
    supervision_id TEXT NOT NULL REFERENCES pull_request_supervisions(id) ON DELETE CASCADE,
    remote_kind TEXT NOT NULL CHECK (remote_kind IN ('ISSUE_COMMENT', 'REVIEW', 'REVIEW_COMMENT')),
    remote_id INTEGER NOT NULL,
    remote_node_id TEXT,
    author_id INTEGER,
    author_login TEXT NOT NULL,
    author_type TEXT NOT NULL,
    body TEXT NOT NULL,
    review_state TEXT,
    commit_sha TEXT,
    path TEXT,
    remote_created_at TEXT NOT NULL,
    remote_updated_at TEXT,
    authorized INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    recipient_agent_id TEXT REFERENCES agents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'RECEIVED', 'IGNORED', 'REJECTED', 'QUEUED', 'RUNNING',
        'RESPONSE_PENDING', 'COMPLETED', 'BLOCKED'
    )),
    rejection_reason TEXT,
    task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id TEXT REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    response_text TEXT,
    response_comment_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (supervision_id, remote_kind, remote_id),
    CHECK (status != 'RESPONSE_PENDING' OR response_text IS NOT NULL),
    CHECK (status != 'COMPLETED' OR response_comment_id IS NOT NULL)
);

CREATE TABLE pull_request_agent_sessions (
    id TEXT PRIMARY KEY,
    supervision_id TEXT NOT NULL REFERENCES pull_request_supervisions(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    instruction_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (supervision_id, agent_id)
);

CREATE TABLE pull_request_corrections (
    id TEXT PRIMARY KEY,
    supervision_id TEXT NOT NULL REFERENCES pull_request_supervisions(id) ON DELETE RESTRICT,
    execution_id TEXT NOT NULL REFERENCES task_execution_workflows(id) ON DELETE RESTRICT,
    source_event_ids_json TEXT NOT NULL,
    description TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('IN_SCOPE', 'OUT_OF_SCOPE', 'AMBIGUOUS')),
    authorized_by TEXT,
    authorized_at TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'AUTHORIZED', 'RUNNING', 'GENERATED', 'VALIDATED', 'COMMITTED',
        'PUSHING', 'PUBLISHED', 'REPLIED', 'BLOCKED', 'UNKNOWN'
    )),
    previous_sha TEXT NOT NULL,
    new_sha TEXT,
    task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id TEXT REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    result_text TEXT,
    validation_result_json TEXT,
    commit_sha TEXT UNIQUE,
    response_text TEXT,
    response_comment_id INTEGER UNIQUE,
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (scope != 'IN_SCOPE' OR (authorized_by IS NOT NULL AND authorized_at IS NOT NULL))
);

CREATE INDEX pull_request_events_pending
ON pull_request_events(supervision_id, status, remote_kind, remote_created_at, remote_id);

CREATE INDEX pull_request_corrections_by_supervision
ON pull_request_corrections(supervision_id, status, created_at);
