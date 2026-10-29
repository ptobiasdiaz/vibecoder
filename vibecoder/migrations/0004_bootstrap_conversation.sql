CREATE TABLE bootstrap_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL UNIQUE REFERENCES github_repositories(id) ON DELETE CASCADE,
    github_issue_id INTEGER NOT NULL UNIQUE,
    github_issue_node_id TEXT NOT NULL UNIQUE,
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    issue_url TEXT NOT NULL,
    principal_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'APPROVAL_PENDING', 'APPROVED', 'BLOCKED')),
    approval_label TEXT NOT NULL,
    approved_by TEXT,
    approved_event_id INTEGER UNIQUE,
    approved_at TEXT,
    frozen_specification TEXT,
    last_polled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (status = 'APPROVED' AND approved_by IS NOT NULL AND approved_event_id IS NOT NULL
         AND approved_at IS NOT NULL AND frozen_specification IS NOT NULL)
        OR status != 'APPROVED'
    )
);

CREATE TABLE bootstrap_comments (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES bootstrap_workflows(id) ON DELETE CASCADE,
    github_comment_id INTEGER NOT NULL UNIQUE,
    github_node_id TEXT NOT NULL UNIQUE,
    author_login TEXT NOT NULL,
    author_type TEXT NOT NULL,
    body TEXT NOT NULL,
    remote_created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'RECEIVED', 'IGNORED', 'REJECTED', 'QUEUED', 'RUNNING',
        'RESPONSE_PENDING', 'COMPLETED', 'BLOCKED'
    )),
    rejection_reason TEXT,
    task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    response_text TEXT,
    response_github_comment_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_id, github_comment_id),
    CHECK (status != 'RESPONSE_PENDING' OR response_text IS NOT NULL),
    CHECK (status != 'COMPLETED' OR response_github_comment_id IS NOT NULL)
);

CREATE INDEX bootstrap_comments_pending
ON bootstrap_comments(workflow_id, status, remote_created_at, github_comment_id);
