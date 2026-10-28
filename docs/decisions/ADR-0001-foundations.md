# ADR-0001 — Principios iniciales del sistema

Estado: Aceptado como línea base de diseño (validación técnica M00 completada).

## Decisiones

1. Una instalación gestiona varios repositorios y varias cuentas Codex.
2. El agente principal persistente dirige el proyecto y selecciona especialistas.
3. Especialistas persistentes, roles documentados y dinámicos; implementadores temporales por Issue/PR.
4. La documentación versionada preserva conocimiento y decisiones.
5. Issues y comentarios son la interfaz principal; PR para revisión técnica.
6. Comunicación humana libre con directivas mínimas `/agent` y `/reply`.
7. Las preguntas suspenden tareas sin mantener procesos bloqueados.
8. Solo humanos autorizan implementación y merge.
9. Bootstrap desde repositorio vacío mediante Issue, con descripción inicial opcional.
10. VM + Docker Compose inicial; workers aislados y arquitectura extensible.
11. Telegram y observabilidad interna incluidos; monitorización externa excluida.
12. Cuotas y consumo gestionados por cuenta; asignación de proyectos a cuentas.

## Aspectos aún abiertos

- Validaciones live no bloqueantes de concurrencia y cancelación.
- Compatibilidad y recuperación de cada mecanismo de autenticación; véase ADR-0002.
- Política de límites y asignación de recursos entre proyectos.
- Formato exacto de salida estructurada del agente frente a Markdown visible.
- Política de revisión de especialistas y reconciliación de cambios paralelos.
