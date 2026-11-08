CREATE TABLE administrative_identities (
    id TEXT PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED')),
    source TEXT NOT NULL,
    is_initial INTEGER NOT NULL DEFAULT 0 CHECK (is_initial IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_administrative_identity_initial
    ON administrative_identities(is_initial) WHERE is_initial = 1;
