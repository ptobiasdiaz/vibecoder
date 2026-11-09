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
existe downgrade automático de SQLite. Los runtimes actuales rechazan esquemas
futuros, pero el runtime histórico 0.3.0 no conoce el esquema 15 y no puede proteger
retroactivamente esa combinación. Dpkg tampoco permite al paquete instalado impedir
de forma universal todos los downgrades.

El retorno soportado exige: detener y pausar el servicio; comprobar que no hay
operaciones activas; verificar un backup anterior a la migración; restaurarlo;
instalar después el paquete anterior; comprobar esquema e integridad; y mantener la
barrera hasta completar la reconciliación. Nunca instale primero el runtime antiguo
sobre la base más reciente.

Los conffiles, secretos, cuentas y workspaces no se sobrescriben por defaults nuevos.
`remove` y `purge` mantienen la política conservadora existente.
