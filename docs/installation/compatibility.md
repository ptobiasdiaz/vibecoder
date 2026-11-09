# Compatibilidad de distribuciones

- **Debian 13 trixie — soportada.** Instalación, systemd, administración,
  actualización, backup y restore validados.
- **Debian sid — validación continua.** Validado el snapshot de 2026-11-09
  (`forky/sid`, systemd 262, Python 3.14). Unstable no implica garantía futura.
- **Ubuntu 24.04 LTS — experimental.** 0.4.0 funciona con Pydantic 1.10/HTTPX
  0.26, pero no existe compromiso de soporte ni upgrade desde 0.3.0.
- **Ubuntu 26.04 LTS — futura.** No evaluada ni soportada.

No mezcle repositorios entre distribuciones ni instale dependencias Python con
`pip --break-system-packages`.
