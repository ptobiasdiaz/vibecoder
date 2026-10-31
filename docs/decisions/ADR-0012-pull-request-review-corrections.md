# ADR-0012 — Reviews y correcciones como workflows autorizados

## Estado

Aceptada para M04.4.

## Decisión

Los comentarios generales, reviews y comentarios de línea se consultan y persisten
como flujos remotos distintos. Su identidad es `(PR, tipo, ID remoto)`; no se intenta
construir un orden causal único entre endpoints.

Una observación puede producir conversación, pero nunca autoriza por sí sola una
modificación. Cada corrección es una entidad persistente vinculada a eventos concretos
y requiere clasificación explícita `IN_SCOPE` y autorización de operador. Los valores
`OUT_OF_SCOPE` y `AMBIGUOUS` bloquean la corrección y vuelven al principal/humano.

Las correcciones reutilizan workspace, rama y sesión de implementación solo si agente,
cuenta, configuración y SHA local/remoto siguen siendo compatibles. Se crea una tarea
controlada `REVIEW_CORRECTION`; no se reabre la tarea de generación original.

El estado integrado es `TaskExecutionWorkflow.MERGED`. La tarea M01 representa la
finalización del turno Codex y ya está `COMPLETED`; el merge verificado es la autoridad
que desbloquea dependencias. Vibecoder nunca fusiona ni cierra el Issue.

