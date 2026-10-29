# ADR-0006 — Git local y workspaces aislados para generación inicial

Estado: Aceptado para M03.3.

## Contexto

La generación inicial debe modificar un repositorio normal o completamente vacío sin
afectar al repositorio de vibecoder ni producir efectos remotos. El resultado debe
resistir reinicios y conservarse ante validaciones fallidas.

## Decisión

1. El adaptador usa el ejecutable `git` mediante `subprocess` con lista de argumentos,
   `shell=False` y prompts de credenciales desactivados. No se añade una biblioteca
   Python porque el flujo necesita comportamiento Git nativo y el binario ya es un
   requisito operativo.
2. Cada proyecto ocupa `<workspace-root>/<project-uuid>`. Nombres GitHub, Issues y
   comentarios nunca forman rutas locales ni nombres de rama.
3. La raíz se configura explícitamente y no puede estar dentro del checkout del
   orquestador. Se rechazan traversal, enlaces de workspace e inconsistencias de
   remoto o rama.
4. Con historia se crea `vibecoder/bootstrap-<id>` desde la rama remota detectada. Sin
   commits se crea una rama huérfana y el bootstrap produce el primer commit local.
5. No hay push, creación de ramas remotas, PR o merge en M03.3.
6. El workspace se conserva ante fallos. La limpieza explícita lo mueve a un archivo
   local recuperable; no ejecuta borrado recursivo.
7. El commit solo se crea después de validar cambios, rutas, enlaces, tamaño,
   credenciales conocidas y estructura documental. Su hash se persiste y se verifica
   para evitar duplicados.
8. Una ejecución incierta se bloquea y no reenvía el prompt automáticamente.

## Repositorios vacíos y M03.4

GitHub no puede abrir un PR convencional si no existe una rama base. M03.4 deberá
elegir mediante una acción humana explícita entre:

- publicar primero una rama base mínima y después la rama de bootstrap, que conserva
  el flujo de revisión habitual pero introduce un primer efecto remoto; o
- publicar el primer commit como rama inicial y revisar por un mecanismo distinto al
  PR convencional, más simple pero con menos garantías de revisión GitHub.

M03.3 no selecciona ni ejecuta ninguna estrategia remota.

