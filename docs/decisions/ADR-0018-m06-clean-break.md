# ADR-0018: M06 is an incompatible product line

Status: accepted.

Vibecoder 0.0.1 starts with schema version 1 and does not migrate or interpret
0.4.0 state. Existing, unknown, and corrupt databases are rejected read-only.
No compatibility entry points or aliases are retained. Operational safety
invariants are retained as design requirements rather than schema compatibility.

