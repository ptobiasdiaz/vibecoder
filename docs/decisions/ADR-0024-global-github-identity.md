# ADR-0024: One global GitHub identity

Status: accepted.

An installation has at most one active GitHub identity. A Fine-grained PAT is
validated with `GET /user`; its numeric user ID and login are persisted while
the token is referenced from external secret storage. A transient network or
rate-limit failure changes connectivity to OFFLINE without deleting identity
or credential. HTTP 401 becomes AUTH_FAILED, but GitHub does not reveal whether
the PAT is malformed, expired or revoked.

Replacing a PAT validates first and swaps the SQLite reference transactionally.
A different GitHub user is rejected once projects exist. Connectivity is
checked in one bounded background probe with persisted exponential backoff.

