# Plugin Codex de vibecoder 0.0.1

El adaptador Codex está fuera del núcleo. `vibecoderd` persiste intenciones y
referencias opacas; `vibecoder.codex_plugin_runtime` es el único componente que
interpreta threads, turns y `CODEX_HOME`.

La versión validada para M06.6.1 es `codex-cli 0.167.0`, publicada oficialmente
para Linux x86_64. El probe no consume inferencia: comprueba ejecutable,
autenticación y negociación `initialize` del App Server.

Referencias oficiales consultadas:

- [Codex App Server y JSONL](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
- [Codex como plataforma](https://developers.openai.com/blog/codex-as-a-platform)

La documentación consultada no garantiza una URL estable de binario standalone
ni documenta universalmente `login --device-auth`; por eso ambos se verifican
antes de usarse y no se presuponen.

El protocolo usado es JSONL sobre stdio: `initialize`/`initialized`,
`thread/start` o `thread/resume`, `turn/start`, deltas y `turn/completed`. Solo
`status=completed` confirma éxito. Desconexión, timeout o terminación sin ese
evento producen `UNKNOWN`. Observación y cancelación no se anuncian hasta una
verificación live de su semántica terminal.

## Instalación, autenticación y aislamiento

Un binario del sistema solo se acepta desde una ruta fija, propiedad de root,
no escribible por grupo/otros, sin symlinks y con versión compatible. La
instalación gestionada exige manifiesto local con URL HTTPS, versión y SHA-256;
descarga a staging y publica atómicamente. M06.6 no inventa una URL: M06.9 debe
distribuir el manifiesto después de validar el artefacto oficial.

Cada worker usa usuario Unix, HOME y `CODEX_HOME` privados. Device Code solo se
ofrece si `codex login --help` anuncia `--device-auth`. El login es una operación
persistida y asíncrona; los secretos no atraviesan daemon, SQLite ni el protocolo
administrativo. `DirectProcessLauncher` nunca permite un Codex productivo.

## Límites de la validación

- No hubo descarga, login, inferencia ni App Server live.
- No está integrado aún el flujo de solicitudes de aprobación del App Server.
- La efectividad de UID, namespaces, cancelación y reinicio se valida en M06.9.
- Los threads se materializan perezosamente en el primer turno.
