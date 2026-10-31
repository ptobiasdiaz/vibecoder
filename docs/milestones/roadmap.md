# Hoja de ruta por hitos

## M00 — Validación técnica

Verificar Codex App Server/CLI: crear y reanudar sesión, recuperar salida, observar uso/límites disponibles, reiniciar proceso, autenticación por cuenta y comportamiento de errores. Documentar limitaciones reales.

**Estado (2026-10-28): COMPLETADO.** Cliente, diagnóstico, unitarias e integración
opt-in implementados. Confirmados App Server 0.163.0, handshake, creación, aislamiento,
turno real y reanudación después de reiniciar. Un thread sin primer turno no es
persistente; uno con un turno real completado sí lo es. Concurrencia y cancelación
live, límites por método de autenticación y recuperación de credenciales quedan como
validaciones posteriores no bloqueantes. Véase [M00](M00.md).

## M01 — Núcleo persistente del orquestador

**Estado (2026-10-28): COMPLETADO.** Biblioteca Python independiente de interfaces,
modelo persistente de cuentas/proyectos/agentes/sesiones/tareas, SQLite con migraciones,
máquinas de estados, intentos, errores estructurados, idempotencia y recuperación
conservadora. FastAPI, Docker, GitHub y colas externas quedan fuera del alcance vigente
de M01. Véase [M01](M01.md).

## M02 — Ejecución persistente de agentes Codex

**Estado (2026-10-28): COMPLETADO.** Worker local secuencial, adaptador App Server,
persistencia de correlaciones y resultados, clasificación estructurada y recuperación
conservadora implementados. La integración real confirmó un turno completo, la
persistencia del resultado y la reanudación de la sesión tras reiniciar el worker y
App Server. Se corrigió la incompatibilidad de `thread/start.sandbox`
(`workspaceWrite` frente al valor oficial `workspace-write`). Permanecen fuera de
alcance el scheduler distribuido y los approvals interactivos; la reconciliación se
limita a 50 turnos, y concurrencia y cancelación live siguen pendientes. Véase
[M02](M02.md).

## M03 — GitHub, bootstrap, protocolo y recuperación

### M03.1 — Adaptador GitHub y registro de repositorios

**Estado (2026-10-28): COMPLETADO; LECTURA LIVE VALIDADA.** Adaptador
REST, autenticación externa, registro por ID estable, repositorios vacíos, Issues,
comentarios, etiquetas, persistencia e idempotencia con reconciliación local/remota.
Sin webhooks ni ejecución de agentes. Véase [M03.1](M03.1.md).

### M03.2 — Bootstrap persistente

**Estado (2026-10-29): COMPLETADO; FLUJO LIVE VALIDADO.** Issue único, agente
principal y sesión persistentes, polling autorizado, ejecución mediante M02,
publicación idempotente, recuperación conservadora y aprobación atribuida por evento.
Se validó en GitHub real una intervención humana, su turno Codex persistido y la
respuesta publicada. La segunda intervención live tras reiniciar sigue pendiente de
observación. Sin generación de código ni Pull Requests. Véase [M03.2](M03.2.md).

### M03.3 — Workspaces Git y generación inicial

**Estado (2026-10-29): COMPLETADO; FLUJO LIVE VALIDADO.** Workspaces Git aislados,
repositorios con historia o vacíos, rama bootstrap, generación Codex mediante M02,
estructura `.vibecoder/`/`doc/`, jerarquía `AGENTS.md`, validación y commit local. La
prueba real confirmó generación, commit local y recuperación tras reabrir la
persistencia. Sin push ni Pull Requests. Véase [M03.3](M03.3.md).

### M03.3.1 — Configuración Git negociada y aprobada

**Estado (2026-10-29): COMPLETADO; FLUJO LIVE VALIDADO.** Propuesta estructurada,
confirmación humana, aprobación conjunta atribuida, hash reproducible, snapshot remoto
y consumo obligatorio por generaciones nuevas. La prueba real confirmó negociación,
persistencia y compatibilidad remota de la configuración aprobada. Las generaciones
históricas se preservan sin ampliar su autorización. Sin push ni Pull Requests. Véase
[M03.3.1](M03.3.1.md).

