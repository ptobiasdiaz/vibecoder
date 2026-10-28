# Modelo de datos conceptual

Entidades iniciales:

- `Account`: ID lógico, proveedor/autenticación, estado, límites, próximo reset; secretos fuera de logs y repositorios.
- `Repository`: GitHub installation/repository IDs, estado de bootstrap, cuenta asignada, configuración activa.
- `Agent`: ID lógico, repositorio, rol, versión de definición, estado, cuenta asignada.
- `Session`: ID técnico Codex, agente, cuenta, ciclo de vida, estado de compactación/reconstrucción.
- `IssueWorkflow`: issue ID, estado, revisión de especificación aprobada, responsables, versión de etiquetas.
- `Execution`: ID, sesión, evento origen, prompt_comment_id, response_comment_id, estado, intentos, timestamps, consumo.
- `Question`: ID estable, Issue/PR, autor lógico, destinatario, comentario origen, comentario respuesta, estado.
- `Implementation`: Issue, rama, worktree, sesión temporal, commits, PR, pruebas y revisiones.
- `WebhookDelivery`: delivery ID, firma validada, resultado de procesamiento, idempotencia.
- `Notification`: canal, deduplicación, severidad, envío y estado.

Restricciones: identificadores de GitHub estables; idempotencia por evento; bloqueo exclusivo de turnos por sesión; no reintentar efectos externos inciertos sin reconciliar con GitHub; vincular decisiones a IDs de comentarios y hashes/versiones de especificación.

La base de datos es autoridad sobre ejecuciones y transiciones; GitHub es el registro visible y la autoridad para aprobaciones y merges.
