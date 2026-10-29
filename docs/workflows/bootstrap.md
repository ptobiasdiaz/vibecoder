# Bootstrap desde repositorio vacío

## Entradas admitidas

- Registrar repositorio sin descripción: crear Issue de inicialización.
- Registrar repositorio con descripción inicial: usarla como contexto del mismo Issue de inicialización.

## Secuencia

1. Administrador instala/autoriza GitHub App para repositorio.
2. Orquestador comprueba configuración versionada; si no existe, entra en `UNINITIALIZED`.
3. Crea un único Issue de bootstrap de forma idempotente.
4. Crea agente principal lógico y sesión Codex con instrucciones genéricas del sistema.
5. Principal conversa con desarrollador, documenta objetivos y arquitectura preliminar.
6. Desarrollador autorizado añade etiqueta de aprobación. Durante polling se verifica
   que el evento más reciente de esa etiqueta sea atribuible a ese actor; la etiqueta
   por sí sola no autoriza.
7. Se crea rama/PR con README, AGENTS.md, registro mínimo del agente principal y documentos base.
8. Un humano revisa y fusiona; proyecto pasa a `READY`.
9. Los especialistas aparecen más adelante mediante cambios de configuración aprobados por PR.

El agente principal inicial y definitivo comparten identidad lógica. No crear especialistas ficticios. Guardar IDs de Issue, PR, sesión y estado para sobrevivir a reinicios.

En M03.2 el flujo termina al congelar la especificación conversacional y mover el
proyecto a `DESIGNING`. Los pasos de archivos, rama y PR permanecen diferidos.

## Criterio de aceptación

Un repositorio totalmente vacío puede llegar a `READY` mediante GitHub, sin preparar archivos manualmente en él. Ninguna acción de aprobación ni merge se ejecuta automáticamente.
