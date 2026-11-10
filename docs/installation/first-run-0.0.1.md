# First run — vibecoder 0.0.1

Install with `sudo apt install ./vibecoder_0.0.1_amd64.deb`. The service starts
as `vibecoder` in `STANDBY/OFFLINE`; this is healthy and performs no remote
work. Add an administrator explicitly:

```bash
sudo usermod -aG vibecoder-admin "$USER"
# start a new login session
vibecoder status
vibecoder github login
vibecoder worker add codex
vibecoder worker codex-1 login
vibecoder worker codex-1 verify
cd ~/dev/project && vibecoder project add
```

`github login` reads the Fine-grained PAT from a hidden terminal prompt. Never
put it in argv or environment. The supported Codex adapter downloads the pinned
official 0.167.0 executable and companion after verifying their SHA-256; worker
credentials live in its private `CODEX_HOME`.

Backups are permission-protected and include credentials but are not encrypted;
exporting them is an explicit administrator responsibility. After restore,
inspect `vibecoder maintenance status`, reconcile remote effects, and clear the
specific barrier explicitly with `vibecoder maintenance clear-barrier ID`.

Diagnostics: `vibecoder doctor`, `vibecoder workflow status`, `vibecoder job
list`, and `journalctl -u vibecoder`. Backup requires `vibecoder pause`, then
`vibecoder backup create`; restore always establishes a recovery barrier.
