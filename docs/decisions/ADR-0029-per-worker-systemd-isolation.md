# ADR-0029 — Per-worker Unix identity and systemd boundary

Production workers use a distinct pre-provisioned Unix account and a transient
systemd unit with fixed hardening. A generic setuid helper is rejected. The
daemon validates the identity and paths but cannot create accounts itself.
Only trusted descriptor modules can be launched. The direct subprocess launcher
is restricted to test-only plugins and is not a security boundary.

M06.6.1 replaced the provisional sudo path with a root launcher service that
accepts only a validated launch request over a private Unix socket. Debian Sid
validation confirmed separate effective UIDs and denial of real reads against
SQLite, global secrets, the administrative socket and other workers. Network
isolation is not claimed.

