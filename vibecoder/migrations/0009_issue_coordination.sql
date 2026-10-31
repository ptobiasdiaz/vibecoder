CREATE TABLE issue_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE RESTRICT,
    github_issue_id INTEGER NOT NULL UNIQUE,
    github_issue_node_id TEXT NOT NULL UNIQUE,
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    issue_url TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    author_login TEXT NOT NULL,
    github_state TEXT NOT NULL CHECK (github_state IN ('open', 'closed')),
    labels_json TEXT NOT NULL,
    principal_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'ACTIVE', 'PLANNING', 'WAITING_APPROVAL', 'APPROVED',
        'MATERIALIZED', 'CLOSED', 'BLOCKED'
    )),
    approval_label TEXT NOT NULL,
    active_plan_revision_id TEXT,
    last_polled_at TEXT,
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, issue_number)
);

CREATE TABLE issue_comments (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES issue_workflows(id) ON DELETE CASCADE,
    github_comment_id INTEGER NOT NULL UNIQUE,
    github_node_id TEXT NOT NULL UNIQUE,
    author_login TEXT NOT NULL,
    author_type TEXT NOT NULL,
    body TEXT NOT NULL,
    remote_created_at TEXT NOT NULL,
    recipient_agent_id TEXT REFERENCES agents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'RECEIVED', 'IGNORED', 'REJECTED', 'QUEUED', 'RUNNING',
        'RESPONSE_PENDING', 'COMPLETED', 'BLOCKED'
    )),
    rejection_reason TEXT,
    task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id TEXT REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    response_text TEXT,
    response_github_comment_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_id, github_comment_id),
    CHECK (status != 'RESPONSE_PENDING' OR response_text IS NOT NULL),
    CHECK (status != 'COMPLETED' OR response_github_comment_id IS NOT NULL)
);

CREATE TABLE issue_agent_sessions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES issue_workflows(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    workspace_path TEXT NOT NULL,
    instruction_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_id, agent_id)
);

CREATE TABLE plan_revisions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES issue_workflows(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT', 'PROPOSED', 'CONFIRMED', 'APPROVED', 'SUPERSEDED', 'BLOCKED'
    )),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    proposed_by_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    published_comment_id INTEGER,
    published_at TEXT,
    approved_by TEXT,
    approved_event_id INTEGER UNIQUE,
    approved_at TEXT,
    specialist_snapshot_json TEXT,
    frozen_context_json TEXT,
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_id, revision),
    CHECK (status != 'CONFIRMED' OR (published_comment_id IS NOT NULL AND published_at IS NOT NULL)),
    CHECK (status != 'APPROVED' OR (
        published_comment_id IS NOT NULL AND approved_by IS NOT NULL
        AND approved_event_id IS NOT NULL AND approved_at IS NOT NULL
        AND specialist_snapshot_json IS NOT NULL AND frozen_context_json IS NOT NULL
    ))
);

CREATE TABLE planned_tasks (
    id TEXT PRIMARY KEY,
    plan_revision_id TEXT NOT NULL REFERENCES plan_revisions(id) ON DELETE RESTRICT,
    logical_id TEXT NOT NULL,
    description TEXT NOT NULL,
    expected_result TEXT NOT NULL,
    assigned_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    acceptance_criteria_json TEXT NOT NULL,
    restrictions_json TEXT NOT NULL,
    materialized_task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE (plan_revision_id, logical_id)
);

CREATE TABLE planned_task_dependencies (
    plan_revision_id TEXT NOT NULL REFERENCES plan_revisions(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES planned_tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES planned_tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (plan_revision_id, task_id, depends_on_task_id),
    CHECK (task_id != depends_on_task_id)
);

CREATE TABLE plan_approval_events (
    id TEXT PRIMARY KEY,
    plan_revision_id TEXT NOT NULL UNIQUE REFERENCES plan_revisions(id) ON DELETE RESTRICT,
    github_event_id INTEGER NOT NULL UNIQUE,
    actor_login TEXT NOT NULL,
    remote_created_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX issue_workflows_by_project ON issue_workflows(project_id, status, issue_number);
CREATE INDEX issue_comments_pending ON issue_comments(workflow_id, status, remote_created_at, github_comment_id);
CREATE INDEX issue_agent_sessions_by_workflow ON issue_agent_sessions(workflow_id, agent_id);
CREATE INDEX plan_revisions_by_workflow ON plan_revisions(workflow_id, revision, status);
CREATE INDEX planned_tasks_by_revision ON planned_tasks(plan_revision_id, logical_id);
