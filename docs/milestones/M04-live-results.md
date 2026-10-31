# Resultados de la validación live integral de M04

## Resumen

**Resultado: SUPERADA el 2026-10-31.** Se validó el flujo real desde un Issue hasta
dos tareas dependientes integradas mediante Pull Requests y merges humanos en
`ptobiasdiaz/vibecoder_test`. Vibecoder no realizó merges ni cerró el Issue.

Entorno:

- repositorio de pruebas: `ptobiasdiaz/vibecoder_test`;
- proyecto: `c223069a-503c-4f60-8f00-a71e171fb84a` (`ACTIVE`);
- Issue: [#4](https://github.com/ptobiasdiaz/vibecoder_test/issues/4);
- especialista `testing`: `12d04502-b1bf-4e38-8715-346b422d8b09`;
- workflow de Issue: `253b3856-36b0-4559-8fd6-e7c67bd8695b`;
- plan aprobado: revisión `4986258b-e543-47b6-a20c-c1ba6d536440`, hash
  `8cda7fbda35704dfec067d6c051efff53632c9a572efa07141b1ec24ab28b2b7`;
- aprobación atribuida a `ptobiasdiaz`, evento GitHub `34778511432`.

No se registran tokens, cabeceras de autenticación ni contenido sensible.

## Etapas y evidencias

| Etapa | Resultado | Evidencia |
| --- | --- | --- |
| Descubrimiento y conversación | SUPERADA | Issue #4; sesión principal reanudada entre comentarios |
| Plan estructurado y aprobación | SUPERADA | revisión y hash exactos; evento de etiqueta posterior y atribuible |
| Materialización | SUPERADA | tareas A y B persistidas sin intentos duplicados |
| Tarea A | SUPERADA | ejecución `3e1b8bb0-8cc7-4709-9e48-45a5f0155743`; PR #5 |
| Dependencia B antes del merge | SUPERADA | B no elegible, sin workspace ni sesión de implementación |
| Review y corrección | SUPERADA | comentario de línea `4432201668`; corrección `35397c0d-dbf1-4228-8e35-70c70eff0d19` |
| Publicación de corrección | SUPERADA | commit `c71c9d93d55c90eaf9654122f5d245959334d5a3` en el mismo PR #5 |
| Merge humano A | SUPERADA | merge `c3aa9b2e910593c2d3cd40d2d5a6712ef2b439ed` |
| Desbloqueo de B | SUPERADA | B solo fue elegible después del merge verificado de A |
| Tarea B | SUPERADA | ejecución `925b1a79-136e-485a-a618-b7c2db575593`; sesión `3912ec38-f0fe-42a8-99e4-e408cc595d88` |
| PR y merge B | SUPERADA | PR #6; commit `32a6cfcde37b9c913b80cdc5de23d7635dc84e8f`; merge `f9385ecaa5be03f7f27295dc1b2489961b26024b` |
| Supervisor | SUPERADA | `dry-run`, ciclos `once`, pausa/reanudación y SIGTERM seguro |
| Recuperación e idempotencia | SUPERADA | reinicio sin turnos, commits, pushes, comentarios ni PRs duplicados |

La tarea A añadió pruebas para `sumar`; la corrección solicitada incorporó el caso
de enteros grandes. La tarea B documentó la estrategia en `doc/testing.md`. Los tests
del proyecto se ejecutaron solo tras autorización expresa: ocho pruebas superadas.

## Fallos detectados y corregidos

La validación descubrió fallos de integración reales, todos cubiertos por regresiones:

1. El prompt de planificación no describía el esquema `vibecoder-plan`.
2. El scheduler consideraba revisiones aprobadas sustituidas en vez de solo la activa.
3. `dry-run` no reflejaba pausas persistentes.
4. Se seleccionaban tareas principales/no especializadas para M04.3.
5. La identidad del remoto comparaba literalmente URLs SSH y HTTPS equivalentes.
6. El evento fuente de una corrección quedaba pendiente tras publicar la respuesta.
7. Permanecían jobs `PENDING` obsoletos cuando avanzaba el workflow.
8. El scheduler preparaba dependientes antes del merge verificado.
9. `TASK_PREPARE` no declaraba su dependencia de lecturas remotas.
10. Un fallo remoto previo al clon dejaba `READY` sin ruta de reanudación.

El 401 provocado por el punto 9 no produjo escritura remota, inferencia ni pérdida de
estado. Su job `BLOCKED` se conserva como evidencia histórica; el workflow sucesor
terminó `MERGED`. No quedaron jobs `RUNNING` ni `UNKNOWN`.

## Seguridad y recuperación

- Codex utilizó la cuenta asignada al especialista y workspaces separados por tarea.
- El token GitHub permaneció fuera de SQLite, workspaces, commits y prompts.
- Las ramas se publicaron sin force push y los PRs se fusionaron manualmente.
- Las publicaciones se separaron de inferencia mediante gates de configuración.
- El proceso continuo se detuvo con SIGTERM durante una etapa segura y reanudó sin
  duplicar efectos.
- No se cerró automáticamente el Issue #4 ni se ejecutaron dependientes antes de tiempo.

## Regresión

La suite offline final se ejecuta con:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Resultado final: **257 tests superados, 45 integraciones live opt-in omitidas**, en
12,222 segundos. Las pruebas live permanecen opt-in y no forman parte de la ejecución
predeterminada.

## Limitaciones conservadas

- Supervisor monoproceso y SQLite local; sin locks distribuidos.
- Polling, sin webhooks.
- Los estados realmente indeterminados requieren reconciliación humana.
- Sin merge ni cierre automático de Issues.
- Las pruebas o código generado solo se ejecutan mediante autorización explícita.
