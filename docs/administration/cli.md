# Administración por CLI

La CLI fue validada bajo systemd real en Debian 13 trixie y Ubuntu 24.04. Las
mutaciones siguen reservadas a UID 0; el usuario de servicio no es administrador.

`vibecoder-admin` administra una instalación sin editar JSON ni SQLite. Las consultas
son de solo lectura; las mutaciones requieren root. `--json` produce JSON sin secretos.
Las operaciones destructivas solicitan confirmación; `--yes` permite automatizarlas
cuando el operador ya ha verificado el destino.

## Primera configuración

El asistente reanudable es:

```bash
sudo vibecoder-admin setup init
sudo vibecoder-admin setup init --non-interactive --admin-id automation
```

La identidad registrada sirve para atribución/auditoría; no sustituye la autorización
UID 0. `--non-interactive` solo aplica parámetros explícitos y nunca activa sin
`--activate`.

```bash
sudo vibecoder-admin accounts add --name primary --auth-type CHATGPT \
  --codex-home /var/lib/vibecoder/accounts/primary \
  --executable /usr/local/bin/codex --cwd /var/lib/vibecoder/workspaces

sudo vibecoder-admin credentials add github --id github-main
sudo vibecoder-admin config set github_credential_id github-main
sudo vibecoder-admin projects add --name demo --account UUID --repository owner/repo
sudo vibecoder-admin projects configure UUID --repository owner/repo \
  --workspace /var/lib/vibecoder/workspaces/demo --authorized-user developer \
  --enable-inference --enable-publication
sudo vibecoder-admin projects enable UUID
sudo vibecoder-admin setup validate
sudo vibecoder-admin setup configure
sudo vibecoder-admin setup activate
```

Para automatización, `credentials add|replace --fd N` lee un descriptor ya abierto.
La activación no ejecuta inferencia ni escribe en GitHub: solo permite al supervisor
evaluar workflows que ya tengan sus propias autorizaciones.

## Grupos de comandos

- `status`, `doctor`.
- `config show|validate|set|reset` (solo claves declaradas).
- `projects list|show|add|configure|enable|disable|status`.
- `accounts list|show|add|configure|check|disable`.
- `credentials list|add|replace|check|revoke`.
- `agents list|show|status|disable`.
- `service status|readiness|pause|resume`.
- `setup status|validate|configure|activate|deactivate`.
- `backup create|list|inspect|verify|restore`.
- `maintenance status|check|reconcile|prepare-upgrade|finish-upgrade`.

Backup y restore exigen `--offline`; restore exige además confirmación o `--yes`. Una
restauración nunca conserva `READY`: instala una barrera `CONFIGURED` y pausa global.

`doctor` comprueba configuración, SQLite, rutas, Git, cuentas y jobs sin inferencia,
red ni cambios de estado. Los códigos son: 0 éxito, 2 uso, 3 autorización, 4
configuración y 5 fallo operacional.

Los cambios críticos en un runtime `READY` lo degradan a `CONFIGURED`. Las pausas usan
la misma tabla persistente que el supervisor.
