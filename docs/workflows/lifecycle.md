# Estados y eventos

## Proyecto

`UNINITIALIZED → BOOTSTRAPPING → DESIGNING → READY → ACTIVE`.

## Issue

`NEW → DISCUSSION → APPROVED → IMPLEMENTING → REVIEW → MERGED`.

Estados auxiliares: `WAITING_HUMAN`, `WAITING_AGENT`, `WAITING_QUOTA`, `BLOCKED`, `FAILED`, `CANCELLED`. Una revisión puede devolver el trabajo a implementación. No inferir `MERGED` por un PR cerrado sin merge.

## Eventos relevantes

- `issues.opened`: encolar análisis principal.
- `issue_comment.created`: procesar comentarios humanos o respuestas correlacionadas; filtrar ecos del bot.
- `issues.labeled`/`unlabeled`: comprobar transiciones, permisos y aprobación.
- `pull_request`/`pull_request_review`/`pull_request_review_comment`: actualizar revisión, comentarios técnicos y merge.

## Autorización

Solo un humano con permisos suficientes puede activar la etiqueta de aprobación. El principal selecciona especialistas; el orquestador valida el registro activo. Merge solo humano conforme a protección de ramas.

## Ejecución

Bloquear sesión, registrar intención, publicar prompt, ejecutar turno, publicar resultado, reconciliar IDs y liberar bloqueo. Si falla en medio de efectos externos, consultar GitHub y estado persistido antes de repetir. No iniciar dos turnos simultáneos de una misma sesión.

En el worker local de M02, el intento se crea antes de llamar a Codex; thread y turn se
persisten en cuanto se conocen. Un reinicio deja el intento `UNKNOWN` y la tarea
`WAITING`. Solo la consulta de historial o una decisión explícita puede cerrarlo; el
prompt no se reenvía automáticamente.

En M03.2, un comentario pasa por `RECEIVED → QUEUED → RUNNING → RESPONSE_PENDING →
COMPLETED`. `IGNORED` representa ecos del bot; `REJECTED`, actor no autorizado o directiva inválida;
`BLOCKED` conserva una ejecución fallida o incierta. La publicación se reintenta desde
`RESPONSE_PENDING` sin volver a ejecutar Codex.
