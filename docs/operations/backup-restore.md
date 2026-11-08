# Backup y restauración

## Preparación y creación

Detenga el servicio y establezca una pausa persistente antes del backup:

```bash
sudo vibecoder-admin service pause --kind all --reason maintenance
sudo systemctl stop vibecoder.service
sudo vibecoder-admin backup create --offline
sudo vibecoder-admin backup list
sudo vibecoder-admin backup verify BACKUP_ID
```

`--offline` es una afirmación administrativa explícita; no se deduce de la ausencia de
un PID. La operación rechaza jobs `RUNNING`, leases y ejecuciones de dominio activas.

El formato 1 contiene `manifest.json` y `payload/`. El manifiesto registra versión,
fecha, runtime, esquema, componentes, tamaños, modos y SHA-256. No contiene secretos.

## Componentes

- `/etc/vibecoder/supervisor.json` y `accounts.json`.
- SQLite consistente mediante `sqlite3.Connection.backup`.
- Secretos.
- Directorios de cuentas/`CODEX_HOME`.
- Workspaces Git completos.
- Estado adicional.

No incluye `/run/vibecoder`, journald, cachés, backups previos ni recovery.

## Restauración

```bash
sudo systemctl stop vibecoder.service
sudo vibecoder-admin --yes backup restore BACKUP_ID --offline
sudo vibecoder-admin maintenance status
sudo vibecoder-admin doctor
```

La restauración verifica antes de escribir, crea un backup pre-restore, usa staging y
journal, y termina en `CONFIGURED` con pausa global. No inicie operaciones remotas
hasta reconciliar GitHub/Codex y activar explícitamente.

Los backups no están cifrados. Manténgalos en almacenamiento local protegido; su
exportación y cifrado con herramientas aprobadas son decisiones externas explícitas.
