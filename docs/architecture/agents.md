# Agentes, responsabilidades y sesiones

## Tipos

**Principal**: visión global, clasificación, discusión de requisitos, selección de agentes, resolución de decisiones arquitectónicas, propuestas de cambios al registro de agentes.

**Especializado**: memoria y conocimiento de un área, planes técnicos, documentación, consultas y supervisión del resultado. Puede preguntar a otros agentes o a humanos, pero no incorporar unilateralmente nuevos responsables al Issue.

**Implementador**: sesión temporal por trabajo, modifica código y tests en worktree aislado, genera commits y participa en correcciones del PR. Su ciclo de vida puede extenderse durante toda la revisión.

## Registro dinámico

La configuración versionada del repositorio declara identidades, rutas de documentación y responsabilidades. Un agente no se autoriza ni cambia permisos editando un comentario. Altas, cambios y retiradas del registro requieren PR y validación humana.

Separar identidad lógica (`backend-z80`), sesión operativa de Codex y memoria duradera
versionada. Un thread vacío aún no es una sesión recuperable; después de un turno real
completado puede reanudarse tras reiniciar App Server. Una sesión puede compactarse o
reconstruirse desde documentos sin perder identidad lógica. Las sesiones pertenecen a
una cuenta explícita, pero las credenciales pertenecen a la cuenta, no al agente.

## Asignación y colaboración

El principal selecciona los especialistas que intervienen en cada Issue. Los especialistas pueden solicitar consultas técnicas, arquitectónicas o humanas. Para introducir un especialista nuevo, escalan al principal. Los especialistas pueden coordinar detalles técnicos sin involucrar continuamente al principal; cambios arquitectónicos o de alcance vuelven al principal/humano.

## Memoria

Documentar arquitectura, invariantes, interfaces, decisiones, riesgos y tareas pendientes. Los planes y decisiones de un Issue se guardan en su rama de trabajo y se revisan con el PR. La compactación de sesiones no debe depender únicamente del historial de conversación.
