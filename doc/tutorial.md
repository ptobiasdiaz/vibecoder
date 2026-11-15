# Tutorial de vibecoder 0.0.1 — De la instalación al primer Pull Request

Este tutorial instala vibecoder en Debian, conecta una identidad GitHub y un
worker Codex, registra un repositorio y recorre el flujo completo hasta un Pull
Request. Está dirigido a personas familiarizadas con Linux, Git y GitHub.

## 1. Qué es vibecoder

Vibecoder es un orquestador persistente de desarrollo. El daemon
`vibecoderd` observa proyectos autorizados en GitHub, conserva el estado en
SQLite y coordina planes, tareas, workspaces, commits y Pull Requests. Un
**worker** es una instancia aislada de un proveedor de IA. En 0.0.1, el plugin
real disponible es Codex App Server.

Vibecoder controla Git y GitHub; Codex recibe únicamente el workspace asignado
y realiza el trabajo de desarrollo. El PAT de GitHub nunca se entrega al
worker. Una persona debe aprobar el hash exacto del plan. En la política
`autonomous`, Codex puede leer y escribir en su workspace y ejecutar tests sin
confirmaciones ordinarias. El sandbox sigue activo y sin acceso de red. El
merge nunca es automático.

```text
Usuario
   │
   ▼
GitHub Issue
   │
   ▼
Plan generado por vibecoder
   │
   ▼
Aprobación humana
   │
   ▼
Worker Codex
   │
   ▼
Código + tests + commit
   │
   ▼
Pull Request
   │
   ▼
Revisión humana
   │
   ▼
Merge humano
```

## 2. Requisitos previos

Obligatorios:

- Debian 13 (trixie) amd64. Debian sid amd64 se usa como plataforma de
  validación continua; una actualización futura de unstable podría requerir
  nuevas comprobaciones.
- systemd como PID 1 y Python 3.11 o posterior.
- Acceso administrativo para instalar el paquete y añadir administradores al
  grupo local `vibecoder-admin`.
- Git, conectividad HTTPS y certificados CA. El paquete declara estas
  dependencias y APT las resuelve.
- Una cuenta GitHub y un Fine-grained Personal Access Token.
- Una cuenta compatible con el Device Code de Codex.

Opcionales:

- `sqlite3` para diagnóstico externo. No se necesita para operar vibecoder y
  no debe usarse para modificar la base.
- `lintian` y herramientas de build, únicamente para reconstruir el paquete.

Ubuntu 24.04 tuvo validación experimental en la línea histórica 0.4.0, pero el
paquete 0.0.1 amd64 no se declara soportado allí. No se ha validado 0.0.1 en
otras distribuciones ni arquitecturas.

## 3. Instalación

Sitúe `vibecoder_0.0.1_amd64.deb` en el directorio actual e instálelo con APT:

```bash
sudo apt install ./vibecoder_0.0.1_amd64.deb
```

El paquete crea:

- el usuario de servicio `vibecoder`;
- los grupos `vibecoder-admin` y `vibecoder-runtime`;
- `vibecoder.service`, que ejecuta el daemon sin privilegios;
- `vibecoder-worker-launcher.service`, un launcher de política limitada;
- `/var/lib/vibecoder` para estado persistente y `/run/vibecoder` para sockets.

Compruebe la instalación:

```bash
vibecoder --version
sudo systemctl status vibecoder vibecoder-worker-launcher
sudo vibecoder status
sudo vibecoder doctor
sudo journalctl -u vibecoder -u vibecoder-worker-launcher --no-pager
```

Una instalación nueva correcta muestra `vibecoder 0.0.1`, ambos servicios
activos y `STANDBY/OFFLINE`. No es un error: significa que aún no existe una
identidad GitHub utilizable. El daemon permanece activo pero no ejecuta trabajo
externo.

## 4. Autorizar la CLI administrativa

La CLI habla con `/run/vibecoder/control.sock`; nunca abre SQLite. `root` tiene
acceso y los administradores ordinarios se autorizan explícitamente:

