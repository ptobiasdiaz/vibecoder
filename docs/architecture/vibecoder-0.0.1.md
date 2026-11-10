# Vibecoder 0.0.1 architecture

## Implemented in M06.1

The core domain is independent of AI providers. A plugin describes a provider;
a worker is a configured execution instance; an agent is a project-scoped
logical identity. Provider references are opaque.

SQLite schema 1 stores installation identity, GitHub identity metadata,
projects and authorisations, plugins and workers, agents and immutable
revisions, provider sessions and executions, structured workflows, idempotent
external operations, jobs, leases, pauses, and recovery barriers. Secret values
are deliberately absent.

Operational readiness is derived from GitHub connectivity and worker capacity.
Pauses, project availability, eligible work, and recovery barriers remain
independent dimensions. READY does not require a project.

## Implemented in M06.2

`vibecoderd` owns SQLite mutations and exposes protocol v1 over a Unix socket.
The unified `vibecoder` CLI provides status, doctor and persistent global
pause/resume. Mutation authorisation uses `SO_PEERCRED`, root and the
`vibecoder-admin` group. An unconfigured daemon is healthy and reports
`STANDBY/OFFLINE`.

## Implemented in M06.3

One global GitHub identity is resolved from a Fine-grained PAT. SQLite stores
numeric identity and an opaque secret reference; the daemon-owned store holds
the token. A bounded background probe updates ONLINE, OFFLINE or AUTH_FAILED
with persisted backoff. GitHub ONLINE without a worker derives
`STANDBY/ONLINE`.

## Implemented in M06.4

The CLI observes a normal repository or worktree without changing it. The
daemon independently resolves the GitHub repository and selected branch, then
creates a managed workspace at the verified remote SHA. Registration is
idempotent and recoverable, and atomically includes the global GitHub identity
as the project's first authorized user. Projects resolve by full ID, canonical
`owner/repository`, or unique name.

## Implemented in M06.5

Trusted declarative descriptors are separated from executable provider
runtimes. Workers persist isolated identity, storage, state, capabilities and
limits. Runtime code executes through a private framed protocol in a separate
process. Production policy uses per-worker Unix users and hardened transient
systemd units; the fake plugin exercises the same contracts offline. Sessions
and executions use opaque references and preserve UNKNOWN until capability-
gated reconciliation.

## Safety contract

- An UNKNOWN execution or external operation is not safe to repeat blindly.
- Approved plans and agent revisions are immutable.
- Human approvals bind the exact plan revision and hash.
- Merges are represented only after verification.
- Discarded unauthorised events contain metadata but no body.
- Provider plugins receive neither GitHub secrets nor database access.

## Transition state

Historical M04–M05 service modules remain in the source tree for later
extraction of proven workflow logic, but they are not part of the executable
0.0.1 contract and currently cannot run against the new domain/schema. M06.6–
M06.8 will migrate or remove them. There are no compatibility adapters.

