# Backup y restauración en vibecoder 0.0.1

El daemon crea backups coherentes de SQLite, secret store, configuración,
workspaces y storages de workers. Los storages se copian mediante el launcher
de política fija sin hacerlos legibles por el daemon durante la operación
normal.

Los backups contienen credenciales sin cifrar. Su directorio tiene permisos
restrictivos, pero cualquier exportación debe cifrarse y custodiarse fuera de
vibecoder.

## Crear y verificar

Active una pausa persistente para impedir trabajo nuevo:

```bash
vibecoder pause
vibecoder backup create
vibecoder backup list
vibecoder backup verify BACKUP_ID
vibecoder resume
```

El manifiesto registra componentes, modos, tamaños y SHA-256, pero no valores
de secretos. La operación rechaza ejecuciones y leases incompatibles.

## Restaurar

```bash
vibecoder pause
vibecoder backup verify BACKUP_ID
vibecoder backup restore BACKUP_ID
vibecoder maintenance status
vibecoder maintenance check
vibecoder maintenance reconcile
```

El restore usa staging y activa una barrera de recuperación. `resume` no la
elimina. Después de comprobar SQLite, GitHub, workers y operaciones externas,
libere únicamente la barrera identificada:

```bash
vibecoder maintenance clear-barrier BARRIER_ID
vibecoder resume
vibecoder doctor
```

Nunca edite SQLite ni elimine el directorio de recuperación para sortear una
barrera. Una operación externa o inferencia `UNKNOWN` requiere evidencia y no
se reintenta automáticamente.