```bash
sudo usermod -aG vibecoder-admin "$USER"
```

Cierre la sesión Linux y vuelva a entrar para renovar los grupos. Después:

```bash
id
vibecoder status
vibecoder doctor
```

`id` debe incluir `vibecoder-admin`. El paquete no añade automáticamente
usuarios humanos a ese grupo.

## 5. Crear el Fine-grained PAT de GitHub

En la interfaz web de GitHub:

1. Abra su avatar, **Settings**.
2. Abra **Developer settings**.
3. Entre en **Personal access tokens** y **Fine-grained tokens**.
4. Pulse **Generate new token**.
5. Asigne un nombre identificable y una caducidad razonable.
6. Elija el **Resource owner** que posee el repositorio.
7. En **Repository access**, seleccione solamente los repositorios que
   administrará vibecoder o todos los repositorios si esa amplitud está
   justificada.
8. Configure estos permisos de repositorio:

   | Permiso | Acceso | Uso |
   | --- | --- | --- |
   | Metadata | Read | Identidad, rama predeterminada y referencias |
   | Contents | Read and write | Clone/fetch/push, ramas y commits |
   | Issues | Read and write | Issues, comentarios y publicación de planes |
   | Pull requests | Read and write | PRs, reviews y comentarios de revisión |
   | Workflows | Write, solo si procede | Cambios autorizados bajo `.github/workflows/` |

9. No conceda permisos de administración del repositorio.
10. Genere el token, cópielo una sola vez y guárdelo en un gestor de secretos.

El acceso a repositorios forma parte del alcance del token. Si crea después un
repositorio y eligió **Only select repositories**, edite la selección del PAT
antes de registrarlo en vibecoder. Las organizaciones pueden exigir aprobación
del propietario.

La comprobación `GET /user` identifica la cuenta, pero no demuestra permisos
de escritura. GitHub tampoco ofrece una consulta fiable de todos los permisos
efectivamente concedidos. Vibecoder diagnostica `401`, `403` y el `404`
ambiguo, sin realizar escrituras ficticias.

## 6. Configurar GitHub

Ejecute el login en un terminal interactivo:

```bash
vibecoder github login
```

El PAT se solicita mediante entrada oculta. No se admite como argumento ni se
debe guardar en una variable o archivo de texto. El daemon lo valida, obtiene
automáticamente el login y el ID numérico y lo guarda fuera de SQLite en su
secret store privado.

Compruebe el resultado:

```bash
vibecoder github status
vibecoder status
```

Debe aparecer su login, ID y conectividad `ONLINE`. Sin workers operativos, el
estado público será `STANDBY/ONLINE`.

Para sustituir un token caducado, ejecute de nuevo `vibecoder github login`.
El nuevo PAT se valida antes de reemplazar el anterior. Si un token se
compromete, revóquelo primero en GitHub, genere otro y repita el login. Un fallo
transitorio de red no borra la credencial ni los proyectos.

## 7. Añadir y autenticar Codex

Consulte el plugin y cree un worker:

```bash
vibecoder plugin show codex
vibecoder worker add codex
vibecoder worker list --long
```

El alta detecta o instala la versión oficial compatible de Codex, verifica su
integridad, crea un UID aislado y un `CODEX_HOME` privado. Un worker Codex nuevo
usa `autonomous` por defecto. Si quiere decisiones por acción:

```bash
vibecoder worker add codex --name codex-manual --approval-policy manual
```

Autentique el worker predeterminado:

```bash
vibecoder worker codex-1 login
```

La CLI muestra la URL y el código públicos de Device Code. Abra la URL en su
navegador, introduzca el código y complete la autenticación. Las credenciales
quedan en el `CODEX_HOME` del worker, no en el daemon ni en el home del
administrador.

Verifique el worker:

```bash
vibecoder worker codex-1 verify
vibecoder worker status codex-1
vibecoder status
```

