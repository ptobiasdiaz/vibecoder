# ADR-0003 — Identidad e idempotencia de operaciones GitHub

Estado: Aceptado para M03.1.

## Contexto

El nombre, propietario y URL de un repositorio pueden cambiar, mientras que los
subhitos de bootstrap y conversación necesitan conservar una asociación estable con
el proyecto. GitHub no ofrece claves de idempotencia generales para crear Issues o
comentarios y un fallo de transporte puede ocurrir después de aceptar una escritura.

## Decisión

1. La identidad del repositorio es el `id` numérico asignado por GitHub; `node_id`,
   propietario, nombre y URLs se conservan como metadatos sincronizables.
2. Un proyecto tiene como máximo un repositorio registrado y un repositorio solo
   puede pertenecer a un proyecto.
3. El token se inyecta externamente al adaptador y nunca se persiste en SQLite.
   El diseño del puerto permite sustituirlo por credenciales de una GitHub App.
4. Cada escritura lógica registra antes una operación local con tipo, clave de
   idempotencia y hash del payload.
5. Issues y comentarios creados incluyen un marcador HTML opaco. Tras reinicios o
   resultados inciertos, se consulta GitHub antes de repetir la escritura.
6. Crear etiquetas se reconcilia por nombre y asignarlas se reconcilia contra el
   estado actual del Issue. No se promete semántica exactamente-una-vez.
7. Solo las lecturas y fallos HTTP transitorios inequívocos se reintentan
   automáticamente. Las escrituras no se reintentan a ciegas.

## Consecuencias

- Los renombrados no rompen la identidad local.
- Los marcadores son metadatos de correlación, no prueba de autenticidad ni comandos.
- La consistencia eventual de GitHub aún deja una ventana pequeña de duplicación; la
  operación queda auditable y reconciliable.
- El alcance de cada operación se obtiene siempre de un repositorio registrado, no de
  texto recibido desde Issues o comentarios.

