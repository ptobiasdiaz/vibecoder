# Instalación en Debian y Ubuntu

## Compatibilidad

Vibecoder requiere Python 3.11 o posterior, `httpx >= 0.27` y `pydantic >= 2.7`. El
paquete usa dependencias de la distribución y no ejecuta `pip` en el host. La
construcción de M05.1 se verifica en Debian forky/sid; Debian y Ubuntu anteriores solo
son compatibles si proporcionan esas versiones. No se declara validación sobre Ubuntu
hasta probar el artefacto en una versión concreta.

Codex CLI/App Server no se instala automáticamente. Su ruta se declarará por cuenta.
Git y certificados CA son dependencias obligatorias.

## Construcción

```bash
sudo apt install build-essential debhelper dh-python pybuild-plugin-pyproject \
  python3-all python3-setuptools python3-httpx python3-pydantic fakeroot
dpkg-buildpackage -us -uc -b
dpkg-deb --info ../vibecoder_0.3.0_all.deb
dpkg-deb --contents ../vibecoder_0.3.0_all.deb
```

El artefacto se crea en el directorio padre. No incluye `.git`, `.venv`, tests, bases
de datos, sesiones, logs, workspaces ni credenciales.

La validación M05.1 produjo un paquete reproducible en Debian forky. Publique el
resultado de `sha256sum` junto al artefacto; no lo inserte dentro del propio paquete,
porque crearía una referencia circular.

## Instalación y primer arranque

```bash
sudo apt install ./vibecoder_0.3.0_all.deb
systemctl status vibecoder.service
journalctl -u vibecoder.service
```

Debhelper instala y habilita la unidad. El primer arranque queda en `NOT_CONFIGURED`:
el proceso permanece vivo, inicializa SQLite si procede y no consulta GitHub ni
ejecuta Codex.

```bash
sudo -u vibecoder vibecoder-supervisor \
  --config /etc/vibecoder/supervisor.json config-check
sudo -u vibecoder vibecoder-supervisor \
  --config /etc/vibecoder/supervisor.json status
```

M05.2 implementa la transición administrada a `READY`:

```bash
sudo vibecoder-admin doctor
sudo vibecoder-admin setup validate
sudo vibecoder-admin setup configure
sudo vibecoder-admin setup activate
```

No cambie el JSON manualmente. M05.3 añadirá la preparación mediante debconf.

## Usuario, directorios y permisos

| Ruta | Propietario/modo | Finalidad |
| --- | --- | --- |
| `/etc/vibecoder` | `root:vibecoder`, `0750` | configuración |
| JSON de configuración | `root:vibecoder`, `0640` | configuración no secreta |
| `/var/lib/vibecoder` | `vibecoder:vibecoder`, `0750` | base y estado persistente |
| `/var/lib/vibecoder/accounts` | `vibecoder:vibecoder`, `0750` | `CODEX_HOME` por cuenta |
| `/var/lib/vibecoder/workspaces` | `vibecoder:vibecoder`, `0750` | workspaces aislados |
| `/var/lib/vibecoder/secrets` | `vibecoder:vibecoder`, `0700` | secretos individuales `0600` |
| `/run/vibecoder` | `vibecoder:vibecoder`, `0750` | estado efímero |

No se crea un home interactivo ni se copia autenticación del instalador. Un archivo
opcional `/etc/vibecoder/environment` debe ser `root:vibecoder` y `0640`; nunca se
versiona ni se muestra en logs.

## systemd y seguridad

La unidad usa journald, reinicia ante fallo y entrega SIGTERM con 45 segundos para el
cierre. Aplica `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`,
capabilities vacías, `UMask=0027` y escritura limitada al estado y runtime.

`ProtectHome=true` implica que Codex no debe depender de un home personal: el
ejecutable debe estar en una ruta de sistema y cada `CODEX_HOME` bajo
`/var/lib/vibecoder/accounts`. Esto no es aislamiento fuerte para código generado.

## Actualización y eliminación

```bash
sudo apt install ./vibecoder_NUEVA_VERSION_all.deb
sudo apt remove vibecoder
sudo apt purge vibecoder
```

Las actualizaciones conservan conffiles y `/var/lib/vibecoder`; SQLite aplica
migraciones incrementales. Tanto `remove` como `purge` preservan datos, sesiones,
workspaces e identidad. Tras archivarlos, un administrador puede eliminarlos
manualmente. Nunca se eliminan workspaces externos como efecto de la purga.
