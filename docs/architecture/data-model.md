# Modelo de datos conceptual

Entidades iniciales:

- `Account`: ID lógico, proveedor/autenticación, referencia no secreta al entorno,
  estado, límites y próximo reset; secretos fuera de base de datos, logs y repositorios.
- `Project`: proyecto lógico con repositorio GitHub opcional inicialmente, estado de
  bootstrap, cuenta asignada y configuración activa.
- `Agent`: ID lógico, repositorio, rol, versión de definición, estado, cuenta asignada.
- `SpecialistAgent`: extensión no destructiva de `Agent` con identificador único por
  proyecto, ciclo de vida, cuenta asignada y revisión activa.
- `AgentConfigurationRevision`: rol, responsabilidades, capacidades, restricciones,
  instrucciones, hashes, commit y autorización versionados.
- `AgentAuditEvent`: actor, operación, transición, versiones y resultado administrativo.
- `IssueWorkflow` y `IssueComment`: Issue ordinario, polling, destinatario, sesión,
  tarea de coordinación y publicación confirmada.
- `IssueAgentSession`: sesión aislada por Issue/agente, cuenta, workspace e instrucciones.
- `PlanRevision`: JSON canónico, revisión/hash, publicación, aprobación atribuida,
  contexto congelado y snapshot de agentes.
- `PlannedTask` y dependencias: delegación lógica y vínculo idempotente a `Task` M01.
- `PlanApprovalEvent`: evento GitHub consumido una sola vez por una revisión exacta.
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
- `GenerationWorkflow`: aprobación y hash de especificación usados, workspace y rama
  aislados, sesión operativa, tarea M02, versiones de instrucciones, validación y
  commit local preparado.
- `GitConfiguration`: revisión propuesta, confirmada, aprobada o aplicada; decisiones
  Git tipadas, hash canónico, snapshot remoto, especificación y evento autorizador.
- `PublicationWorkflow`: cadena autorizada desde generación hasta ramas, PR, merge y
  activación, con SHA y estado remoto verificados.
- `PublicationOperation`: intención y resultado de cada push para reconciliar una
  terminación incierta antes de repetir el efecto.
- `IssueWorkflow`: issue ID, estado, revisión de especificación aprobada, responsables, versión de etiquetas.
- `Execution`: ID, sesión, evento origen, prompt_comment_id, response_comment_id, estado, intentos, timestamps, consumo.
- `Question`: ID estable, Issue/PR, autor lógico, destinatario, comentario origen, comentario respuesta, estado.
- `Implementation`: Issue, rama, worktree, sesión temporal, commits, PR, pruebas y revisiones.
- `WebhookDelivery`: delivery ID, firma validada, resultado de procesamiento, idempotencia.
- `Notification`: canal, deduplicación, severidad, envío y estado.

Restricciones: identificadores de GitHub estables; idempotencia por evento; bloqueo exclusivo de turnos por sesión; no reintentar efectos externos inciertos sin reconciliar con GitHub; vincular decisiones a IDs de comentarios y hashes/versiones de especificación.

`TaskExecutionWorkflow` (M04.3) vincula de forma única una tarea materializada con su
plan aprobado, especialista/revisión/cuenta, workspace, sesión, base/rama,
validación, commit, push y PR. Sus operaciones remotas conservan intención y resultado
para reconciliar estados inciertos.

M04.4 añade `PullRequestSupervision`, eventos remotos tipados, sesiones conversacionales
por PR/agente y ciclos de corrección. La tarea M01 concluye el turno Codex; el estado
`MERGED` del workflow es la autoridad de integración y dependencias.

La base de datos es autoridad sobre tareas, intentos y transiciones; GitHub será el
registro visible y la autoridad para aprobaciones y merges cuando se integre.
## Operación continua (M04.5)

- `supervisor_jobs`: intención, categoría, prioridad, elegibilidad, intentos, lease,
  error y siguiente acción. No sustituye al workflow de origen.
- `supervisor_controls`: pausas globales o por proyecto para toda actividad,
  inferencia o publicación.
- `supervisor_activity`: observabilidad local estructurada sin secretos.
