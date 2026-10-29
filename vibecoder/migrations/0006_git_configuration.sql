CREATE TABLE git_configurations (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES bootstrap_workflows(id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL REFERENCES github_repositories(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision > 0),
    status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'CONFIRMED', 'APPROVED', 'APPLIED')),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    config_json TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    source_comment_id INTEGER,
    observed_github_id INTEGER NOT NULL,
    observed_is_empty INTEGER NOT NULL CHECK (observed_is_empty IN (0, 1)),
    observed_default_branch TEXT,
    observed_head_sha TEXT,
    specification_hash TEXT,
    approved_by TEXT,
    approved_event_id INTEGER,
    applied_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_id, revision),
    UNIQUE (workflow_id, configuration_hash, status),
    CHECK (
        status NOT IN ('APPROVED', 'APPLIED')
        OR (specification_hash IS NOT NULL AND approved_by IS NOT NULL AND approved_event_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX one_approved_git_configuration
ON git_configurations(workflow_id) WHERE status IN ('APPROVED', 'APPLIED');

CREATE INDEX git_configurations_by_workflow
ON git_configurations(workflow_id, status, revision);

ALTER TABLE generation_workflows
ADD COLUMN git_configuration_id TEXT REFERENCES git_configurations(id) ON DELETE RESTRICT;

ALTER TABLE generation_workflows
ADD COLUMN git_configuration_hash TEXT;
