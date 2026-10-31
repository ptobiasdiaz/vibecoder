# Protocolo de comentarios GitHub (borrador v0)

## Principio

El texto es Markdown libre. El protocolo visible se limita a convenciones opcionales, fáciles para humanos:

- `/agent main` o `/agent <id>`: destinatario explícito.
- `/agent human`: solicitud de intervención humana.
- `/reply Q-17`: vincula una respuesta a una pregunta cuando hay ambigüedad.

Las directivas solo se reconocen al inicio de línea, fuera de citas y bloques de código. Si no hay destinatario, en Issues se dirige al principal; en PR se dirige al especialista responsable si es único, y en caso ambiguo se solicita aclaración o se escala al principal.

## Reglas

1. Los comentarios humanos ordinarios no requieren comandos.
2. El orquestador publica prompts y respuestas en el mismo Issue; en PR publica discusiones de revisión técnica.
3. Cada comentario generado por el sistema identifica claramente agente, ejecución y tipo de mensaje. Los metadatos internos pueden incluirse en comentarios HTML, pero nunca se consideran prueba de identidad.
4. Verificar autor y estado interno; ignorar mensajes propios salvo correlaciones expresamente esperadas para evitar bucles.
5. Las preguntas tienen ID estable, destinatario y estado (`OPEN`, `ANSWERED`, `BLOCKED`, `CANCELLED`). La sesión que pregunta termina el turno y se reanuda cuando llegue respuesta.
6. Detectar dependencias circulares entre preguntas y escalar a humano.
7. Una pregunta dirigida a un agente no lo incorpora automáticamente a los responsables del Issue.
8. El texto de comentarios es entrada no confiable; los permisos los controla el orquestador.

## Ejemplo

```markdown
/agent hal

¿La interfaz de memoria admite lectura por bloques?
```

```markdown
/reply Q-17

Sí, mediante la operación `read_block`, sin cambiar el contrato público.
```

## Por validar

Mecanismo exacto de correlación de preguntas cuando un comentario contiene varias solicitudes; manejo de ediciones y borrados; comentarios de revisión inline; distinción entre respuesta de Codex y solicitudes de acción. Empezar con una pregunta pendiente por turno de agente.

## Perfil M03.2 — Bootstrap

- Un comentario ordinario autorizado se dirige a `main`.
- `/agent main` es el único destinatario explícito aceptado en este subhito.
- Otras directivas se registran como rechazadas y no crean tareas.
- La identidad se obtiene del campo `user` de GitHub; los marcadores HTML no autentican.
- Los comentarios de actores tipo `Bot` no crean tareas.
- Con un token personal, compartir login con vibecoder no convierte una intervención
  humana en comentario del bot. Solo se considera respuesta propia el comentario cuyo
  ID remoto está vinculado a una operación `CREATE_COMMENT` persistida.
- Cada respuesta visible comienza con `vibecoder · agente main` y conserva un marcador
  opaco de idempotencia gestionado por el adaptador.
- Tras aprobar el bootstrap, comentarios posteriores se registran pero no alteran la
  especificación congelada.
- La configuración Git solo se confirma con un único bloque JSON
  `vibecoder-git-config` que declare `confirmation: confirmed` y proceda de un usuario
  autorizado. La prosa, los ejemplos y los bloques del agente son propuestas.

## Perfil M03.4 — Pull Request de bootstrap

- El cuerpo incluye Issue, generación, hashes, ramas y commit publicado.
- Un marcador HTML de operación permite reconciliar una respuesta perdida, pero no
  sustituye la verificación de repositorio, IDs, refs, SHA y autorización persistida.
- Vibecoder no publica instrucciones para autoaprobar o fusionar el PR.
- `closed` sin `merged=true` se trata como cierre sin integración y bloquea activación.

## Perfil M04.2 — Issues y planes

- Un comentario ordinario o `/agent main` se dirige al principal.
- `/agent <agent-id>` permite una consulta a un especialista activo; no autoriza
  implementación ni altera un plan aprobado.
- Las respuestas identifican al agente y se correlacionan por IDs remotos persistidos.
- El plan visible es Markdown con `revision` y `sha256`; la fuente de verdad es JSON
  validado y congelado.
- `status:approved` solo autoriza la revisión con un evento nuevo, atribuible y
  posterior a su publicación. Una etiqueta antigua nunca se hereda.

## Perfil M04.3 — Pull Requests de implementación

- El PR solo procede de una tarea materializada y un workflow M04.3 autorizado.
- Incluye marcador idempotente, Issue, revisión del plan, tarea, especialista, commit
  y validaciones; una respuesta perdida se reconcilia con esos datos.
- Los comentarios no autorizan ejecución, push o merge.
- Vibecoder no aprueba ni fusiona estos PRs en M04.3.

## Perfil M04.4 — Reviews

- Comentarios generales, reviews y comentarios de línea conservan tipo e ID propios.
- Por defecto las observaciones técnicas se enrutan al especialista; `/agent main`
  escala alcance y coordinación.
- `CHANGES_REQUESTED` no concede autorización ilimitada. Cada corrección necesita
  clasificación y autorización vinculadas a eventos concretos.
- Una aprobación solo se considera vigente para el HEAD revisado. Merge se verifica
  separadamente; `closed` no significa `merged`.
