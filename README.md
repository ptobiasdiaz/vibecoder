# vibecoder 0.0.1

Vibecoder is a provider-neutral, persistent multi-agent orchestrator. Version
0.0.1 is a clean architectural break from 0.4.0: databases, configuration,
entry points and packaging from the old line are not supported.

M06.1–M06.9 provide the neutral domain, SQLite schema 1, local daemon and CLI,
a protected global GitHub identity, recoverable project registration, isolated
workers, Codex App Server, human approvals, the Issues-to-PR workflow, a
persistent scheduler, local recovery and Debian packaging.

```bash
sudo apt install ./builds/vibecoder_0.0.1_amd64.deb
sudo usermod -aG vibecoder-admin "$USER"   # vuelva a iniciar sesión
vibecoder github login
vibecoder worker add codex
vibecoder worker codex-1 login
cd ~/dev/breakout && vibecoder project add
```

Only GitHub users authorized on a project can provide instructions or approve
an exact plan revision. Vibecoder can push task branches and open pull requests;
reviews and merges remain exclusively human.

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
- [First run](docs/installation/first-run-0.0.1.md)
- [Issues to PR](docs/workflows/issues-to-pr-0.0.1.md)
- [Agent instructions](AGENTS.md)

Historical M00–M05 documents remain as engineering evidence and are not the
current product contract.
