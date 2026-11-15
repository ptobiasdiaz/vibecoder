# Worker isolation threat model

## Implemented production policy

`SystemdWorkerLauncher` starts only a module named by a validated trusted
descriptor. A transient service changes to a pre-provisioned per-worker Unix
user before Python loads provider code and applies:

- `NoNewPrivileges=yes`
- empty `CapabilityBoundingSet`
- `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`
- `LockPersonality=yes`, `UMask=0077`
- `InaccessiblePaths` for global secrets, SQLite, control socket, all worker
  storage and all workspaces
- a more specific `ReadWritePaths` only for that worker storage and the
  workspace assigned to the request
- a minimal environment and `close_fds=True`

The daemon accepts only pre-provisioned accounts distinct from root, the daemon
UID and the administrative group. Storage and temp directories must be owned by
that worker and have no group/other permissions.

This provides a design boundary for filesystem, process identity and
credentials. Network is intentionally not disabled at the unit level because
real providers need network access; Codex independently denies tool network in
its sandbox policy.

On systemd 262, `RestrictSUIDSGID=yes` makes bubblewrap fail before it can
construct Codex's `workspace-write` sandbox. M06.11 therefore omits that one
setting from transient worker units. `NoNewPrivileges` and the empty capability
set still prevent setuid/setgid privilege gain, while retaining the inner
sandbox enforces workspace and tool-network policy. The daemon and fixed
launcher units keep their own hardening.

## Offline-test boundary

`DirectProcessLauncher` runs the same protocol under the test UID. It verifies
framing, recovery and absence of inherited environment/file descriptors, but
is explicitly marked `security_boundary = False` and is accepted only for a
`test_only` plugin. It does **not** prove Unix isolation.

## Pending Debian validation

M06.9 must provision real per-worker users and private directories, authorize
the daemon to create only the fixed transient units, and demonstrate under
systemd that workers cannot read the PAT, SQLite, the administrative socket,
another worker's storage or an unassigned workspace. Until then these are
implemented policy, not live-validated guarantees.

