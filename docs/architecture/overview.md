# Arquitectura general

## Componentes

- **GitHub App**: webhooks, comentarios, etiquetas, ramas, PR y permisos de GitHub.
- **Orquestador**: máquina de estados, enrutamiento, prompts, registro de agentes, coordinación, autorización, idempotencia y planificación.
- **Codex runtime**: adaptador App Server para crear, reanudar, observar, reconciliar y
  cancelar turnos, aislado por cuenta.
- **Worker local**: selecciona una tarea persistente, coordina una ejecución secuencial
  y cierra recursos; no implementa transporte ni autenticación.
- **Workers**: ejecución bajo demanda y aislada, con worktrees por Issue y entornos de compilación controlados.
- **Persistencia**: proyectos, cuentas, sesiones, turnos, preguntas, trabajos, eventos y estado de recuperación.
- **Telegram**: alertas y consultas de estado; no sustituye GitHub para aprobaciones.
- **Métricas**: endpoint compatible con Prometheus. La supervisión externa de VM/host queda fuera del alcance.

## Principios

1. Multi-repositorio desde el diseño; despliegue inicial en una VM Linux con Docker Compose.
2. Agente principal persistente por proyecto, con autoridad sobre clasificación, arquitectura y selección de especialistas.
3. Especialistas persistentes y con responsabilidades documentadas, modificables mediante PR aprobado.
4. Sesiones de implementación por Issue/PR, aisladas y reanudables durante revisiones.
5. GitHub Issues como registro visible de conversación; comentarios de PR como canal técnico de revisión.
6. La documentación versionada es memoria duradera, no la única fuente de verdad operativa.
7. El orquestador ejecuta reglas de control; no interpreta decisiones técnicas por cuenta propia.
8. Un turno por sesión a la vez. Paralelismo entre sesiones sujeto a recursos, dependencias y límites de cuenta.
9. Solo humanos autorizados aprueban implementación y merge.

## Límites de confianza

Los textos de Issues, PR y comentarios son entradas no confiables. No pueden alterar permisos ni instrucciones privilegiadas. Verificar firmas de webhook, identidad y permisos del autor, scopes mínimos de GitHub App y aislamiento de credenciales. No dar acceso al socket Docker ni a secretos de otras cuentas a los workers.

## Flujo resumido

Issue abierto → análisis del principal → discusión → especificación final → etiqueta de aprobación humana → selección de especialistas → planes y consultas → implementación aislada → revisión técnica → PR → revisión y merge humano → documentación y cierre.
