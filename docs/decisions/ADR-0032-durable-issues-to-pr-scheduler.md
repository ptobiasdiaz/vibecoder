# ADR-0032 — Durable provider-neutral Issues-to-PR scheduler

Accepted. Authorized remote text is filtered before agent-visible persistence.
Plans are canonical JSON immutable revisions approved by exact SHA-256. Local
jobs use leases; intent records precede every GitHub/Git effect. Confirmed-safe
polls may retry after lease expiry, while task/provider uncertainty becomes
UNKNOWN and blocks automatic repetition. The core alone owns Git and GitHub;
there is deliberately no merge operation.
