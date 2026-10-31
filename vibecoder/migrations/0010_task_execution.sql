CREATE TABLE task_execution_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    issue_workflow_id TEXT NOT NULL REFERENCES issue_workflows(id) ON DELETE RESTRICT,
    plan_revision_id TEXT NOT NULL REFERENCES plan_revisions(id) ON DELETE RESTRICT,
    planned_task_id TEXT NOT NULL UNIQUE REFERENCES planned_tasks(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    agent_revision_id TEXT NOT NULL REFERENCES agent_configuration_revisions(id) ON DELETE RESTRICT,
    agent_configuration_hash TEXT NOT NULL,
    instructions_hash TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'READY', 'WAITING_DEPENDENCIES', 'PREPARING', 'WORKSPACE_READY',
        'RUNNING', 'GENERATED', 'VALIDATING', 'VALIDATED', 'COMMITTED',
        'PUSHING', 'BRANCH_PUBLISHED', 'CREATING_PR', 'PR_OPEN', 'MERGED',
        'BLOCKED', 'UNKNOWN', 'FAILED'
    )),
    workspace_path TEXT NOT NULL UNIQUE,
    remote_url TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    work_branch TEXT NOT NULL UNIQUE,
    session_id TEXT UNIQUE REFERENCES agent_sessions(id) ON DELETE RESTRICT,
    instruction_versions_json TEXT NOT NULL,
    authorization_snapshot_json TEXT NOT NULL,
    context_hash TEXT,
    allowed_paths_json TEXT NOT NULL,
    validation_commands_json TEXT NOT NULL,
    validation_authorized_by TEXT,
    result_text TEXT,
    validation_result_json TEXT,
    commit_sha TEXT UNIQUE,
    published_sha TEXT,
    pull_request_id INTEGER,
    pull_request_number INTEGER,
    pull_request_url TEXT,
    merge_commit_sha TEXT,
    remote_state_json TEXT,
    blocked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (validation_commands_json = '[]' OR validation_authorized_by IS NOT NULL),
    CHECK (status NOT IN ('COMMITTED', 'PUSHING', 'BRANCH_PUBLISHED', 'CREATING_PR', 'PR_OPEN', 'MERGED') OR commit_sha IS NOT NULL),
    CHECK (status NOT IN ('BRANCH_PUBLISHED', 'CREATING_PR', 'PR_OPEN', 'MERGED') OR published_sha IS NOT NULL),
    CHECK (status NOT IN ('PR_OPEN', 'MERGED') OR pull_request_number IS NOT NULL),
    CHECK (status != 'MERGED' OR merge_commit_sha IS NOT NULL)
);

CREATE TABLE task_execution_operations (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES task_execution_workflows(id) ON DELETE CASCADE,
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
    UNIQUE (execution_id, operation_type, idempotency_key)
);

CREATE INDEX task_execution_by_status
ON task_execution_workflows(project_id, status, created_at);

CREATE INDEX task_execution_operations_by_execution
ON task_execution_operations(execution_id, started_at);
