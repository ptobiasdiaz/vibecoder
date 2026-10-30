# Operación, cuentas, cuotas y Telegram

## Multicuenta

Las cuentas Codex son entidades independientes de proyectos y agentes. Un proyecto se asigna a una cuenta configurada; los límites se comparten entre proyectos de la misma cuenta. No suponer que una sesión puede trasladarse entre cuentas. Mantener credenciales aisladas.

Varios agentes pueden compartir una cuenta manteniendo threads independientes. Cada
cuenta dispone de su propio entorno de credenciales y sesiones mediante `CODEX_HOME` o
el mecanismo oficial equivalente de la versión desplegada. El gestor de autenticación,
el gestor de sesiones y el ejecutor son componentes lógicamente desacoplados.
Un especialista puede usar una cuenta distinta; cambiarla exige suspenderlo. Sus
sesiones históricas conservan la cuenta original y no se reanudan bajo otro
`CODEX_HOME`: la identidad lógica permanece estable y la siguiente sesión compatible
se crea de forma independiente.

Se contemplan login persistente con ChatGPT, API key y access tokens de Codex cuando
el tipo de cuenta y la versión los soporten. Los agentes reutilizan la autenticación
de la cuenta y no ejecutan `codex login` en cada arranque. La disponibilidad concreta,
facturación, límites y renovación de cada método deben verificarse antes de habilitarlo.

Las credenciales no se guardan en GitHub, comentarios ni en texto plano en la base de
datos de aplicación. Deben permanecer en almacenamiento de secretos o credenciales
apropiado. Las sesiones no se transfieren automáticamente entre cuentas.

## Uso y límites

Registrar por ejecución, agente, proyecto y cuenta los tokens/estadísticas disponibles, tiempos y errores. Diferenciar uso observado, estimaciones y facturación real. Consultar los límites y ventanas de restablecimiento mediante mecanismos oficiales disponibles en la versión instalada de Codex; verificar capacidades reales durante el hito técnico.

Al detectar agotamiento: marcar `WAITING_QUOTA`, notificar, no consumir reintentos repetidos, comprobar disponibilidad tras reset y reanudar de forma segura. Distinguir de errores de red, autenticación y workers.

Los errores de autenticación son recuperables y se registran por separado del
agotamiento de cuota. La recuperación ante credenciales caducadas o revocadas y el
comportamiento de límites bajo cada método de autenticación se validarán en hitos
posteriores.

## Telegram

Bot restringido a chats/usuarios autorizados. Comandos iniciales: `/status`, `/usage`, `/projects`, `/tasks`, `/errors`. Más adelante: `/pause`, `/resume` con control de permisos. Notificaciones de cuota agotada/restablecida, bloqueos, errores y PR preparados. No aprobar implementación ni fusionar PR por Telegram.

## Métricas

Exponer métricas Prometheus: trabajos por estado, duración, fallos, cola, sesiones activas, disponibilidad de cuentas y uso observable. Monitorización externa de VM/host fuera de alcance.

## Recuperación

Persistir antes y después de efectos externos, deduplicar webhooks, reanudar trabajos pendientes y reconciliar ejecuciones inciertas. No repetir ciegamente cambios en GitHub o commits.
