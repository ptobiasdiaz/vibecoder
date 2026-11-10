# ADR-0025: Daemon-owned file secret store

Status: accepted.

Secret values are files below the daemon's private secret directory (`0700`);
files are `0600`, atomically published and never named from user input. SQLite
contains only opaque metadata references. Symlinks and traversal are rejected.
Startup reconciliation removes safe orphan temporary/secret files and retires
superseded GitHub PAT metadata.

This protects against other local users, diagnostics and accidental logging; it
does not provide cryptographic isolation from a compromised daemon. Provider
plugins must not execute as arbitrary in-process code with the daemon UID.
M06.5 must provide a mediated or separately sandboxed runtime boundary.

