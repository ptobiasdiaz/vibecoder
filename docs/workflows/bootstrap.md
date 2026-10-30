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
6. Principal presenta una configuración Git estructurada; un desarrollador autorizado
   puede modificarla y la confirma con un único bloque `vibecoder-git-config`.
7. Desarrollador autorizado añade etiqueta de aprobación. Durante polling se verifica
   que el evento más reciente de esa etiqueta sea atribuible a ese actor; la etiqueta
   por sí sola no autoriza y la configuración confirmada es obligatoria.
8. Se crea rama/PR con README, AGENTS.md, registro mínimo del agente principal y documentos base.
9. Un humano revisa y fusiona; proyecto pasa a `READY`.
10. Los especialistas aparecen más adelante mediante cambios de configuración aprobados por PR.

El agente principal inicial y definitivo comparten identidad lógica. No crear especialistas ficticios. Guardar IDs de Issue, PR, sesión y estado para sobrevivir a reinicios.

En M03.2 el flujo termina al congelar la especificación conversacional y mover el
proyecto a `DESIGNING`. M03.3 prepara un workspace aislado, genera `AGENTS.md`, la
estructura mínima `.vibecoder/` y documentación pública bajo `doc/`, valida y crea un
commit local. M03.4 publica de forma recuperable, abre un PR para revisión humana y
solo mueve el proyecto a `ACTIVE` después de verificar el merge mediante GitHub.

## Criterio de aceptación

Un repositorio totalmente vacío puede llegar a `READY` mediante GitHub, sin preparar archivos manualmente en él. Ninguna acción de aprobación ni merge se ejecuta automáticamente.
