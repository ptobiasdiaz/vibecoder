# Actualizaciones y rollback

## Preparación manual

```bash
sudo systemctl stop vibecoder.service
sudo vibecoder-admin maintenance prepare-upgrade --offline
sudo apt install ./vibecoder_NUEVA_VERSION_all.deb
sudo vibecoder-admin maintenance check
```

El paquete 0.4.0 crea además un backup pre-migración automático durante upgrades,
después de que debhelper detenga la unidad y antes de aplicar migraciones. `postinst`
verifica esquema, integridad y claves foráneas sin red.

Una actualización con jobs activos o leases no continúa. PENDING y UNKNOWN se
preservan; UNKNOWN nunca vuelve automáticamente a PENDING.

## Rollback

Rollback de paquete, esquema y restauración completa son operaciones diferentes. No
existe downgrade automático de SQLite. Un runtime antiguo rechaza un esquema más
reciente. Para volver a una versión anterior se necesita un backup creado con un
esquema compatible y una restauración controlada, seguida de reconciliación remota.

Los conffiles, secretos, cuentas y workspaces no se sobrescriben por defaults nuevos.
`remove` y `purge` mantienen la política conservadora existente.
