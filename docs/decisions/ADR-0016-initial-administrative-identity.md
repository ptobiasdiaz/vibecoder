# ADR-0016 — Identidad administrativa inicial y debconf

## Estado

Aceptada para M05.3.

## Decisión

La identidad administrativa de vibecoder es atributiva y auditable, no una credencial
ni un mecanismo de autenticación. Las mutaciones siguen exigiendo UID efectivo 0. No
se crean usuarios Unix, contraseñas, sudoers ni grupos privilegiados.

Debconf puede recoger un identificador lógico no secreto. `postinst` invoca una
operación interna de `AdministrationService` para aplicar migraciones y crear esa
identidad de forma idempotente; nunca ejecuta SQL ad hoc. Un valor vacío deja la
identidad pendiente. Un valor distinto durante `dpkg-reconfigure` no reemplaza la
identidad existente: se informa y la instalación continúa.

`setup init` es un orquestador reanudable sobre operaciones administrativas ya
existentes. Cada paso es atómico en su ámbito y `READY` solo se alcanza mediante una
opción explícita. La ejecución desatendida no solicita datos ni asume confirmaciones.

## Consecuencias

- Debconf no contiene secretos ni es autoridad operacional.
- Las instalaciones M05.2 se migran sin degradar `READY`.
- La atribución fiable llega como `uid:0`; `SUDO_USER` no concede autoridad.
- La administración multiusuario y autenticación de aplicación permanecen fuera de
  alcance.
