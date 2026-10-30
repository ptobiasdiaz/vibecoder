# ADR-0009 — Identidad, configuración y ciclo de vida de especialistas

Estado: Aceptado para M04.1.

## Contexto

La identidad lógica de un agente debe sobrevivir a cambios de instrucciones, cuenta y
sesión. A la vez, una edición local de `.vibecoder/agents/<id>/AGENTS.md` no puede
convertirse silenciosamente en configuración operativa.

## Decisión

1. La entidad `Agent` de M01 conserva la identidad estable. `SpecialistAgent` añade
   ciclo de vida y cuenta asignada sin reemplazarla.
2. La configuración es una secuencia inmutable de revisiones con rol,
   responsabilidades, capacidades, restricciones, ruta, hashes, autor y commit.
3. Los estados del especialista son `DRAFT`, `PROPOSED`, `ACTIVE`, `SUSPENDED` y
   `RETIRED`; las revisiones distinguen `PROPOSED`, `VALIDATED`, `ACTIVE`,
   `SUPERSEDED` y `REJECTED`.
4. Activar exige operador explícito, proyecto activo, cuenta disponible, revisión
   validada y archivo exacto integrado en el `HEAD` limpio de la rama principal.
5. Una propuesta local o una edición externa bloquea el contexto operativo; no se
   convierte en activa por existir en el filesystem.
6. Retirar conserva identidad, revisiones, auditoría y sesiones. El identificador no
   se reutiliza.
7. La cuenta pertenece a la ejecución, no a la identidad. Cambiarla exige suspender
   el agente; las sesiones anteriores se conservan y no se reanudan con la nueva
   cuenta.
8. `AGENTS.md` raíz se deja al descubrimiento normal de Codex y se registra por hash;
   las instrucciones específicas se cargan explícitamente una sola vez.
9. M04.1 registra autorización administrativa local. La aprobación GitHub y el PR de
   cambios de agentes pertenecen a M04.2/M04.3.

## Consecuencias

- Hay trazabilidad reproducible sin acoplar identidad, revisión y thread.
- Una configuración pendiente puede hacer visible deriva y bloquear, nunca ampliar
  permisos silenciosamente.
- La detección automática de contradicciones sigue limitada a reglas explícitas
  `Rule: clave = valor`; las ambigüedades semánticas requieren revisión humana.
- Las tareas de especialistas todavía no se asignan automáticamente.
- M04.1 bloquea también su ejecución directa; habilitarla exige el workflow de
  enrutamiento y autorización de un hito posterior.
