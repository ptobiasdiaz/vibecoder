# Worker runtime protocol v1

The daemon launches a worker process and exchanges one length-prefixed UTF-8
JSON request over stdin/stdout. Frames have a 4-byte big-endian length and a
4 MiB maximum. Every message contains protocol version, request ID and worker
ID. Responses are correlated and typed.

Operations currently exercised by the fake runtime are:

- `probe`
- `create_session`
- `continue_session`
- `execute`
- `observe`
- `cancel`

This is not the administrative protocol. A worker cannot request SQL, GitHub,
project administration or arbitrary daemon operations. Requests contain only
provider context, opaque references and an assigned workspace path when needed.
They never contain the global PAT.

Runtime approvals use a separate peer-authenticated Unix socket and the bounded
operations `approval.register`, `approval.claim`, and `approval.confirm`.
This broker is deliberately not the administrative socket. Provider payloads
are validated as untrusted data and cannot invoke arbitrary daemon operations.

Execution intent is persisted before launch. Process crash, timeout or daemon
restart produces `UNKNOWN`; a new inference is not started automatically.
The same rule applies to an approval whose delivery was not confirmed.

