# Integración en Debian Sid

M06.6.1 se validó el 10 de noviembre de 2026 en la VM autorizada
`192.168.56.6`: Debian GNU/Linux forky/sid, kernel
`7.3.3+deb14-amd64`, systemd `262`, Python `3.14.10` y Git `2.55.0`.

La instalación se preparó en `/opt/vibecoder-integration` mediante un entorno
virtual que reutiliza paquetes Python oficiales del sistema. No se construyó ni
se declaró validado el paquete Debian definitivo de M06.9.

Los servicios `vibecoderd` y `vibecoder-worker-launcher` quedaron habilitados y
activos. SQLite superó `integrity_check` y `foreign_key_check`; el estado final
es `STANDBY/OFFLINE` por ausencia deliberada de GitHub, con capacidad de
ejecución `AVAILABLE`.

La suite live se mantiene separada en `tests/integration` y requiere opt-in. La
suite offline nunca usa red, systemd, GitHub, Codex real ni cuota.
