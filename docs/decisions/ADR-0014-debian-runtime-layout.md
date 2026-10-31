# ADR-0014 — Paquete Debian y layout del runtime

## Estado

Aceptada para M05.1.

## Decisión

El runtime se distribuye como paquete Debian `Architecture: all`, construido con
`debhelper`, `dh-python` y `pybuild-plugin-pyproject`. Las dependencias Python se
resuelven mediante paquetes Debian; los scripts de mantenimiento no invocan `pip`, no
descargan desde PyPI y no alteran el Python administrado por la distribución.

El servicio usa la identidad de sistema `vibecoder:vibecoder`. La configuración vive
en `/etc/vibecoder`, los datos en `/var/lib/vibecoder`, el runtime efímero en
`/run/vibecoder` y los logs en journald. systemd proporciona temporales privados.

La configuración tiene un estado operativo explícito. El paquete instala
`NOT_CONFIGURED`: systemd puede estar activo y aplicar migraciones, pero el supervisor
no selecciona trabajos ni realiza efectos externos. M05.2/M05.3 serán responsables de
validar la configuración y cambiarla a `READY`.

`postinst` crea exclusivamente la identidad y directorios necesarios, sin clonar,
autenticar ni ejecutar Codex. `remove` y `purge` preservan deliberadamente
`/var/lib/vibecoder`, el usuario y workspaces; su eliminación es una operación
administrativa explícita.

## Consecuencias

- La instalación no necesita Internet salvo para resolver previamente el paquete y
  sus dependencias APT.
- Codex no es una dependencia Debian: se configura después mediante una ruta
  ejecutable y `CODEX_HOME` por cuenta.
- El hardening de systemd reduce superficie, pero no convierte la ejecución de código
  generado en un sandbox fuerte.
- Solo se declara verificada una distribución cuando el `.deb` se construye e
  inspecciona en ella; la unidad requiere una prueba separada bajo systemd real.
