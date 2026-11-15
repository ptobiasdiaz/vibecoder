# Compatibilidad de distribuciones

- **Debian 13 trixie amd64 — soportada.** Es la plataforma objetivo de 0.0.1;
  el paquete requiere Python 3.11+ y systemd.
- **Debian sid — validación continua.** Validado el snapshot de 2026-11-09
  (`forky/sid`, systemd 262, Python 3.14). Unstable no implica garantía futura.
- **Ubuntu 24.04 LTS — histórico/experimental.** La validación corresponde a
  0.4.0; el paquete 0.0.1 no se declara validado ni soportado allí.
- **Ubuntu 26.04 LTS — futura.** No evaluada ni soportada.

No mezcle repositorios entre distribuciones ni instale dependencias Python con
`pip --break-system-packages`.
