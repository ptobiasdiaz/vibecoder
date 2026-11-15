# vibecoder 0.0.1

Vibecoder es un orquestador persistente de desarrollo asistido por IA. Observa
Issues de GitHub, genera planes inmutables, espera su aprobación humana,
distribuye las tareas entre workers aislados y publica ramas y Pull Requests.
Los reviews y merges siguen siendo exclusivamente humanos.

La versión 0.0.1 incluye:

- daemon y CLI local protegida por socket Unix;
- una identidad GitHub global mediante Fine-grained PAT;
- registro de proyectos desde repositorios Git locales sin modificarlos;
- autorizaciones por proyecto y filtrado de texto no autorizado;
- plugin Codex 0.167.0 con Device Code y `CODEX_HOME` aislado;
- workers con UID propio y políticas `autonomous` o `manual`;
- planes aprobados por hash, DAG de tareas, scheduler y recuperación;
- commits, ramas, PRs, correcciones de review y detección de merge humano;
- backup/restore y paquete Debian con systemd.

`autonomous` permite a Codex leer, escribir y ejecutar tests dentro del
sandbox asignado sin confirmaciones ordinarias. No significa acceso
irrestricto: no concede red, privilegios, PAT GitHub, SQLite ni merge.

## Compatibilidad

- **Debian 13 trixie amd64:** plataforma soportada.
- **Debian sid amd64:** validación continua sobre una fotografía concreta.
- **Ubuntu y otras distribuciones/arquitecturas:** no validadas para 0.0.1.

El paquete requiere systemd, Python 3.11 o posterior, Git y conectividad HTTPS.

## Inicio rápido

```bash
sudo apt install ./builds/vibecoder_0.0.1_amd64.deb
sudo usermod -aG vibecoder-admin "$USER"
# cierre la sesión Linux y vuelva a entrar

vibecoder github login
vibecoder worker add codex
vibecoder worker codex-1 login
vibecoder worker codex-1 verify

cd ~/dev/mi-proyecto
vibecoder project add
vibecoder status
```

La instalación arranca de forma segura en `STANDBY/OFFLINE`. Pasa a
`STANDBY/ONLINE` al configurar GitHub y a `READY/ONLINE` cuando existe al menos
un worker operativo. No existe una activación manual global.

## Documentación

- [Tutorial de instalación y primer proyecto](doc/tutorial.md)
- [Notas de versión 0.0.1](doc/release-notes-v0.0.1.md)
- [Arquitectura](docs/architecture/vibecoder-0.0.1.md)
- [Instalación Debian](docs/installation/debian.md)
- [Fine-grained PAT](docs/github/fine-grained-pat.md)
- [CLI](docs/administration/cli-0.0.1.md)
- [Workflow Issues → PR](docs/workflows/issues-to-pr-0.0.1.md)
- [Plugin Codex](docs/architecture/codex-plugin.md)
- [Aislamiento de workers](docs/security/worker-isolation.md)
- [Políticas de aprobación](docs/architecture/worker-approval-policies.md)
- [Backup y restore](docs/operations/backup-restore.md)
- [Roadmap](docs/milestones/roadmap.md)

## Limitaciones conocidas

El artefacto publicado es `amd64`; no existe interfaz web ni marketplace de
plugins. Codex 0.167.0 es la versión validada. Las operaciones `UNKNOWN`
requieren reconciliación y nunca se reintentan a ciegas. Telegram y los canales
alternativos de aprobación quedan fuera de 0.0.1 y están previstos para 0.0.2.

La línea 0.0.1 no es compatible con bases, configuración o entrypoints de
vibecoder 0.4.0. Los documentos M00–M05 permanecen únicamente como evidencia
histórica.
