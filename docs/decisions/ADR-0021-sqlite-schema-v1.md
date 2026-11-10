# ADR-0021: New SQLite schema version 1

Status: accepted.

M06 uses one initial schema containing provider-neutral projects, workers,
agents, sessions, workflows, operation ledgers, leases, and recovery barriers.
Secrets are external references. Agent and plan revisions are immutable. The
last project authorisation is protected both by application logic and a trigger.

