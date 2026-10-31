# ADR-0010 — Planes versionados y aprobación por evento no reutilizable

Estado: Aceptado para M04.2.

## Contexto

Un Issue puede necesitar varias revisiones del plan. La mera presencia de
`status:approved` no demuestra qué revisión se aprobó ni quién la autorizó. Además,
la conversación y las consultas de especialistas no deben confundirse con tareas de
implementación autorizadas.

## Decisión

1. Cada Issue ordinario tiene un workflow persistente independiente del bootstrap.
2. El plan es JSON validado y canónico; Markdown es solo su representación humana.
3. Las revisiones son inmutables y conservan hash, tareas y dependencias.
4. Publicar una revisión registra el ID y fecha del comentario remoto.
5. Aprobar exige un evento `labeled` de `status:approved`, atribuible a un usuario de
   la allowlist, posterior a la publicación exacta y no utilizado por otra revisión.
6. Una etiqueta antigua, aunque siga presente, no autoriza una revisión nueva. Se
   requiere retirarla y reaplicarla para producir un evento verificable.
7. La aprobación congela Issue, conversación, plan y snapshot de ID, cuenta, revisión
   de configuración y hashes de instrucciones de cada agente.
8. La materialización crea tareas `PLANNED_WORK` idempotentes, pero M04.2 prohíbe al
   worker ejecutarlas.
9. Los turnos `ISSUE_COORDINATION` e `ISSUE_CONSULTATION` son análisis, no
   implementación. Cada pareja Issue/agente utiliza una sesión propia.

## Consecuencias

- Una aprobación histórica sigue siendo auditable pero no amplía autorizaciones.
- Los reinicios no duplican comentarios, planes ni tareas.
- Un cambio posterior de cuenta, configuración o instrucciones bloquea la
  materialización; no sustituye silenciosamente el snapshot aprobado.
- Webhooks, scheduler y ejecución de `PLANNED_WORK` quedan fuera de M04.2.
