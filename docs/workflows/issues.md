# Workflow de Issues de desarrollo

1. `discover` consulta Issues de un proyecto `ACTIVE`, excluye Pull Requests y el
   bootstrap completado, y crea un workflow único por ID GitHub.
2. `poll` registra comentarios en orden `(created_at, id)`. Comentarios propios se
   reconocen por operaciones remotas persistidas; usuarios no autorizados no provocan
   turnos.
3. Un comentario ordinario o `/agent main` se dirige al principal. `/agent <id>` solo
   admite especialistas `ACTIVE`, integrados y consistentes.
4. Cada Issue/agente mantiene una sesión compatible con cuenta, workspace y versiones
   de instrucciones. Una incompatibilidad crea otra sesión y conserva la anterior.
5. El principal puede producir un bloque JSON `vibecoder-plan`; también puede cargarse
   explícitamente mediante CLI. Solo el principal consolida el plan.
6. El plan se valida, publica en Markdown con revisión/hash y pasa a espera.
7. Un evento nuevo y atribuible de `status:approved`, posterior a esa publicación,
   congela conversación, plan y snapshot de agentes.
8. `materialize` crea una tarea `PLANNED_WORK` por ID lógico y conserva dependencias.
   El selector general del worker siempre las ignora.
9. M04.3 puede habilitar una tarea concreta mediante un workflow verificable. Una
   dependencia solo se satisface tras merge confirmado; luego se preparan workspace,
   sesión, validación, commit, rama y PR sin merge automático.
10. M04.4 supervisa el PR. Las correcciones requieren eventos concretos, alcance
    `IN_SCOPE` y autorización local; se publican en la misma rama.
11. Solo un merge humano verificado marca la ejecución integrada y libera
    dependencias. El Issue no se cierra automáticamente.

Un Issue cerrado antes de aprobar queda `CLOSED`; al reabrirse vuelve a `ACTIVE`. Un
resultado Codex incierto queda bloqueado y nunca se reenvía automáticamente.
## Ejecución continua

M04.5 puede descubrir, conversar, comprobar aprobaciones, materializar, ejecutar y
supervisar mediante polling. Cada paso se agenda desde el estado persistido y vuelve a
validar el workflow. Una revisión, job o lease no concede autorización. `UNKNOWN` y
`BLOCKED` requieren reconciliación/intervención y no se seleccionan como trabajo normal.

