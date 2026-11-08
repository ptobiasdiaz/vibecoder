# Recuperación de mantenimiento

El journal está en `/var/lib/vibecoder/recovery/restore-journal.json`. Si una
restauración queda en `FAILED`, `PREPARED` o `SWAPPING`, el runtime debe permanecer
detenido y bloqueado.

Diagnóstico:

```bash
sudo vibecoder-admin maintenance status
sudo vibecoder-admin maintenance check
```

Rollback al backup creado justo antes de restaurar:

```bash
sudo vibecoder-admin maintenance reconcile --rollback --offline
```

El rollback también deja `CONFIGURED` y pausa global. Después se deben reconciliar
jobs UNKNOWN, PRs, pushes, comentarios, merges y sesiones con los servicios M03/M04.
No se repite inferencia ni publicación por el simple hecho de restaurar o expirar un
lease.

Una migración fallida deja el paquete sin configurar y conserva el backup
pre-migración. No edite `schema_migrations` manualmente ni elimine el journal para
forzar el arranque.
