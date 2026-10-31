# Agentes, responsabilidades y sesiones

## Tipos

**Principal**: visión global, clasificación, discusión de requisitos, selección de agentes, resolución de decisiones arquitectónicas, propuestas de cambios al registro de agentes.

**Especializado**: memoria y conocimiento de un área, planes técnicos, documentación, consultas y supervisión del resultado. Puede preguntar a otros agentes o a humanos, pero no incorporar unilateralmente nuevos responsables al Issue.

**Implementador**: sesión temporal por trabajo, modifica código y tests en worktree aislado, genera commits y participa en correcciones del PR. Su ciclo de vida puede extenderse durante toda la revisión.

## Registro dinámico

La configuración versionada del repositorio declara identidades, rutas de documentación y responsabilidades. Un agente no se autoriza ni cambia permisos editando un comentario. Altas, cambios y retiradas del registro requieren PR y validación humana.

M04.1 materializa esta separación mediante una identidad `Agent` estable, un registro
de especialista con ciclo `PROPOSED/ACTIVE/SUSPENDED/RETIRED` y revisiones inmutables
de configuración. La activación local solo acepta instrucciones ya integradas en el
`HEAD` limpio de la rama principal. El flujo de aprobación y PR remoto se incorpora en
M04.2/M04.3.

Separar identidad lógica (`backend-z80`), sesión operativa de Codex y memoria duradera
versionada. Un thread vacío aún no es una sesión recuperable; después de un turno real
completado puede reanudarse tras reiniciar App Server. Una sesión puede compactarse o
reconstruirse desde documentos sin perder identidad lógica. Las sesiones pertenecen a
una cuenta explícita, pero las credenciales pertenecen a la cuenta, no al agente.

## Asignación y colaboración

El principal selecciona los especialistas que intervienen en cada Issue. Los especialistas pueden solicitar consultas técnicas, arquitectónicas o humanas. Para introducir un especialista nuevo, escalan al principal. Los especialistas pueden coordinar detalles técnicos sin involucrar continuamente al principal; cambios arquitectónicos o de alcance vuelven al principal/humano.

## Memoria

Documentar arquitectura, invariantes, interfaces, decisiones, riesgos y tareas pendientes. Los planes y decisiones de un Issue se guardan en su rama de trabajo y se revisan con el PR. La compactación de sesiones no debe depender únicamente del historial de conversación.

Las reglas comunes están en `AGENTS.md` y las específicas en
`.vibecoder/agents/<agent-id>/AGENTS.md`. Seguridad del orquestador y reglas globales
prevalecen sobre instrucciones específicas, memoria y tarea. M03.3 registra hashes de
las instrucciones usadas y bloquea conflictos explícitos; no crea especialistas.
M04.1 carga explícitamente el archivo específico, conserva el hash del global sin
duplicar su texto y bloquea cualquier deriva respecto de la revisión activa.

M04.2 permite consultas de planificación mediante `/agent <id>`. Cada agente conserva
una sesión por Issue y el principal sigue siendo el único que consolida el plan. Un
especialista no aprueba su trabajo ni adquiere permiso de implementación. La
asignación congela revisión, cuenta e instrucciones; cualquier deriva posterior
bloquea la materialización.

M04.3 crea una sesión de implementación exclusiva por tarea. El worker solo acepta
`PLANNED_WORK` cuando recibe un workflow autorizado en `RUNNING`; la sesión de
coordinación nunca se reutiliza como sesión de implementación.

En M04.4 el especialista puede conversar directamente en su PR. Las decisiones de
alcance vuelven al principal. La sesión conversacional del PR permanece separada; una
corrección compatible reutiliza exclusivamente la sesión de implementación original.
