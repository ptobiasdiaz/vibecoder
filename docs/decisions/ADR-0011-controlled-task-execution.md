# ADR-0011 — Ejecución controlada por workflow de tarea

## Estado

Aceptada para M04.3.

## Decisión

Una tarea `PLANNED_WORK` no habilita por sí misma al worker. Cada ejecución requiere
un `TaskExecutionWorkflow` persistente, vinculado a la revisión exacta del plan, al
evento de aprobación, al snapshot del especialista y a la tarea materializada. El
núcleo solo permite crear el intento cuando ese workflow está en `RUNNING`.

Cada tarea usa un clon aislado y una rama determinista `vibecoder/task-<task-uuid>`.
La sesión de implementación pertenece exclusivamente a la tarea, aunque conserve la
identidad y cuenta del especialista. No se comparte con sesiones conversacionales.

Las fases Codex, validación, commit, push y PR son explícitas. Push y PR requieren una
confirmación local; las operaciones inciertas se reconcilian contra GitHub antes de
repetirse. Las dependencias solo se satisfacen con un merge verificado.

## Consecuencias

- No existe un desbloqueo global de tareas especializadas.
- Los workspaces fallidos se conservan para diagnóstico.
- La rama base y su SHA quedan congelados al preparar.
- `AGENTS.md` y `.vibecoder/` son rutas sensibles en tareas ordinarias.
- Los comandos funcionales son opt-in y no equivalen a aislamiento fuerte.
- M04.3 abre PRs, pero nunca los aprueba ni fusiona.