El worker debe mostrar `READY` y su política. Con GitHub ONLINE, la instancia
queda `READY/ONLINE`, aunque todavía no haya proyectos.

Puede cambiar la política cuando no existe una ejecución activa:

```bash
vibecoder worker set codex-1 --approval-policy manual
vibecoder worker set codex-1 --approval-policy autonomous
```

`autonomous` permite operaciones ordinarias dentro de `workspace-write`, sin
red y sin privilegios. `manual` conserva el broker por acción. Ninguna opción
aprueba planes ni merges.

## 8. Crear el repositorio de ejemplo

En GitHub, cree `vibecoder-tutorial`, público o privado, dentro del alcance del
PAT. Inicialícelo con README para disponer de un commit remoto y una rama
predeterminada. Después:

```bash
mkdir -p ~/dev
cd ~/dev
git clone https://github.com/SU_USUARIO/vibecoder-tutorial.git
cd vibecoder-tutorial
printf '[project]\nname = "vibecoder-tutorial"\nversion = "0.1.0"\n' > pyproject.toml
mkdir -p src tests
touch src/__init__.py tests/__init__.py
git add README.md pyproject.toml src/__init__.py tests/__init__.py
git commit -m "chore: initialize Python project"
git push origin HEAD
git status --short
git branch --show-current
```

Lo importante es que exista al menos un commit publicado y que el checkout
esté limpio y sincronizado.

## 9. Registrar el proyecto

Desde el checkout local:

```bash
vibecoder project add
vibecoder project list --long
vibecoder project status vibecoder-tutorial
vibecoder project vibecoder-tutorial users
```

La CLI inspecciona Git en modo de solo lectura. El daemon valida `origin` con
GitHub, obtiene el ID estable, la rama y el SHA remoto, y crea su propio
workspace desde GitHub. El checkout del usuario no se convierte en workspace y
no se modifica.

Reglas importantes:

- `origin` debe ser una URL HTTPS o SSH inequívoca de GitHub.
- El repositorio debe tener un commit publicado.
- Detached HEAD se rechaza.
- Si la rama local no es la predeterminada, use, por ejemplo,
  `vibecoder project add --branch develop`; debe coincidir con la rama actual y
  existir remotamente.
- Cambios locales o commits no publicados se rechazan. Con
  `--ignore-dirty`, vibecoder ignora esos cambios y parte explícitamente del SHA
  remoto; nunca los copia al workspace.

La identidad del PAT queda autorizada inicialmente. Añada otros usuarios por
login GitHub:

```bash
vibecoder project vibecoder-tutorial authorize colaborador
vibecoder project vibecoder-tutorial users
vibecoder project vibecoder-tutorial deauthorize colaborador
```

No se puede eliminar el último usuario autorizado. Las decisiones se comparan
por ID numérico, no por el login mutable.

## 10. Primer ciclo: del Issue al Pull Request

### 10.1 Crear el Issue en GitHub

Abra un Issue titulado, por ejemplo, `Añadir suma de enteros`, con este cuerpo:

```text
Implementa una función sum_integers(a: int, b: int) -> int en
src/calculator.py y añade tests automatizados para valores positivos,
negativos y cero. Mantén el cambio pequeño y documentado.
```

El autor debe estar autorizado en el proyecto. Vibecoder sondea periódicamente
los proyectos habilitados. Para adelantar el siguiente ciclo desde la CLI:

```bash
vibecoder workflow poll vibecoder-tutorial
vibecoder workflow status
vibecoder job list
```

Un Issue autorizado nuevo programa automáticamente la generación del plan. Si
quiere solicitar expresamente otra revisión, publique en el Issue:

```text
/vibecoder plan
```

### 10.2 Revisar y aprobar el plan

Vibecoder publica un comentario con la revisión, las tareas, el JSON canónico,
el SHA-256 y el comando exacto. Compruebe que el plan describe únicamente el
cambio pedido. Para aprobarlo, una persona autorizada debe publicar en el mismo
Issue:

