# ADR-0008 — Publicación remota recuperable y merge exclusivamente humano

Estado: Aceptado para M03.4.

## Contexto

Push y creación de Pull Request son efectos remotos que pueden completarse aunque el
cliente pierda la respuesta. Repetirlos ciegamente produciría ramas o PR duplicados.
Además, GitHub no permite crear referencias mediante su API Git en un repositorio sin
ramas, incluso si el objeto commit existe ([documentación oficial](https://docs.github.com/en/rest/git/refs#create-a-reference)).

## Decisión

1. `publication_workflows` separa publicación de generación y conserva toda la cadena
   bootstrap/configuración/generación/PR/activación.
2. Cada push persiste primero una intención. Un error deja `UNKNOWN`; antes de repetir
   se consulta la referencia remota y su SHA.
3. En un remoto vacío, M03.3 prepara localmente un commit raíz técnico vacío,
   reproducible y sin código. El commit generado es su descendiente directo.
4. M03.4 publica primero ese commit en la rama principal aprobada, verifica que GitHub
   la reconoce como predeterminada y después publica la rama bootstrap.
5. No se usan force push, shell, prompts interactivos ni hooks. Git HTTPS usa un
   `GIT_ASKPASS` temporal; el token solo vive en el entorno del proceso Git.
6. El PR lleva un marcador de idempotencia y se busca por head/base antes de crearlo.
7. `closed` no equivale a `merged`: se exige `merged=true`, SHA final y refs esperadas.
8. Vibecoder nunca aprueba ni fusiona el PR.

## Consecuencias

- Una publicación incierta se detiene y reconcilia con GitHub.
- Generaciones históricas cuyo commit raíz contiene código no se reescriben.
- El token no llega a Codex ni se guarda en SQLite, Git o URLs.
- La publicación completa sobre un repositorio GitHub vacío fue validada live el 30
  de octubre de 2026, incluido PR y activación tras merge humano.
