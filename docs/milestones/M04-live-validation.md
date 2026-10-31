# Validación live integral de M04.3, M04.4 y M04.5

## Advertencia

Esta validación fue **ejecutada y superada el 2026-10-31**. El documento conserva el
procedimiento reproducible; las evidencias están en
[M04-live-results.md](M04-live-results.md). Consume cuota Codex y escribe Issues,
comentarios, ramas, commits y Pull Requests en el repositorio de pruebas. El merge es
siempre manual. Use un proyecto ACTIVE, especialista ACTIVE y checkout limpio.

## Preparación

Defina fuera del repositorio `supervisor.json` y use los UUID reales:

```bash
cd /home/tobias/dev/vibecoder
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json config-check
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json dry-run
```

El primer comando solo valida; el segundo solo lee SQLite. Confirme que
`publication_enabled` es `false` durante la inspección inicial.

## Escenario integral

1. Cree manualmente un Issue de desarrollo en el repositorio de pruebas.
2. Descubra y procese la conversación con la CLI M04.2 o con ciclos `once`. Esta etapa
   lee/escribe GitHub y los turnos conversacionales consumen Codex.
3. Publique un plan tipado, retire cualquier etiqueta antigua y aplique de nuevo
   `status:approved` después de publicar la revisión exacta. Verifique hash, evento y
   materialización.
4. Compruebe sin efectos:

   ```bash
   .venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json dry-run
   .venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json jobs
   ```

5. Habilite inferencia y mantenga publicación deshabilitada. Ejecute ciclos hasta
   `COMMITTED`; esto consume Codex y modifica únicamente el workspace/commit local:

   ```bash
   .venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor-local.json once
   ```

6. Tras inspeccionar commit y validación, use una configuración con
   `publication_enabled: true` y ejecute ciclos hasta `PR_OPEN`. Esto publica rama y PR:

   ```bash
   .venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor-publish.json once
   ```

7. En GitHub, solicite un cambio claramente dentro del alcance. Ejecute `once` para
   polling/routing; autorice explícitamente la corrección con la CLI M04.4 y continúe
   por ejecución, validación, commit, push y respuesta. Consume Codex y escribe GitHub.
8. Apruebe y fusione manualmente el PR. Nunca use vibecoder para el merge.
9. Ejecute `once` hasta que el merge quede verificado, la tarea `COMPLETED` y sus
   dependientes resulten elegibles, pero no ejecutados automáticamente fuera del plan.

## Reinicio, pausa e idempotencia

Durante una etapa local segura, termine con SIGTERM y reinicie:

```bash
timeout --signal=TERM 10s .venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json run
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json reconcile
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json status
```

Pruebe pausas persistentes:

```bash
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json pause --inference --actor operador --reason prueba
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json once
.venv/bin/python -m vibecoder.supervisor --config /ruta/supervisor.json resume --actor operador
```

Repita `once` después de cada estado confirmado: no debe duplicar turno Codex, commit,
push, comentario ni PR. Si aparece `UNKNOWN`, no continúe automáticamente; inspeccione
el workflow remoto/local y use su reconciliación específica.

## Criterios de éxito

- Issue → plan aprobado → tareas materializadas → especialista → PR.
- Review humana → corrección autorizada → mismo PR y rama, sin force push.
- Merge humano verificado → tarea completada → dependencia elegible.
- Reinicio y pausa no duplican efectos.
- No hay merges/cierres automáticos ni operaciones fuera del plan.
- Tokens no aparecen en jobs, logs, workspace, commits o respuestas.
