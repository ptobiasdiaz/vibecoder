# Codex GitHub Orchestrator — Diseño

Repositorio de especificación y futura implementación de un orquestador multi-repositorio y multiagente basado en Codex y GitHub.

## Propósito

Permitir que un equipo dirija proyectos mediante GitHub Issues. Un agente principal persistente analiza, clasifica y negocia requisitos. Tras aprobación humana, agentes especializados persistentes planifican y supervisan sesiones de implementación aisladas. El resultado se entrega mediante Pull Requests con revisión y merge humanos.

## Documentación

- [Arquitectura](docs/architecture/overview.md)
- [Modelo de agentes](docs/architecture/agents.md)
- [Modelo de datos](docs/architecture/data-model.md)
- [Protocolo de comentarios](docs/protocols/github-comments.md)
- [Eventos y estados](docs/workflows/lifecycle.md)
- [Bootstrap](docs/workflows/bootstrap.md)
- [Operación, cuotas y Telegram](docs/architecture/operations.md)
- [Decisiones arquitectónicas](docs/decisions/ADR-0001-foundations.md)
- [Hitos](docs/milestones/roadmap.md)

## Repositorios

1. **Este repositorio**: diseño, documentación y futura implementación del orquestador.
2. **Repositorio de prueba**: inicialmente vacío, se utilizará para validar el bootstrap de extremo a extremo.

## Estado

Especificación inicial de diseño. No existe todavía una implementación funcional. Las interfaces, nombres de métodos y compatibilidad exacta con la versión de Codex deben validarse en el primer hito técnico.
