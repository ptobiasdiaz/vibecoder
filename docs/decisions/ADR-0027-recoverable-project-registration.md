# ADR-0027 — Registro recuperable de proyectos

El registro usa la identidad numérica GitHub y una clave de idempotencia de
repositorio y rama. Persiste `PREPARING`, prepara un clon temporal autenticado
mediante `GIT_ASKPASS`, verifica el SHA y lo mueve al nombre definitivo.
Proyecto, repositorio, workspace, autorización inicial y operación completada
se confirman en una transacción SQLite.

Un reintento verifica cualquier workspace definitivo dejado antes de la
transacción y completa el registro; no lo borra ciegamente. El remoto persistido
es HTTPS sin credenciales. El PAT sólo existe en el almacén del daemon y el
entorno efímero de Git, nunca en argv, configuración, auditoría o plugins.

La identidad global queda autorizada en la misma transacción. SQLite impide
eliminar el último usuario autorizado.

