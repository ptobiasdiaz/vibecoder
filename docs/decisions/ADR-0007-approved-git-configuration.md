# ADR-0007 — Configuración Git estructurada y aprobación conjunta

Estado: Aceptado para M03.3.1.

## Contexto

La especificación conversacional no basta para autorizar inequívocamente la rama
principal, el primer commit de un repositorio vacío o las operaciones remotas futuras.
Inferir esos valores desde prosa mezclaría propuesta, confirmación y autorización.

## Decisión

1. La configuración usa un único bloque Markdown `vibecoder-git-config` con JSON de
   esquema cerrado versión 1.
2. Cada revisión tiene estado `PROPOSED`, `CONFIRMED`, `APPROVED` o `APPLIED`.
3. El agente presenta bloques `proposed`. Solo un comentario de un usuario autorizado
   puede publicar `confirmation: confirmed`.
4. Dos bloques, campos extra, valores incoherentes u operaciones desconocidas se
   rechazan. La prosa nunca confirma valores.
5. `status:approved` y su evento atribuible aprueban conjuntamente la conversación y
   la última revisión confirmada. Sin ambas no existe aprobación.
6. SHA-256 se calcula sobre JSON canónico sin estado ni `confirmation`; la aprobación
   añade hash de especificación, actor y evento.
7. Se captura ID GitHub, estado vacío, rama por defecto y SHA del HEAD. La deriva
   posterior bloquea y nunca se corrige silenciosamente.
8. Para un remoto vacío se autorizan exactamente commit base técnico mínimo,
   publicación de rama bootstrap y creación de PR, siempre con merge humano.
9. Para un remoto con commits se conserva la rama por defecto y no se autoriza un
   commit técnico.
10. M03.3 persiste ID/hash y marca la configuración `APPLIED`. Generaciones históricas
    se conservan, pero requieren una aprobación nueva antes de efectos remotos.

## Consecuencias

- M03.4 recibirá una autorización enumerable y auditable.
- Cambiar decisiones crea una revisión nueva.
- Los workflows aprobados no se reescriben retroactivamente.
- El JSON es estricto aunque permanece legible dentro de Markdown.