### M03.4 — Publicación y Pull Request de bootstrap

**Estado (2026-10-30): COMPLETADO; FLUJO REMOTO LIVE VALIDADO.** Publicación
recuperable, base técnica para remotos vacíos, push verificado, Pull Request
idempotente, polling de merge humano y activación persistente. La prueba real confirmó
el primer push, la rama predeterminada, la publicación bootstrap, el PR, el merge
manual y la activación. Nunca hace force push ni merge. Webhooks, `/reply` y eventos
generales permanecen posteriores. Véase [M03.4](M03.4.md).

## M04 — Registro dinámico y especialistas

### M04.1 — Definición y ciclo de vida

**Estado (2026-10-30): COMPLETADO; VALIDACIÓN LIVE LOCAL SUPERADA.** Identidad
persistente, propuestas y revisiones versionadas, integración obligatoria de
instrucciones, cuentas por agente, sesiones compatibles, auditoría y ciclo de
suspensión/reactivación validados sobre el proyecto de pruebas activado por M03.4. No
negocia por GitHub ni ejecuta tareas especializadas. Véase [M04.1](M04.1.md).

### M04.2 y siguientes

**M04.2 — Estado (2026-10-31): COMPLETADO; FLUJO LIVE VALIDADO.** Descubrimiento y
conversación real de Issues, consultas del principal y de especialistas, planes
versionados, aprobación no reutilizable, snapshot y tareas materializadas con
ejecución bloqueada. La recuperación confirmó el workflow `MATERIALIZED`, las tareas
`PENDING` y la ausencia de intentos. Véase [M04.2](M04.2.md).

### M04.3 — Ejecución especializada y Pull Requests

**Estado (2026-10-31): COMPLETADO; VALIDACIÓN OFFLINE Y LIVE SUPERADAS.**
Ejecución ligada a planes aprobados, dependencias por merge verificado, workspace y
sesión por tarea, validación, commit y publicación/PR idempotentes y recuperables. No
fusiona ni supervisa reviews. El escenario integral ejecutó dos tareas dependientes y
demostró que la segunda no se prepara antes del merge de la primera. Véase
[M04.3](M04.3.md).

### M04.4 — Supervisión de PRs y correcciones

**Estado (2026-10-31): COMPLETADO; VALIDACIÓN OFFLINE Y LIVE SUPERADAS.** Polling
separado de comentarios/reviews, conversaciones por agente,
correcciones explícitamente autorizadas, commits/push sobre la misma rama y merge
humano verificado. No fusiona ni cierra Issues. Véase [M04.4](M04.4.md).

### M04.5 — Operación continua

**Estado (2026-10-31): COMPLETADO; VALIDACIÓN OFFLINE Y LIVE INTEGRAL SUPERADAS.**
Supervisor monoproceso, scheduler derivado de workflows, dispatcher sobre
servicios existentes, jobs y leases persistentes, límites multidimensionales, pausas,
backoff, recuperación conservadora, dry-run y apagado ordenado. No añade autorización,
merge ni cierre automático. Véase [M04.5](M04.5.md), el
[procedimiento](M04-live-validation.md) y los [resultados live](M04-live-results.md).

## M05 — Implementación y PR

Planes técnicos, worktrees/containers aislados, sesiones temporales, tests, integración, PR y ciclo de correcciones.

## M06 — Operación multicuenta

Asignación de cuentas, métricas de uso, agotamiento, espera y recuperación, Telegram y endpoint Prometheus.

## M07 — Robustez y escala

Workers distribuidos, políticas de scheduling, recuperación ante fallos, auditoría, permisos y pruebas de carga.

## Regla de entrega

Cada hito debe tener pruebas reproducibles, criterios de aceptación y documentación actualizada. No automatizar el merge.
