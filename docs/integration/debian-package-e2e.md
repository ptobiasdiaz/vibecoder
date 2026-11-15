# Validación E2E del paquete Debian 0.0.1

La instalación limpia mediante APT arrancó en `STANDBY/OFFLINE`; tras GitHub y
Codex pasó automáticamente a `READY/ONLINE`. El daemon usó `vibecoder` y cada
worker un UID `vibecoder-w-*` independiente.

## Recorrido real

1. Login GitHub de `ptobiasdiaz` (ID 57227182).
2. `codex-1` autenticado y verificado con Codex 0.167.0.
3. Registro local sin modificar el checkout del usuario.
4. Issue #1, plan revisión 3 y aprobación humana del hash exacto.
5. Ejecución Codex con aprobaciones por acción.
6. Push y creación idempotente del PR #2.
7. Review humana, continuación de sesión y segundo commit sobre el mismo PR.
8. Merge humano detectado y verificado, sin merge automático.

Un reinicio conservó todos los registros sin duplicarlos. El backup incluyó
SQLite, secretos, workers, workspaces y plugins; su manifiesto v1 y 574
checksums se verificaron. El restore activó una barrera y solo se desbloqueó
tras integridad, probe y reconciliación.

`apt remove` conservó SQLite y secretos. El purge controlado eliminó estado y
usuarios de servicio; una reinstalación limpia volvió a `STANDBY/OFFLINE` y el
backup recuperó el estado bajo barrera. Un grupo administrativo con miembros
humanos se conserva de forma deliberadamente conservadora.

Limitaciones: los backups contienen secretos sin cifrado propio; los binarios
gestionados aumentan su tamaño; una ejecución histórica `UNKNOWN` permanece
bloqueada y nunca fue reintentada. La corrección del workspace de una tarea
dependiente pasó la regresión offline, pero su repetición live requiere un plan
nuevo y aprobación humana.

