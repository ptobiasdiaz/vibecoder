# Backup y restauración

> **Contrato vigente 0.0.1:** use `vibecoder pause`, `vibecoder backup create`,
> `vibecoder backup verify ID` y `vibecoder backup restore ID`. Los storages de
> workers se copian mediante el launcher privilegiado de política fija sin
> hacerlos legibles por el daemon en operación normal. Todo restore activa una
> barrera; `resume` nunca la elimina. El texto M05 inferior es evidencia
> histórica de la línea retirada 0.4.0.

M05.5/M05.5.1 verificaron el procedimiento con systemd detenido en Debian trixie,
Debian sid y, de forma experimental, Ubuntu 24.04, incluidos propietarios, barrera
y rollback de una sustitución interrumpida.

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
