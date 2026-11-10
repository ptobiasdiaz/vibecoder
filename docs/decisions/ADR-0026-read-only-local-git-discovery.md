# ADR-0026 — Descubrimiento Git local de solo lectura

La CLI inspecciona el checkout del administrador con comandos Git de solo
lectura y envía una observación estructurada. El daemon no la considera
autoridad: vuelve a consultar repositorio, rama y SHA mediante el PAT global.

Se admiten HTTPS, SCP SSH y `ssh://` de `github.com`. Se rechazan HEAD separado,
remotos ambiguos y credenciales embebidas. No se ejecutan `fetch`, `pull`,
`checkout`, `reset` ni `clean` en el checkout; Git se invoca sin shell, prompts,
configuración global ni hooks.

`--ignore-dirty` autoriza ignorar cambios y commits locales no publicados. No
los copia ni borra: el workspace parte del SHA devuelto por GitHub.

