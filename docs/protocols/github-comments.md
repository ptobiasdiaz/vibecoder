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
