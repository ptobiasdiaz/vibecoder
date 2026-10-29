# ADR-0005 — Estructura de contexto, documentación y jerarquía AGENTS.md

Estado: Aceptado para M03.3.

## Contexto

Los agentes necesitan instrucciones, memoria duradera y documentación pública sin
mezclar finalidades ni depender solamente del historial de una sesión. Codex reconoce
`AGENTS.md` en la raíz de un workspace, mientras que las instrucciones específicas de
agentes no deben ocupar la raíz ni descubrirse de forma implícita.

## Decisión

1. Los archivos internos versionados de vibecoder viven bajo `.vibecoder/`.
2. `AGENTS.md` en la raíz es la única excepción deliberada, por compatibilidad con las
   herramientas de desarrollo y el mecanismo habitual de instrucciones de Codex.
3. Las instrucciones específicas usan
   `.vibecoder/agents/<agent-id>/AGENTS.md`. M03.3 solo crea las de `main`.
4. La documentación destinada a usuarios y desarrolladores vive preferentemente en
   `doc/`; la finalidad, no el autor, determina la ubicación.
5. La precedencia es: seguridad del orquestador, `AGENTS.md` global, instrucciones
   específicas, contexto/memoria y tarea actual. Un nivel inferior no puede invalidar
   uno superior.
6. El orquestador carga explícitamente las instrucciones específicas. Registra hashes
   SHA-256 de todos los archivos usados y evita insertar otra vez el texto del
   `AGENTS.md` raíz cuando Codex ya lo descubre desde el workspace.
7. Los conflictos explícitos pueden declararse con líneas Markdown legibles
   `- Rule: clave = valor`; valores distintos para una misma clave bloquean la
   ejecución. Las contradicciones semánticas no demostrables requieren revisión
   humana y no se resuelven automáticamente.
8. La lectura lógica de contexto busca primero `.vibecoder/` y luego `doc/`. Las
   categorías internas, públicas y de instrucciones mantienen ubicaciones canónicas.
9. Toda escritura recibe una ruta explícita. No existe fallback de escritura ni
   sobrescritura implícita.
10. Ninguno de estos directorios puede contener secretos, sesiones, bases operativas,
    logs, cachés o temporales.

## Consecuencias

- La memoria es revisable y versionable por humanos.
- Las instrucciones específicas no dependen del descubrimiento automático de Codex.
- Los hashes permiten auditar el contexto de una ejecución.
- La detección automática de conflictos se limita deliberadamente a reglas explícitas;
  no pretende interpretar semántica libre.

