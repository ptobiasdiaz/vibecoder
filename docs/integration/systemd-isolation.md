# Aislamiento systemd de workers

M06.6.1 verificó con accesos reales tres identidades Unix distintas para
`codex-1`, `codex-2` y `fake-1`. Ninguna pertenece a `vibecoder-admin` ni
comparte UID con el daemon.

Una unidad transitoria observada aplicó el UID/GID del worker,
`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`,
`RestrictSUIDSGID`, `LockPersonality`, `UMask=0077`, capabilities vacías,
`KillMode=control-group` y tiempo máximo acotado.

Desde el proceso aislado se verificó acceso denegado al PAT global, SQLite,
socket administrativo, storage de otro worker y workspace no asignado. El mismo
proceso sí pudo usar su storage, temporal y workspace asignados. No heredó
variables sensibles ni descriptores distintos de stdio/systemd.

Esto prueba separación Unix, filesystem y procesos en esta configuración. No
equivale a aislamiento de red: no se introduce un namespace de red por worker.
El helper root valida una operación cerrada, usuario derivado del UUID, módulo
incluido y rutas confinadas; no ofrece ejecución arbitraria.
