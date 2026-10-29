# Modelo de datos conceptual

Entidades iniciales:

- `Account`: ID lógico, proveedor/autenticación, referencia no secreta al entorno,
  estado, límites y próximo reset; secretos fuera de base de datos, logs y repositorios.
- `Project`: proyecto lógico con repositorio GitHub opcional inicialmente, estado de
  bootstrap, cuenta asignada y configuración activa.
- `Agent`: ID lógico, repositorio, rol, versión de definición, estado, cuenta asignada.
- `Session`: ID técnico Codex, agente, cuenta, ciclo de vida, estado de compactación/reconstrucción.
- `Task`: unidad asignada a un agente, estado, referencia externa e idempotencia.
- `ExecutionAttempt`: intento numerado, sesión, thread, turn, resultado final y error
  estructurado; un resultado desconocido tras reinicio nunca se reejecuta sin
  reconciliación.
- `GitHubRepository`: repositorio autorizado vinculado a un proyecto, identificado por
  el ID estable de GitHub; propietario, nombre, URLs, permisos y estado vacío son
  metadatos sincronizables.
- `GitHubOperation`: intención de escritura remota, clave idempotente, hash del payload,
  estado y correlación con el recurso remoto para reconciliar reinicios y resultados
  inciertos.
- `BootstrapWorkflow`: Issue inicial, agente principal, estado de aprobación, actor y
  evento verificables, y especificación conversacional congelada.
- `BootstrapComment`: comentario remoto estable, autor, orden, autorización, tarea M02,
  respuesta persistida y comentario de respuesta confirmado.
- `IssueWorkflow`: issue ID, estado, revisión de especificación aprobada, responsables, versión de etiquetas.
- `Execution`: ID, sesión, evento origen, prompt_comment_id, response_comment_id, estado, intentos, timestamps, consumo.
- `Question`: ID estable, Issue/PR, autor lógico, destinatario, comentario origen, comentario respuesta, estado.
- `Implementation`: Issue, rama, worktree, sesión temporal, commits, PR, pruebas y revisiones.
- `WebhookDelivery`: delivery ID, firma validada, resultado de procesamiento, idempotencia.
- `Notification`: canal, deduplicación, severidad, envío y estado.

Restricciones: identificadores de GitHub estables; idempotencia por evento; bloqueo exclusivo de turnos por sesión; no reintentar efectos externos inciertos sin reconciliar con GitHub; vincular decisiones a IDs de comentarios y hashes/versiones de especificación.

La base de datos es autoridad sobre tareas, intentos y transiciones; GitHub será el
registro visible y la autoridad para aprobaciones y merges cuando se integre.
