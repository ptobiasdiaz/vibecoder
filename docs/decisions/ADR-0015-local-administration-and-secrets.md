# ADR-0015 — Administración local y secretos

## Estado

Aceptada para M05.2.

## Decisión

La administración mutable requiere UID 0. El usuario de servicio `vibecoder` no es
administrador. La CLI es una fachada de `AdministrationService`, reutilizable por el
futuro paquete web, y usa los servicios de dominio para proyectos, cuentas y agentes.

SQLite sigue siendo la autoridad de identidades y workflows. `supervisor.json` y
`accounts.json` contienen exclusivamente configuración operativa no secreta. Sus
actualizaciones usan bloqueo advisory, validación integral, `fsync` y reemplazo
atómico, conservando propietario y modo.

Los secretos se almacenan individualmente en `/var/lib/vibecoder/secrets`, con
directorio `0700`, archivos `0600`, nombres validados, rechazo de enlaces y reemplazo
atómico. SQLite conserva solo metadatos y referencias. El proceso de servicio puede
leer los secretos que necesita; no se promete protección frente a un proceso de
servicio comprometido. «Revocar» en la CLI significa revocación local, no revocación
del token ante GitHub.

La entrada interactiva usa TTY sin eco. La automatización usa un descriptor ya
abierto; los secretos nunca son argumentos. M05.3 podrá preparar la autoridad inicial
sin cambiar este modelo.

## Consecuencias

- No existe un segundo registro administrativo de proyectos o agentes.
- Los cambios críticos degradan `READY` a `CONFIGURED`.
- No se concede administración al grupo de servicio.
- Una futura administración multiusuario necesitará una política adicional explícita.
