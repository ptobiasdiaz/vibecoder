# ADR-0004 — Polling, autorización y aprobación del bootstrap

Estado: Aceptado para M03.2.

## Contexto

M03.2 debe conversar mediante comentarios sin webhooks y reconocer una aprobación
humana verificable. El estado actual de una etiqueta no identifica quién la añadió y
los comentarios y marcadores HTML son entradas externas no confiables.

## Decisión

1. El polling usa el ID estable del comentario, no timestamps, como identidad. La
   fecha remota solo ordena y el ID rompe empates.
2. Los autores autorizados se configuran localmente mediante una lista explícita. Los
   actores tipo `Bot` se ignoran. Con un token personal, las respuestas propias se
   correlacionan por el ID remoto de una operación persistida; no se ignora a una
   persona solo porque comparta el login de la credencial activa.
3. Cada comentario aceptado crea una tarea normal de M02. Un estado persistente
   `RUNNING` reclama el comentario; otros procesos responden `BUSY`.
4. La publicación es una fase posterior a Codex. Una respuesta persistida puede
   publicarse o reconciliarse otra vez sin ejecutar un turno nuevo.
5. La aprobación requiere simultáneamente la etiqueta configurada y que el evento más
   reciente de esa etiqueta sea `labeled`, con actor humano incluido en la lista local.
6. Una etiqueta sin evento atribuible, atribuida a un usuario no autorizado o con
   comentarios pendientes deja el workflow en `APPROVAL_PENDING`.
7. Al aprobar se guarda una instantánea Markdown de la conversación completada y el
   proyecto pasa de `BOOTSTRAPPING` a `DESIGNING`. No se generan archivos ni Git refs.

## Consecuencias

- El polling es reiniciable y no confía en frases como autorización.
- La lista local de autorizados es configuración operativa y no puede cambiarse desde
  GitHub.
- Una caída durante Codex deja el intento indeterminado y requiere recuperación o
  reconciliación conservadora; el prompt no se reenvía automáticamente.
- Ediciones y borrados de comentarios quedan fuera de M03.2.

