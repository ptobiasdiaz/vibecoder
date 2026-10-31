# ADR-0013 — Supervisor local con trabajos y reservas persistentes

## Estado

Aceptada para M04.5.

## Decisión

El supervisor es un único proceso local. Deriva trabajos idempotentes del estado de
los workflows existentes y los guarda como **intenciones**, nunca como nueva fuente
de autorización. Antes de despachar vuelve a consultar el workflow original.

Cada trabajo obtiene un lease SQLite con proceso, token, heartbeat y expiración. Un
lease expirado pasa a `UNKNOWN`: primero debe reconciliarse y no autoriza a repetir
inferencia ni escrituras remotas. La unicidad parcial impide dos intenciones activas
para la misma operación y entidad.

Los límites se aplican por categoría, proyecto, agente, cuenta, workspace y PR. M04.5
no promete multiinstancia ni locks distribuidos. Los controles de pausa son gates
operativos persistentes; no crean ni amplían permisos.

## Consecuencias

- Reinicios y timeouts conservan trazabilidad y evitan reejecución ciega.
- Los servicios M03/M04 siguen validando aprobación, identidad y estado remoto.
- La concurrencia configurable existe dentro de un proceso, con valores conservadores;
  el dispatcher actual procesa secuencialmente.
- `dry-run` solo lee SQLite y no crea trabajos, directorios ni operaciones remotas.
