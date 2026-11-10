# ADR-0030 — Provider sessions and conservative execution recovery

Session and execution references are opaque. Intent and idempotency key are
stored before invoking a runtime. A terminal provider result is persisted once.
An interrupted PENDING/RUNNING execution becomes UNKNOWN after restart and is
never reissued. Reconciliation requires both an opaque reference and a verified
RECONCILIATION capability; otherwise UNKNOWN remains blocked. Cancellation is
also capability-gated.

