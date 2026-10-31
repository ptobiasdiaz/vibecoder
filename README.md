# Codex GitHub Orchestrator — Diseño

Repositorio de especificación y futura implementación de un orquestador multi-repositorio y multiagente basado en Codex y GitHub.

## Propósito

Permitir que un equipo dirija proyectos mediante GitHub Issues. Un agente principal persistente analiza, clasifica y negocia requisitos. Tras aprobación humana, agentes especializados persistentes planifican y supervisan sesiones de implementación aisladas. El resultado se entrega mediante Pull Requests con revisión y merge humanos.

## Documentación

- [Arquitectura](docs/architecture/overview.md)
- [Modelo de agentes](docs/architecture/agents.md)
- [Modelo de datos](docs/architecture/data-model.md)
- [Protocolo de comentarios](docs/protocols/github-comments.md)
- [Eventos y estados](docs/workflows/lifecycle.md)
- [Bootstrap](docs/workflows/bootstrap.md)
- [Operación, cuotas y Telegram](docs/architecture/operations.md)
- [Decisiones arquitectónicas](docs/decisions/ADR-0001-foundations.md)
- [Hitos](docs/milestones/roadmap.md)

## Repositorios

1. **Este repositorio**: diseño, documentación y futura implementación del orquestador.
2. **Repositorio de prueba**: inicialmente vacío, se utilizará para validar el bootstrap de extremo a extremo.

## Estado

M00, M01, M02 y M03.2 están completados. M03.1 incorpora el adaptador REST de GitHub
y el registro persistente de repositorios. M03.2 valida la conversación de bootstrap
live mediante polling, agente principal persistente y aprobación atribuida. M03.3,
también validado live, incorpora workspaces Git aislados, generación inicial,
`.vibecoder/`, jerarquía `AGENTS.md`, validación y commits locales. M03.3.1 añade
configuración Git negociada, aprobación conjunta y detección de deriva. M03.4
implementa push recuperable, PR con merge exclusivamente humano y activación
verificada, y su flujo remoto completo está validado live. Sin webhooks, Telegram ni
despliegue. M04.1 incorpora gestión local persistente y auditada de especialistas,
configuración versionada integrada en Git, cuentas y sesiones compatibles; su ciclo
local de activación, persistencia, suspensión y reactivación está validado live. M04.2
añade coordinación persistente de Issues, consultas de especialistas, planes
estructurados y tareas aprobadas; el flujo está validado live hasta `MATERIALIZED`.
M04.3 añade ejecución individual autorizada, workspaces y sesiones por tarea,
validación, commits, ramas y Pull Requests recuperables; su validación live está
pendiente. M04.4 añade supervisión de reviews, conversación directa, correcciones
autorizadas sobre la misma rama y finalización tras merge humano; su validación live
se realizará conjuntamente con M04.3. M04.5 añade el supervisor continuo local con
planificación persistente, leases, límites, pausas, recuperación, dry-run y CLI; está
validado offline y su escenario live integral queda pendiente. Véanse
[M00](docs/milestones/M00.md), [M01](docs/milestones/M01.md) y
[M02](docs/milestones/M02.md), [M04.1](docs/milestones/M04.1.md) y
[M04.3](docs/milestones/M04.3.md), [M04.4](docs/milestones/M04.4.md) y
[M04.5](docs/milestones/M04.5.md).

## Supervisor continuo

```bash
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json config-check
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json dry-run
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json run
```

Las publicaciones remotas están deshabilitadas por defecto. El token GitHub se obtiene
de una variable de entorno y nunca se almacena en el archivo de configuración.
