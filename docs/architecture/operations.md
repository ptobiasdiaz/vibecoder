# Operación, cuentas, cuotas y Telegram

## Multicuenta

Las cuentas Codex son entidades independientes de proyectos y agentes. Un proyecto se asigna a una cuenta configurada; los límites se comparten entre proyectos de la misma cuenta. No suponer que una sesión puede trasladarse entre cuentas. Mantener credenciales aisladas.

## Uso y límites

Registrar por ejecución, agente, proyecto y cuenta los tokens/estadísticas disponibles, tiempos y errores. Diferenciar uso observado, estimaciones y facturación real. Consultar los límites y ventanas de restablecimiento mediante mecanismos oficiales disponibles en la versión instalada de Codex; verificar capacidades reales durante el hito técnico.

Al detectar agotamiento: marcar `WAITING_QUOTA`, notificar, no consumir reintentos repetidos, comprobar disponibilidad tras reset y reanudar de forma segura. Distinguir de errores de red, autenticación y workers.

## Telegram

Bot restringido a chats/usuarios autorizados. Comandos iniciales: `/status`, `/usage`, `/projects`, `/tasks`, `/errors`. Más adelante: `/pause`, `/resume` con control de permisos. Notificaciones de cuota agotada/restablecida, bloqueos, errores y PR preparados. No aprobar implementación ni fusionar PR por Telegram.

## Métricas

Exponer métricas Prometheus: trabajos por estado, duración, fallos, cola, sesiones activas, disponibilidad de cuentas y uso observable. Monitorización externa de VM/host fuera de alcance.

## Recuperación

Persistir antes y después de efectos externos, deduplicar webhooks, reanudar trabajos pendientes y reconciliar ejecuciones inciertas. No repetir ciegamente cambios en GitHub o commits.
