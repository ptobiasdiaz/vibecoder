# Resultados finales de M05

Fecha de cierre: 2026-11-09. Versión: vibecoder 0.4.0, esquema SQLite 15.

## Política de soporte

| Plataforma | Política | Evidencia |
|---|---|---|
| Debian 13 trixie | Soportada | ciclo completo M05.5, incluido upgrade 0.3→0.4 |
| Debian sid | Validación continua | ciclo completo M05.5.1 sobre snapshot 2026-11-09 |
| Ubuntu 24.04 LTS | Experimental | instalación 0.4 y operación probadas; upgrade histórico no soportado |
| Ubuntu 26.04 LTS | Futuro | no evaluada |

## Matriz consolidada

| Área | trixie | sid | Ubuntu 24.04 |
|---|---|---|---|
| APT/debconf/systemd real | Superada | Superada | Superada (experimental) |
| CLI/READY explícito/permisos | Superada | Superada | Superada (experimental) |
| restart/SIGTERM/reboot | Superada | Superada | Superada (experimental) |
| backup/restore/barrera/rollback | Superada | Superada | Superada (experimental) |
| upgrade auténtico 0.3→0.4 | Superada | Superada | No soportado por dependencias de 0.3 |
| remove/reinstall/purge conservador | Superada | Superada | Superada (experimental) |

La validación sid observó kernel 7.3.3, systemd 262, Python 3.14.10, Pydantic
2.14.1, HTTPX 0.28.1, Git 2.55.0 y SQLite 3.54.1. El build ejecutó 293 pruebas
(`OK`, 45 omitidas) y lintian sin tags. El SHA-256 definitivo debe tomarse del
artefacto reconstruido después de incorporar esta documentación.

## Riesgos residuales aceptados

- Un runtime histórico 0.3 no rechaza por sí mismo un esquema 15. Dpkg tampoco
  ofrece desde el paquete instalado una barrera universal contra todos los
  downgrades. La mitigación soportada es restore de backup compatible antes de
  instalar el runtime anterior.
- Sid cambia continuamente; cada snapshot requiere volver a validar.
- Los backups contienen secretos y no están cifrados por vibecoder.
- Ubuntu no forma parte del compromiso de soporte de M05.

No quedan defectos críticos abiertos dentro de la política publicada. M05 queda
cerrado; M06 puede comenzar sin ampliar implícitamente el soporte de plataformas.
