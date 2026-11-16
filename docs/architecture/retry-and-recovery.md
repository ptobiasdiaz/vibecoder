# Reintentos y recuperación

El fix M06.13 de vibecoder 0.0.1 separa la operación lógica (plan o tarea) de sus intentos de
proveedor. Cada intento posee número, ejecución, clasificación de fallo, base
Git y fechas propias. Los registros terminales nunca se sobrescriben.

## Clasificación

- `TRANSIENT`: cuota o rate limit confirmados. Se programa otro intento con
  backoff persistente de 15 minutos, 30 minutos, 1, 2, 4 y hasta 6 horas, con
  jitter determinista.
- `FUNCTIONAL`: el proveedor terminó, pero el trabajo no satisface el contrato.
  Requiere `task retry`.
- `PERMANENT`: autenticación, capacidad o configuración inválida.
- `AMBIGUOUS`: desconexión o efecto externo incierto. Permanece `UNKNOWN`, crea
  barrera y nunca genera otro intento hasta reconciliarse.

Las esperas sobreviven a reinicios mediante `eligible_at` y `next_retry_at`.

```bash
vibecoder attempt list --json
vibecoder plan retry propietario/proyecto 16
vibecoder task retry UUID-DE-TAREA
```

Los reintentos requieren administración local, conservan la aprobación del
plan y se auditan. Una petición repetida mientras existe un job pendiente es
idempotente. No se aceptan reintentos de `UNKNOWN`.

Antes de una tarea independiente se verifica el SHA actual de la rama base en
GitHub y se persiste como base inmutable del intento. Las tareas dependientes
parten del `result_sha` confirmado de sus predecesoras, no del HEAD remoto.

Con varias predecesoras, vibecoder no inventa una integración cuando sus resultados
no forman una base inequívoca.
