# Políticas de aprobación de workers

Cada worker persiste una política de aprobación de acciones. Esta dimensión es
independiente de la aprobación humana del plan y del merge humano.

`autonomous` es el valor predeterminado de las nuevas altas Codex. Cada plugin
declara de forma neutral las políticas que admite y su valor predeterminado;
un plugin que no declare autonomía permanece en `manual`. En Codex 0.167.0
se traduce a `approvalPolicy=never`, `sandbox=workspace-write` al crear o
reanudar el thread y a `sandboxPolicy={type: workspaceWrite,
networkAccess: false}` en cada turno. Permite leer y escribir en el workspace y
ejecutar herramientas allí. No elimina el sandbox, no concede red, UID root,
capacidades Linux, PAT GitHub, SQLite ni sockets administrativos.

`manual` conserva el broker durable por acción: registro, decisión humana de
un solo uso, entrega y confirmación. Expiración, reinicios y entregas ambiguas
mantienen la semántica conservadora existente.

Una solicitud residual recibida en modo autónomo nunca se acepta. El runtime
la deniega, conserva metadatos redactados en el resultado de ejecución y marca
la ejecución como fallida con `AUTONOMOUS_APPROVAL_REQUEST`. No existe política
`unrestricted`.

Las bases 0.0.1 previas se amplían de forma idempotente con
`worker_approval_policies`. Todos sus workers reciben `manual`; las altas
nuevas de Codex reciben `autonomous`. Un cambio se rechaza durante ejecuciones
activas, se aplica a los turnos posteriores y queda auditado. Una ejecución
`UNKNOWN` no se reanuda ni se repite por cambiar la política.

Comandos:

```bash
vibecoder worker add codex
vibecoder worker add codex --approval-policy manual
vibecoder worker set codex-1 --approval-policy autonomous
vibecoder worker status codex-1
```

En Debian Sid con systemd 262, las unidades transitorias omiten
`RestrictSUIDSGID`: esa opción hace fallar bubblewrap con `EOPNOTSUPP` antes de
crear el sandbox. `NoNewPrivileges`, capabilities vacías y el UID dedicado
conservan la protección frente a elevación.
