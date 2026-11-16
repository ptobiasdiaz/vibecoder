# Telegram como canal de aprobaciones — propuesta pospuesta

Telegram será un canal del servicio neutral de aprobaciones del núcleo, nunca
una capacidad especial del plugin Codex:

```text
Plugin de proveedor -> Approval Service -> CLI
                                      `-> Telegram
```

El token de BotFather será un secreto daemon-owned. Los workers no podrán
leerlo. La vinculación exigirá una ceremonia local autenticada que asocie IDs
numéricos de Telegram con administradores autorizados; no se confiará en
nombres visibles. Cada mensaje mostrará proyecto, tarea, worker, acción,
motivo, caducidad y un identificador no reutilizable. Los botones ACEPTAR y
DENEGAR producirán una decisión idempotente de un solo uso, auditada y
correlacionada con la solicitud exacta.

La primera implementación usará long polling, sin webhook público. Mantendrá
denegación por defecto, expiración, estados `UNKNOWN`, fallback a CLI y
redacción de secretos. El bot no aprobará planes, no hará merges y no dará a
los plugins acceso al token. El contrato futuro deberá permitir otros canales
sin cambiar plugins ni persistencia de ejecuciones.

Esta propuesta no está implementada y permanece prevista para 0.0.2. El fix
M06.13 conserva la versión 0.0.1.
