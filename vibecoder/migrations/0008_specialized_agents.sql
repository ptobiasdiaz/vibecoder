CREATE TABLE specialist_agents (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    logical_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'PROPOSED', 'ACTIVE', 'SUSPENDED', 'RETIRED')),
    assigned_account_id TEXT NOT NULL REFERENCES codex_accounts(id) ON DELETE RESTRICT,
    active_revision_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, logical_id)
);

CREATE TABLE agent_configuration_revisions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES specialist_agents(agent_id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'VALIDATED', 'ACTIVE', 'SUPERSEDED', 'REJECTED')),
    role TEXT NOT NULL,
    responsibilities_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    restrictions_json TEXT NOT NULL,
    instructions_path TEXT NOT NULL,
    instructions_hash TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    source_commit TEXT,
    created_by TEXT NOT NULL,
    authorized_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (agent_id, version),
    UNIQUE (agent_id, configuration_hash)
);

CREATE UNIQUE INDEX one_active_agent_revision
ON agent_configuration_revisions(agent_id) WHERE status = 'ACTIVE';

CREATE TABLE agent_audit_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL,
    actor TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    previous_version INTEGER,
    new_version INTEGER,
    result TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX specialist_agents_by_project
ON specialist_agents(project_id, status, logical_id);

CREATE INDEX agent_audit_by_agent
ON agent_audit_events(agent_id, created_at);

DROP TRIGGER session_account_matches_project_insert;
DROP TRIGGER session_account_matches_project_update;

CREATE TRIGGER session_account_matches_agent_insert
BEFORE INSERT ON agent_sessions
BEGIN
    SELECT CASE WHEN NEW.codex_account_id != COALESCE(
        (SELECT assigned_account_id FROM specialist_agents WHERE agent_id = NEW.agent_id),
        (SELECT p.codex_account_id FROM agents a JOIN projects p ON p.id = a.project_id
         WHERE a.id = NEW.agent_id)
    ) THEN RAISE(ABORT, 'session account does not match agent account') END;
END;

CREATE TRIGGER session_account_matches_agent_update
BEFORE UPDATE OF agent_id, codex_account_id ON agent_sessions
BEGIN
    SELECT CASE WHEN NEW.codex_account_id != COALESCE(
        (SELECT assigned_account_id FROM specialist_agents WHERE agent_id = NEW.agent_id),
        (SELECT p.codex_account_id FROM agents a JOIN projects p ON p.id = a.project_id
         WHERE a.id = NEW.agent_id)
    ) THEN RAISE(ABORT, 'session account does not match agent account') END;
END;
