# Runtime approvals

Runtime approvals are provider-neutral, per-action human decisions. They are
not M06.7 plan approvals. Codex-specific request and response shapes stay in
the isolated plugin runtime.

## Flow and trust boundaries

1. The provider emits a server request while a turn remains active.
2. The worker sends a bounded `approval.register` frame to the dedicated
   `/run/vibecoder/runtime-approval.sock` broker.
3. The daemon verifies the worker with `SO_PEERCRED`, correlates worker,
   session and execution, and persists the request before exposing it.
4. An authenticated local administrator records one `APPROVE` or `DENY`
   decision through the control socket.
5. The worker polls `approval.claim`, returns the provider-specific response,
   and reports confirmation only after a subsequent App Server event proves
   that the connection continued.

The worker cannot select an actor, write SQLite, use the administrative socket,
or mark a request approved. The administrative client cannot provide provider
references. Details are a bounded, redacted display subset.

## States

`PENDING` -> `DECISION_RECORDED` -> `DELIVERING` -> `APPROVED` or `DENIED`.
An elapsed undecided request becomes `EXPIRED` and receives a one-action
denial when the original connection remains usable. Any unconfirmed request
present when the daemon restarts becomes `UNKNOWN`; it is never replayed to a
different execution. Approval means only that one action was authorized, not
that the command or the complete turn succeeded.

The default policy is deny. There is no session-wide approval, automatic
approval, or sandbox-disable decision.

## Codex 0.167.0 mapping

- `item/commandExecution/requestApproval`: `accept` / `decline`.
- `item/fileChange/requestApproval`: `accept` / `decline`.
- `item/permissions/requestApproval`: the requested turn-scoped permission
  profile, or an empty turn-scoped profile on denial.

The client advertises `experimentalApi` and `requiredHumanApproval`, starts
threads with `approvalsReviewer=user`, `approvalPolicy=on-request`, and a
`read-only` Codex sandbox. The outer systemd boundary remains authoritative.

