# ADR-0028 — Trusted declarative plugin registry

Plugin discovery consumes validated versioned descriptors and persists their
public metadata. It never imports provider runtime code. Runtime modules must
belong to an installed trusted vibecoder distribution and are loaded only after
the launcher changes security domain. Arbitrary entry points, marketplaces and
remote installers are excluded.

