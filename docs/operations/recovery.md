# Recuperación operativa

Diagnóstico seguro:

```bash
vibecoder status
vibecoder doctor
vibecoder job list --json
vibecoder attempt list --json
vibecoder maintenance status
vibecoder maintenance reconcile
```

Los fallos de cuota confirmados quedan `WAITING_RETRY`; `eligible_at` y
`next_retry_at` sobreviven al reinicio. Los fallos funcionales pueden
recuperarse explícitamente:

```bash
vibecoder plan retry PROJECT ISSUE
vibecoder task retry TASK_ID
```

Un reintento crea otra ejecución e idempotency key, preserva el intento anterior
y no repite publicaciones ya confirmadas. Los estados `UNKNOWN` o efectos
externos inciertos no admiten reintento: deben reconciliarse y mantienen una
barrera cuando corresponde.

Después de un restore, la barrera de recuperación tampoco se levanta mediante
`resume`. Verifique el estado externo y utilice exclusivamente los comandos de
mantenimiento. No edite SQLite ni elimine journals o barreras manualmente.
