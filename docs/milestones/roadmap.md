# Roadmap — vibecoder 0.0.1

M00–M05 document the retired 0.4.0 product line. M06 is an intentional clean
break and does not provide data, API, CLI, or package compatibility.

| Milestone | Scope | State |
|---|---|---|
| M06.1 | Neutral domain, plugin contracts, schema v1, state and resolution | Implemented offline |
| M06.2 | Daemon, Unix control socket and unified CLI | Implemented offline |
| M06.3 | Global GitHub identity and PAT diagnostics | Implemented offline |
| M06.4 | Read-only local discovery and project registration | Implemented offline |
| M06.5 | Plugin registry and generic workers | Implemented offline |
| M06.6 | Codex plugin, managed runtime and authentication | Implemented; M06.6.1 live validated on Debian Sid |
| M06.6.2 | Codex runtime approvals and live mediation | Implemented and live validated on Debian Sid |
| M06.7 | Provider-neutral Issues, plans, tasks, PRs and reviews | Pending |
| M06.8 | Supervisor integration, recovery and maintenance | Pending |
| M06.9 | Debian package 0.0.1 and isolated systemd validation | Pending |
| M06.10 | Opt-in Breakout live acceptance | Pending |

M06 is complete only after M06.10 has evidence for the end-to-end flow.

M06.6.1 validó daemon, AF_UNIX, aislamiento systemd por UID, Codex 0.167.0,
Device Code y continuidad real. GitHub live y la mediación humana de
aprobaciones de ejecución Codex quedaron validadas en M06.6.2. Las aprobaciones
de planes multiagente siguen perteneciendo a M06.7.
