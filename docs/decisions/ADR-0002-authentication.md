# ADR-0002 — Autenticación por cuenta Codex

Estado: Aceptado tras M00.

## Contexto

M00 confirmó con Codex App Server 0.163.0 que un thread vacío no es recuperable tras
reiniciar el proceso y que un thread con un turno real completado sí puede reanudarse.
La autenticación, las sesiones operativas de los agentes y su identidad lógica tienen
ciclos de vida diferentes y no deben modelarse como una sola entidad.

La documentación oficial describe login persistente con ChatGPT, API key y, para
cuentas compatibles, access tokens. Su disponibilidad, facturación, límites y
renovación no son equivalentes y pueden cambiar entre versiones.

## Decisión

1. La autenticación pertenece a una cuenta configurada en vibecoder, no a cada agente.
2. Varios agentes pueden compartir una cuenta y mantienen sesiones independientes.
3. Cada cuenta tiene un entorno aislado de credenciales y sesiones mediante
   `CODEX_HOME` o un mecanismo oficial equivalente.
4. El gestor de autenticación estará desacoplado del gestor de sesiones y del ejecutor.
5. Se contemplan login persistente con ChatGPT, API key y access tokens de Codex solo
   cuando el tipo de cuenta y la versión utilizada los soporten.
6. Los agentes no ejecutan `codex login` en cada arranque; reutilizan el estado
   autenticado de su cuenta.
7. Los errores de autenticación son estados recuperables distintos del agotamiento de
   cuota y de los fallos de ejecución.
8. Las sesiones no se transfieren automáticamente entre cuentas.
9. Las credenciales no se almacenan en GitHub, comentarios ni en texto plano en la
   base de datos de aplicación. Se usa almacenamiento de secretos o credenciales
   apropiado para el entorno.
10. Cada mecanismo se valida contra la versión de Codex desplegada antes de declararlo
    compatible. La presencia de documentación o de un método no demuestra acceso para
    una cuenta concreta.

## Consecuencias

- `Account` será propietaria de la referencia al entorno de autenticación; `Agent` y
  `Session` solo referenciarán la cuenta asignada.
- Rotar o reparar credenciales no cambia la identidad lógica del agente.
- Cambiar una sesión de cuenta requiere una operación explícita de reconstrucción; no
  se copiarán threads o credenciales automáticamente.
- M01 puede definir interfaces y persistencia de referencias, pero no implementará un
  gestor de autenticación completo antes de validar requisitos operativos.

## Validaciones pendientes no bloqueantes

- concurrencia live de varias sesiones sobre una cuenta;
- cancelación live y confirmación del estado final;
- límites de uso bajo ChatGPT, API key y otros métodos compatibles;
- recuperación ante credenciales caducadas o revocadas;
- compatibilidad de access tokens según tipo de cuenta y versión.

Estas validaciones se convierten en tareas de hitos posteriores, especialmente M03,
M06 y M07.

## Referencias

- [M00](../milestones/M00.md)
- [Autenticación de Codex](https://learn.chatgpt.com/docs/auth)
- [Access tokens](https://learn.chatgpt.com/docs/enterprise/access-tokens)
