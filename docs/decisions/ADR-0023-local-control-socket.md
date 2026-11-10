# ADR-0023: Local control socket and Unix authorisation

Status: accepted.

`vibecoderd` is the sole owner of persistent mutations. The CLI uses a Unix
stream socket at `/run/vibecoder/control.sock`; there is no administrative TCP
listener. JSON messages use a four-byte network-order length, a 1 MiB limit,
protocol version and request ID.

The daemon obtains PID, UID and primary GID from Linux `SO_PEERCRED` and reads
supplementary groups from matching `/proc/<pid>/status`. Root and members of
`vibecoder-admin` may mutate state. JSON identity fields have no authority. The
socket starts under umask 0077 before receiving final mode `0660`.