```text
/vibecoder approve HASH_DE_64_CARACTERES
```

Copie el hash del comentario actual. Un hash incorrecto, una revisión obsoleta
o un usuario no autorizado no materializa tareas. No se ejecuta nada antes de
esta aprobación.

### 10.3 Observar la ejecución

Después de aprobar, consulte:

```bash
vibecoder workflow poll vibecoder-tutorial
vibecoder workflow status
vibecoder job list
vibecoder worker status codex-1
sudo journalctl -u vibecoder -f
```

El scheduler materializa el DAG, asigna el worker, crea un workspace aislado y
persiste la intención antes de iniciar Codex. En `autonomous`, Codex puede leer,
editar y ejecutar tests dentro del sandbox sin aprobaciones ordinarias. No
espere un texto o SHA fijo: compruebe que el job termina, que el test aparece y
que GitHub recibe una rama y un PR asociados al Issue.

En política `manual`, consulte solicitudes pendientes:

```bash
vibecoder approval list --pending
vibecoder approval show ID
vibecoder approval approve ID
# o: vibecoder approval deny ID
```

La decisión requiere TTY y escribir exactamente `APROBAR` o `DENEGAR`.

### 10.4 Revisar y corregir

Abra el Pull Request creado por vibecoder. Revise base, rama, commits, diff y
tests. Para pedir una corrección, use una review o comentario de revisión del
PR desde una cuenta autorizada, por ejemplo:

```text
Añade un caso que compruebe números negativos y documenta la función.
```

Vibecoder detecta el feedback, continúa el trabajo y publica otro commit en la
misma rama, sin duplicar el PR. Verifique de nuevo el diff y los tests.

Cuando esté satisfecho, haga el merge manualmente en GitHub. Vibecoder solo
observa y verifica ese merge; nunca pulsa el botón ni cierra el Issue por su
cuenta.

## 11. Tareas dependientes

Un plan puede contener, por ejemplo:

```text
Tarea A: crear base_value() y su test
   │
   ▼
Tarea B: crear derived_value() usando base_value()
```

En el JSON del plan, B incluye el identificador de A en `dependencies`. Tras la
aprobación humana, el scheduler no hace READY a B hasta que A termina con
resultado confirmado. El workspace de B parte del SHA resultante de A —o lo
incorpora mediante una operación Git verificable si existen varias
dependencias—, no del SHA original.

Compruebe la secuencia con `vibecoder job list`, el contenido y ancestry de las
ramas publicadas y los PRs resultantes. El contrato no obliga a que todo plan
produzca exactamente un PR. Si A falla, B permanece bloqueada y no se ejecuta
ignorando la dependencia. Una ejecución incierta queda `UNKNOWN`, no se repite
automáticamente.

## 12. Aprobaciones y modelo de seguridad

- **Plan:** siempre requiere aprobación humana del SHA-256 de una revisión
  inmutable.
- **Acciones autonomous:** Codex usa `approvalPolicy=never` dentro de
  `workspace-write`, sin red. No es acceso irrestricto.
- **Acciones manual:** cada solicitud se persiste, expira y admite una única
  decisión auditada. La denegación es el valor seguro.
- **Aislamiento:** cada worker tiene UID, storage, sesiones y `CODEX_HOME`
  propios. No puede leer el PAT, SQLite, el socket administrativo ni otros
  workspaces.
- **Secretos:** SQLite guarda referencias, nunca el PAT. Los backups sí
  contienen secretos sin cifrar y deben protegerse.
- **UNKNOWN:** una desconexión o efecto ambiguo conserva `UNKNOWN` y puede
  activar una barrera. Nunca se reintenta a ciegas.
- **Merge:** siempre humano.

## 13. Diagnóstico

