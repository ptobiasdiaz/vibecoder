# Codex approvals live validation

Date: 2026-11-10. Host: authorized Debian Sid VM `192.168.56.6`. Codex:
official `0.167.0`. No GitHub repository or credential was used.

## Protocol evidence

Generated 0.167.0 schemas and a raw deny-only probe confirmed:

- methods `item/commandExecution/requestApproval`,
  `item/fileChange/requestApproval`, and `item/permissions/requestApproval`;
- command request fields include thread, turn, item, command, cwd, kind,
  reason, available decisions and optional amendments;
- command/file decisions include one-time `accept` and `decline`;
- `serverRequest/resolved` follows a handled request;
- a denied command item completes with status `declined`.

## Results

| Test | Result | Evidence |
|---|---|---|
| Real request received and persisted | PASS | command request correlated to worker/session/execution |
| Explicit deny | PASS | state `DENIED`; target file absent |
| Explicit approve | PASS | state `APPROVED`; exact file content `approval-test-approve` |
| No response | PASS | state `EXPIRED`; denial observed by turn; file absent |
| Daemon restart while pending | PASS | approval and execution `UNKNOWN`; file absent; active count zero |
| Duplicate/contradictory decisions | PASS offline | idempotent same decision; conflict on opposite decision |
| Local authorization | PASS | admin group accepted; unprivileged user rejected |
| Isolation regression | PASS | PAT, SQLite, control socket, foreign storage/workspace denied |
| Assigned workspace | PASS | worker could read/write only assigned workspace |

The approved file was mode `0600` and owned by the isolated Codex worker UID.
There were no residual transient worker units after the restart test.

## Discovered prerequisite

The original standalone binary lacked `codex-code-mode-host`; App Server
reported that Code Mode failed closed and emitted no command item. The matching
official x86_64 musl archive was installed beside Codex after verifying the
published archive SHA-256
`b5074d5dd64643b2c073927e494a588eff31c1f5a27f12c1e2c45f9a6cf07dd4`.
The runtime probe now refuses READY when that helper is absent or unsafe.

## Cleanup

The authenticated `CODEX_HOME` was preserved. Test approval rows remain as
non-secret audit evidence. The single approved workspace file is harmless test
data. No GitHub state was touched.

