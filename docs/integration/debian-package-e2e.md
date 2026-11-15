# Validación E2E del paquete Debian 0.0.1

## M06.11 — autonomía de Codex

El Issue #9 y el PR #10 validaron `autonomous` con Codex 0.167.0 instalado:
una tarea humana aprobada modificó Python y ejecutó cuatro tests sin ninguna
aprobación de acción. La rama fue publicada por el núcleo, sin PAT en el
worker y sin merge. Después, `manual` produjo una solicitud por acción real,
decidida por CLI y auditada; la corrección actualizó la misma rama. El estado
final de `codex-1` es `READY/autonomous`.

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

## DAG dependiente validado

El Issue #5 materializó exactamente A -> B. A produjo
`1a2d0d5b0ef0bd6995d4d4e62939f5a29de41e8e`; B comenzó exactamente en ese
SHA y produjo `82f313418ce920842f23c355668a90027bd6965b`, con A como padre directo.
Las dos pruebas pasaron y se publicaron los PR #6 y #7 sin merge automático.
Polling y reinicio no crearon duplicados.

Limitaciones: los backups contienen secretos sin cifrado propio; los binarios
gestionados aumentan su tamaño; las ejecuciones históricas `UNKNOWN` permanecen
bloqueadas y nunca se reintentan ciegamente.

