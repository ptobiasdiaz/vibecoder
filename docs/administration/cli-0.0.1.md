# Unified CLI 0.0.1

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
isolation boundary. Live authentication remains opt-in. Agents and workflow
scheduling are not yet integrated.

Approval mutations require an interactive TTY and the exact confirmation word.
They apply to one provider action only; they never approve a session globally.

