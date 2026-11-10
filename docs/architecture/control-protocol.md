# Local control protocol v1

Transport: Unix stream socket only. Frame: unsigned 32-bit big-endian body
length followed by UTF-8 JSON. Maximum body size: 1 MiB.

```json
{"version":1,"request_id":"uuid","operation":"status","arguments":{}}
{"version":1,"request_id":"uuid","ok":true,"result":{}}
{"version":1,"request_id":"uuid","ok":false,"error":{"code":"FORBIDDEN","message":"...","details":{}}}
```

Operations are `status`, `doctor`, `pause`, `resume`, `github.login`,
`github.status`, and `github.verify`. `github.login` accepts the PAT only inside
the authorised local connection and never returns it. Unknown
operations and unexpected arguments are rejected; no operation maps to a shell.

M06.4 adds `project.add`, `project.list`, `project.status`, `project.users`,
`project.authorize` and `project.deauthorize`. Mutations require an authorized
peer. The local checkout path does not cross the protocol and its observation
never replaces daemon-side GitHub validation.

M06.5 adds read operations `plugin.list`, `plugin.show`, `worker.list` and
`worker.status`, plus authorized mutations `worker.add`, `worker.enable` and
`worker.disable`. Responses contain descriptor, capability and diagnostic
metadata but no worker or GitHub secrets.

M06.6 adds `worker.login`, `worker.login-status`, `worker.login-cancel`,
`worker.logout` and `worker.verify`. Authentication is represented by a stable
operation ID so a Device Code wait does not monopolize the control server. Only
the public URL/code text can appear in responses; Codex credentials stay in the
worker-owned `CODEX_HOME`.

