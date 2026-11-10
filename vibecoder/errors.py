"""Errores estables de la interfaz programática del núcleo."""


class CoreError(RuntimeError):
    pass


class OperationError(CoreError):
    """Safe error that may cross the local control boundary."""
    code = "OPERATION_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        self.details = details or {}
        super().__init__(message)


class ValidationError(CoreError):
    pass


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    pass


class InvalidTransition(CoreError):
    def __init__(self, entity: str, current: str, requested: str):
        self.entity = entity
        self.current = current
        self.requested = requested
        super().__init__(f"transición inválida de {entity}: {current} -> {requested}")

