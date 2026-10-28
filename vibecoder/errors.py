"""Errores estables de la interfaz programática del núcleo."""


class CoreError(RuntimeError):
    pass


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

