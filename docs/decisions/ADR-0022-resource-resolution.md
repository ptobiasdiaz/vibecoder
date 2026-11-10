# ADR-0022: Exact and scope-aware resource resolution

Status: accepted.

Resolution order is full exact ID, exact natural identifier, then unique
case-folded name in the caller-provided scope. UUID prefixes are not accepted.
Exact IDs take precedence over a colliding name; every other ambiguity returns
the complete candidate set instead of selecting arbitrarily.

