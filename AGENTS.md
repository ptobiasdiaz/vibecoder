# AGENTS.md — vibecoder 0.0.1

The active architecture is the provider-neutral M06 line. Do not restore APIs,
migrations, entry points, or configuration formats from vibecoder 0.4.0.

## Invariants

- Provider-specific concepts stay behind plugin ports.
- The core never exposes the GitHub credential to an AI plugin.
- `UNKNOWN` executions and external operations require reconciliation; never retry blindly.
- Agent names are scoped by project. Resource ambiguity is always an error.
- Secrets are references in SQLite, never values.
- Unauthorised remote text must not enter agent-visible persistence.
- Preserve workspace confinement, immutable revisions, human approvals, and verified merges.

## Development

- Use `apply_patch` for edits and preserve unrelated working-tree changes.
- Tests are offline by default; live tests require explicit opt-in.
- Do not add compatibility adapters for the 0.4.0 line.
- M06.4 adds read-only local Git discovery and daemon-owned project registration.
  The user's checkout is never an operational workspace or source of files.
- M06.5 adds trusted declarative plugins, workers and a private runtime protocol.
  DirectProcessLauncher is test-only and never a claimed security boundary.
- M06.6 keeps the Codex client inside its isolated plugin runtime. Only a
  confirmed `turn/completed` with `status=completed` is success; do not claim
  cancellation or reconciliation until that Codex version is live-validated.
- M06.6.2 runtime approvals are per provider action, deny by default, and
  distinct from plan approvals. Never replay an unconfirmed approval after a
  restart; preserve it as `UNKNOWN`.
- M06.7 permits automatic materialization of logical agents only from a
  validated, human-approved immutable plan. Provider workers never own the
  administrative records or GitHub effects.
- The CLI must never open SQLite. Mutations belong to the daemon and use kernel peer credentials.
- The GitHub PAT is daemon-owned secret material. Never include it in domain
  objects, diagnostics, audit details, exceptions, URLs, arguments or plugin contracts.

