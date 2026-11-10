# ADR-0031 — Codex detrás de la frontera de plugin

Estado: aceptado para M06.6.

El cliente App Server, autenticación y semántica Codex viven exclusivamente en
el runtime aislado. El núcleo recibe capacidades, outcomes y referencias opacas.
La instalación se inyecta mediante un contrato neutral y no importa el cliente.

La validación live fijó `0.167.0`; no se usa `latest`. Una instalación gestionada
solo se publica con manifiesto confiable y SHA-256. Device Code se detecta, no se
presupone. Cancelación y reconciliación no se declaran sin evidencia live.

M06.6.1 confirmó Device Code, App Server y continuidad de sesión. El esquema
oficial contiene aprobaciones, `turn/interrupt` y `thread/read`, pero estas
capacidades permanecen sin anunciar hasta disponer de mediación humana y
confirmación terminal segura.

M06.6.2 verificó las aprobaciones de comandos y mantiene la traducción de los
tipos Codex dentro del plugin. El núcleo persiste una solicitud neutral y la
decisión humana. Se exige `codex-code-mode-host` junto al ejecutable: sin él,
Code Mode falla cerrado y el worker no puede considerarse operativo.
