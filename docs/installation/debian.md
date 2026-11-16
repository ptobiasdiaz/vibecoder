# Instalación Debian de vibecoder 0.0.1

## Compatibilidad

El artefacto actual es `vibecoder_0.0.1_amd64.deb`. Debian 13 trixie amd64
es la plataforma soportada y Debian sid amd64 se valida continuamente sobre
versiones concretas. Requiere systemd como PID 1 y Python 3.11 o posterior.

El paquete declara `python3-httpx`, `python3`, `adduser`, `ca-certificates`,
`git` y `systemd-sysv`; APT resuelve estas dependencias desde Debian. No usa
`pip` sobre el Python del sistema.

## Instalación y arranque

```bash
sudo apt install ./vibecoder_0.0.1_amd64.deb
vibecoder --version
sudo systemctl status vibecoder vibecoder-worker-launcher
sudo vibecoder status
sudo vibecoder doctor
```

El paquete crea el usuario no interactivo `vibecoder`, los grupos
`vibecoder-admin` y `vibecoder-runtime`, habilita ambos servicios y crea los
directorios persistentes. Sin GitHub ni workers, `STANDBY/OFFLINE` es el estado
sano esperado.

Autorice explícitamente cada administrador local:

```bash
sudo usermod -aG vibecoder-admin "$USER"
# cierre la sesión y vuelva a entrar
vibecoder status
```

No se añaden usuarios humanos automáticamente.

## Primera configuración

```bash
vibecoder github login
vibecoder worker add codex
vibecoder worker codex-1 login
vibecoder worker codex-1 verify
cd ~/dev/proyecto
vibecoder project add
```

`github login` recibe el Fine-grained PAT en un prompt oculto. Codex se instala
mediante el gestor verificado, usa Device Code bajo el UID del worker y guarda
sus credenciales en un `CODEX_HOME` privado. Los workers Codex nuevos usan
`autonomous`; `--approval-policy manual` conserva decisiones por acción.

## Directorios y permisos

| Ruta | Acceso esperado | Finalidad |
| --- | --- | --- |
| `/var/lib/vibecoder` | daemon; traversal mínimo para workers | SQLite y estado |
| `/var/lib/vibecoder/secrets` | `vibecoder`, `0700`; archivos `0600` | secretos globales |
| `/var/lib/vibecoder/workers` | storage separado por UID | Codex homes y sesiones |
| `/var/lib/vibecoder/workspaces` | asignación explícita | workspaces gestionados |
| `/var/lib/vibecoder/backups` | acceso administrativo restringido | backups con secretos |
| `/run/vibecoder/control.sock` | `vibecoder:vibecoder-admin`, `0660` | CLI administrativa |
| `/run/vibecoder/runtime-approval.sock` | grupo runtime, `0660` | broker runtime |

El daemon ejecuta como `vibecoder`. Cada worker productivo usa un UID distinto.
El launcher root solo acepta operaciones y rutas de política fija; no es un
shell privilegiado genérico.

## systemd y logs

```bash
systemctl status vibecoder vibecoder-worker-launcher
journalctl -u vibecoder -u vibecoder-worker-launcher --since today
```

Las unidades aplican filesystem restringido, `NoNewPrivileges` donde procede,
capabilities vacías y sockets Unix. Las unidades transitorias Codex no usan
`RestrictSUIDSGID` porque systemd 262 impediría el bubblewrap interno; mantienen
UID independiente y el resto de la frontera documentada.

## Backup, actualización y eliminación

Antes de una actualización material, cree y verifique un backup:

```bash
vibecoder pause
vibecoder backup create
vibecoder backup list
vibecoder backup verify ID
vibecoder resume
sudo apt install ./vibecoder_0.0.1_amd64.deb
```

Los upgrades y reinstalaciones conservan base, proyectos, secretos y workers.
`apt remove vibecoder` conserva los datos. `apt purge vibecoder` elimina
explícitamente `/var/lib/vibecoder`, sus secretos y las identidades de workers;
cree y exporte un backup antes de usarlo.

Un restore crea siempre una barrera:

```bash
vibecoder pause
vibecoder backup restore ID
vibecoder maintenance status
vibecoder maintenance reconcile
vibecoder maintenance clear-barrier ID_BARRERA
vibecoder resume
```

`resume` no elimina barreras. Los backups contienen secretos sin cifrar.

## Construcción

```bash
sudo apt install build-essential debhelper dh-python pybuild-plugin-pyproject \
  python3-all python3-setuptools python3-httpx fakeroot
dpkg-buildpackage -us -uc -b
dpkg-deb --info ../vibecoder_0.0.1_amd64.deb
dpkg-deb --contents ../vibecoder_0.0.1_amd64.deb
```

El paquete no contiene `.git`, `.venv`, bases runtime, tokens, workspaces ni
credenciales. Consulte el [tutorial completo](../tutorial.md).
