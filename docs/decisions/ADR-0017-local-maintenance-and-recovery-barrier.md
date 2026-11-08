# ADR-0017 — Mantenimiento local y barrera de recuperación

## Estado

Aceptada para M05.4.

## Decisión

Los backups son directorios privados versionados con manifiesto y payload, no archivos
tar. Solo contienen rutas relativas verificadas; se rechazan enlaces simbólicos,
hardlinks, archivos inesperados, modos o checksums diferentes. Esta elección permite
inspección y staging sin implementar un extractor de archivos privilegiado.

El backup manual requiere pausa global persistente, ausencia de ejecuciones/leases y
confirmación explícita de que el supervisor está detenido. Incluye configuración,
SQLite mediante su API de backup, secretos, `CODEX_HOME`, workspaces y estado. No
incluye `/run`, logs, el propio almacén de backups ni recovery.

La restauración crea primero un backup del estado actual, mantiene un journal fuera de
los componentes restaurados, aplica desde staging y valida después. Siempre cambia el
runtime a `CONFIGURED` y mantiene pausa global: restaurar estado local nunca demuestra
el estado de GitHub o Codex. La reactivación requiere reconciliación y decisión humana.

Durante upgrade Debian, debhelper detiene la unidad. `postinst` crea un backup
pre-migración antes de abrir el repositorio con el runtime nuevo, aplica migraciones y
verifica integridad. Jobs activos o leases hacen fallar la configuración del paquete
en vez de arriesgar duplicaciones.

## Consecuencias

- Los backups contienen secretos y no están cifrados; requieren protección `0700` y
  exportación explícita.
- No hay downgrade automático de esquema.
- Un runtime rechaza esquemas más recientes que sus migraciones incorporadas.
- La restauración multiarchivo no es globalmente atómica; journal, backup previo y
  rollback conservador permiten recuperarla.
