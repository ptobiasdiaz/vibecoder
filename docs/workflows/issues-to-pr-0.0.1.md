# Authorized Issues-to-PR workflow

The scheduler polls enabled projects. Issue and review text is retained and
shown to workers only when the numeric GitHub author ID is authorized for that
project. Rejected events retain metadata, never bodies.

An authorized Issue schedules an immutable JSON plan. Vibecoder publishes the
revision and SHA-256 with this exact approval command:

```text
/vibecoder approve PLAN_SHA256
```

Only that revision is materialized into a dependency DAG. Each task uses a
daemon-created workspace and provider-neutral worker session. The worker has no
GitHub PAT. After confirmed completion, the core commits, pushes a controlled
branch and creates one idempotent PR. Authorized review feedback may create a
correction; merges are only observed and verified, never performed.

Any uncertain provider or remote effect becomes `UNKNOWN`, creates a recovery
barrier where appropriate and is not blindly retried.
