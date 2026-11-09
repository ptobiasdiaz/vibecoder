# Resultados live de M05

Fecha: 2026-11-08. No se utilizaron GitHub ni Codex reales.

## Inventario

| VM | Sistema observado | Kernel | systemd | Python |
|---|---|---|---|---|
| `192.168.56.6` | Debian 13.7 trixie amd64 | 6.12.115 | 257 | 3.13.5 |
| `192.168.56.3` | Ubuntu 24.04.5 LTS amd64 | 6.8.0-88 | 255 | 3.12.3 |

PID 1 fue systemd en ambos sistemas. SSH usó un `known_hosts` temporal dedicado.

## Matriz

| Prueba | Debian trixie | Ubuntu 24.04 |
|---|---|---|
| Instalación/APT/debconf | Superada | Superada |
| systemd, autostart, usuario no root | Superada | Superada |
| CLI, secreto ficticio, READY | Superada | Superada |
| Restart, SIGTERM, reboot | Superada | Superada |
| Journald sin secretos | Superada | Superada |
| Backup/verify/restore/barrera | Superada | Superada |
| Restore interrumpido/rollback | Superada | Superada |
| Upgrade auténtico 0.3.0→0.4.0 | Superada | Bloqueada por 0.3.0 |
| Migración 14→15/backup previo | Superada | No ejecutada |
| Falta de espacio/recuperación dpkg | No ejecutada | Superada |
| Config inválida/fallo de servicio | No ejecutada | Superada |
| Remove/reinstall/purge conservador | Superada | Superada |
| Runtime histórico 0.3 sobre esquema 15 | Fallida | No ejecutada |

## Evidencias y defectos

- Upgrade Debian: backup esquema 14; resultado esquema 15 y READY, conservando
  secreto ficticio, proyecto, cuenta y workspace local.
- Tras reboot cambiaron ambos boot IDs; unidades `enabled/active`, SQLite íntegro.
- `systemd-analyze security`: exposición 4.3 (`OK`) en ambas VMs.
- Restore: propietarios `vibecoder:vibecoder`, CONFIGURED y pausa.
- Fallo inyectado: journal FAILED, rollback ROLLED_BACK y datos recuperados.
- Purge conservó `/var/lib/vibecoder`.
- Se corrigieron dependencias noble, propietarios de restore y mensaje de
  reconfiguración.

El intento de bloquear downgrade desde el `prerm` instalado fue ineficaz: dpkg
recurre al script del paquete entrante. El runtime histórico 0.3 desconoce el
esquema 15 y no puede corregirse retroactivamente. Los runtimes desde 0.4 sí
rechazan esquemas futuros; el rollback soportado exige restaurar antes un backup
compatible.

## Resolución posterior

- M05.5.1 convirtió la VM Debian a sid auténtica y repitió el ciclo completo.
- Ubuntu 24.04 pasó a compatibilidad experimental; su paquete histórico no bloquea
  el soporte oficial Debian.
- La incapacidad del runtime histórico 0.3.0 de rechazar esquema 15 se documenta
  como limitación histórica y se mitiga mediante restore previo compatible.