| Síntoma | Causa probable | Diagnóstico | Acción segura |
| --- | --- | --- | --- |
| El daemon no arranca | Unidad, permisos o esquema inválido | `systemctl status vibecoder`; `journalctl -u vibecoder` | Corrija la causa; no borre SQLite |
| La CLI dice acceso denegado | Usuario fuera de `vibecoder-admin` | `id`; `stat /run/vibecoder/control.sock` | Añada el grupo y vuelva a iniciar sesión |
| Worker no READY | Instalación, aislamiento o auth | `vibecoder worker status codex-1`; `vibecoder doctor` | Ejecute login/verify según el diagnóstico |
| Codex requiere autenticación | Device Code ausente o caducado | `vibecoder worker codex-1 status` | `vibecoder worker codex-1 login` |
| GitHub 401 | PAT inválido o revocado | `vibecoder github status` | Genere otro PAT y repita `github login` |
| GitHub 403 | Permiso u organización | CLI y journald | Revise permisos y aprobación organizativa |
| Repositorio inaccesible/404 | No existe o está fuera del PAT | `vibecoder project add`; `github status` | Inclúyalo en el alcance del PAT |
| No aparece plan | Autor no autorizado o polling pendiente | `project PROYECTO users`; `workflow status`; `job list` | Autorice al usuario o fuerce `workflow poll PROYECTO` |
| Plan pendiente | Falta aprobación exacta | Comentario de plan en GitHub | Publique `/vibecoder approve HASH` |
| Tarea no comienza | Dependencia, pausa o barrera | `job list`; `status`; `maintenance status` | Resuelva la causa; no fuerce estados |
| Codex no escribe | Sandbox, UID o permisos del workspace | `worker status`; `journalctl -u vibecoder` | Verifique el worker; no use `chmod 777` |
| Ejecución UNKNOWN | Resultado externo incierto | `maintenance status`; `job list` | Reconcile evidencia; no repita la inferencia |
| No aparece PR | Push/PR pendiente o incierto | `workflow status`; `maintenance status` | Compruebe GitHub y use reconciliación conservadora |
| Barrera activa | Restore o efecto incierto | `vibecoder maintenance status` | Investigue y `maintenance reconcile`; limpie solo una barrera resuelta |

Comandos de recuperación:

```bash
vibecoder maintenance check
vibecoder maintenance reconcile
vibecoder maintenance clear-barrier ID
```

No edite SQLite y no elimine workspaces o barreras para ocultar un estado
incierto.

## 14. Referencia rápida

```bash
vibecoder --version
vibecoder status [--json]
vibecoder doctor [--json]
vibecoder pause
vibecoder resume

vibecoder github login
vibecoder github status [--json]

vibecoder plugin list [--json]
vibecoder plugin show codex
vibecoder worker add codex [--name NOMBRE] [--approval-policy autonomous|manual]
vibecoder worker list [--long] [--json]
vibecoder worker status WORKER [--json]
vibecoder worker WORKER login
vibecoder worker WORKER logout
vibecoder worker WORKER verify
vibecoder worker WORKER enable
vibecoder worker WORKER disable
vibecoder worker set WORKER --approval-policy autonomous|manual

vibecoder project add [RUTA] [--branch RAMA] [--ignore-dirty]
vibecoder project list [--long] [--json]
vibecoder project status PROYECTO [--json]
vibecoder project PROYECTO users
vibecoder project PROYECTO authorize LOGIN
vibecoder project PROYECTO deauthorize LOGIN

vibecoder workflow status [--json]
vibecoder workflow poll PROYECTO [--json]
vibecoder job list [--json]
vibecoder approval list [--pending] [--worker WORKER] [--json]
vibecoder approval show ID
vibecoder approval approve ID
vibecoder approval deny ID

vibecoder maintenance status [--json]
vibecoder maintenance check [--json]
vibecoder maintenance reconcile [--json]
vibecoder maintenance clear-barrier ID
vibecoder backup create
vibecoder backup list
vibecoder backup verify ID
vibecoder backup restore ID
```

Para backups coherentes, active primero `vibecoder pause`; todo restore crea una
barrera de recuperación que `resume` no elimina.
