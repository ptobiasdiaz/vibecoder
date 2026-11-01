CREATE TABLE admin_credentials (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    secret_path TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE admin_audit_events (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    result TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_admin_audit_target
    ON admin_audit_events(target_type, target_id, created_at);
