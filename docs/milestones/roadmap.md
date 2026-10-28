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

## M02 — Bootstrap vertical

Repositorio vacío → Issue bootstrap → conversación principal persistente → aprobación humana → PR inicial → merge humano → `READY`.

## M03 — Protocolo y preguntas

`/agent`, `/reply`, comentarios, preguntas pendientes, reanudación de sesiones y control de concurrencia.

## M04 — Registro dinámico y especialistas

Definición versionada de roles, creación/retirada mediante PR, sesiones persistentes, asignación exclusiva por principal.

## M05 — Implementación y PR

Planes técnicos, worktrees/containers aislados, sesiones temporales, tests, integración, PR y ciclo de correcciones.

## M06 — Operación multicuenta

Asignación de cuentas, métricas de uso, agotamiento, espera y recuperación, Telegram y endpoint Prometheus.

## M07 — Robustez y escala

Workers distribuidos, políticas de scheduling, recuperación ante fallos, auditoría, permisos y pruebas de carga.

## Regla de entrega

Cada hito debe tener pruebas reproducibles, criterios de aceptación y documentación actualizada. No automatizar el merge.
