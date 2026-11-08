CREATE TABLE maintenance_records (
    id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reference TEXT,
    details_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX idx_maintenance_records_type
    ON maintenance_records(operation_type, started_at);
