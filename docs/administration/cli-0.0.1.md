# Unified CLI 0.0.1

Los workers nuevos usan `autonomous`. También están disponibles:

```bash
vibecoder worker add codex --approval-policy manual
vibecoder worker set codex-1 --approval-policy autonomous
vibecoder worker status codex-1
```

`autonomous` evita confirmaciones ordinarias dentro del sandbox; `manual`
conserva los comandos `approval list/show/approve/deny`.

```bash
vibecoder status [--json]
vibecoder doctor [--json]
vibecoder pause
vibecoder resume
vibecoder github login
vibecoder github status [--json]
vibecoder project add [path] [--branch BRANCH] [--ignore-dirty]
vibecoder project list [--long|--json]
vibecoder project PROJECT status [--json]
vibecoder project PROJECT users
vibecoder project PROJECT authorize LOGIN
vibecoder project PROJECT deauthorize LOGIN
vibecoder plugin list [--json]
vibecoder plugin show PLUGIN
vibecoder worker list [--long|--json]
vibecoder worker add PLUGIN [--name NAME]
vibecoder worker WORKER status [--json]
vibecoder worker WORKER enable
vibecoder worker WORKER disable
vibecoder worker WORKER login
vibecoder worker WORKER logout
vibecoder worker WORKER verify
vibecoder approval list [--pending] [--worker WORKER] [--json]
vibecoder approval show ID [--json]
vibecoder approval approve ID
vibecoder approval deny ID
vibecoder workflow status [--json]
vibecoder workflow poll PROJECT [--json]
vibecoder job list [--json]
vibecoder attempt list [--kind PLAN|TASK] [--operation-id ID] [--json]
vibecoder plan retry PROJECT ISSUE
vibecoder task retry TASK_ID
vibecoder maintenance status [--json]
vibecoder maintenance check [--json]
vibecoder maintenance reconcile [--json]
vibecoder maintenance clear-barrier ID
vibecoder backup create [--json]
vibecoder backup list [--json]
vibecoder backup verify ID [--json]
vibecoder backup restore ID [--json]
```

The CLI talks only to `vibecoderd` and never opens SQLite. Add administrators
explicitly, then start a new login session:

```bash
sudo usermod -aG vibecoder-admin "$USER"
```

GitHub login requires an interactive TTY and uses hidden input; tokens are not
accepted as arguments. Project discovery is read-only and registration is
daemon-owned. Codex login detects official Device Code support, reports progress
without passing credentials through the daemon, and requires the production
isolation boundary. Live authentication remains opt-in. The persistent
Issues-to-PR workflow and scheduler are integrated; use `workflow status`,
`workflow poll PROJECT` and `job list` for operational inspection.

Approval mutations require an interactive TTY and the exact confirmation word.
They apply to one provider action only; they never approve a session globally.

