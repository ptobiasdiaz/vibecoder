"""Errores estables de la interfaz programática del núcleo."""


class CoreError(RuntimeError):
    pass


class OperationError(CoreError):
    """Safe error that may cross the local control boundary."""
    code = "OPERATION_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        self.details = details or {}
        super().__init__(message)


class RetryDeferred(OperationError):
    """A confirmed, effect-free transient failure eligible for a later attempt."""
    code = "RETRY_DEFERRED"

    def __init__(self, message: str, *, next_retry_at: str, details: dict[str, object] | None = None):
        merged = dict(details or {})
        merged["next_retry_at"] = next_retry_at
        self.next_retry_at = next_retry_at
        super().__init__(message, details=merged)


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

