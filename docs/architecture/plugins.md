# Plugins and workers

Plugins are trusted, versioned integrations described by declarative JSON.
Discovery reads and validates descriptors without importing provider code into
`vibecoderd`. Versioned descriptors are immutable once persisted.

A worker is a configured instance of one plugin. It has a unique name, stable
ID, private Unix identity, private storage, verified capabilities, concurrency
limit and lifecycle state. Agents remain project identities and do not belong
to a provider.

The included descriptors are:

- `fake@1.0.0`, test-only and executable through the normal runtime contract.
- `codex@1.1.0`, implemented behind the isolated runtime boundary. It requires
  a verified `codex-cli 0.167.0`, worker Unix identity and private `CODEX_HOME`.

Only adapters included in vibecoder or installed by trusted managed packages
are accepted. There is no marketplace or arbitrary Python download/loading.

Worker states are `INSTALLING`, `AUTHENTICATION_REQUIRED`, `VERIFYING`,
`READY`, `UNAVAILABLE`, `DISABLED` and `ERROR`. READY follows a successful
probe; database presence alone never makes a worker usable.

Codex installation is manifest-driven and its App Server protocol remains
provider-private. See [codex-plugin.md](codex-plugin.md).

