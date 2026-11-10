# vibecoder 0.0.1

Vibecoder is a provider-neutral, persistent multi-agent orchestrator. Version
0.0.1 is a clean architectural break from 0.4.0: databases, configuration,
entry points and packaging from the old line are not supported.

M06.1–M06.6 provide the neutral domain, SQLite schema 1, local daemon and CLI,
a protected global GitHub identity, recoverable project registration, and a
provider-neutral plugin/worker runtime, and an offline-validated Codex adapter.
Codex installation, authentication and execution live remain explicitly opt-in.

```bash
cd ~/dev/breakout
vibecoder project add
vibecoder project list
```

- [Current architecture](docs/architecture/vibecoder-0.0.1.md)
- [M06.1](docs/milestones/M06.1.md)
- [M06.2](docs/milestones/M06.2.md)
- [M06.3](docs/milestones/M06.3.md)
- [M06.4](docs/milestones/M06.4.md)
- [M06.5](docs/milestones/M06.5.md)
- [M06.6](docs/milestones/M06.6.md)
- [Codex plugin](docs/architecture/codex-plugin.md)
- [Plugins and workers](docs/architecture/plugins.md)
- [Worker isolation](docs/security/worker-isolation.md)
- [Project registration](docs/projects/registration.md)
- [Fine-grained PAT](docs/github/fine-grained-pat.md)
- [Roadmap](docs/milestones/roadmap.md)
- [Agent instructions](AGENTS.md)

Historical M00–M05 documents remain as engineering evidence and are not the
current product contract.
