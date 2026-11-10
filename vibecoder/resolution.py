"""Scope-aware resource resolution, independent from CLI and persistence."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, TypeVar
from .errors import ConflictError, NotFoundError

T = TypeVar("T")


def normalize_name(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True)
class AmbiguousCandidate:
    id: str
    name: str
    natural_id: str | None


class AmbiguousResourceError(ConflictError):
    def __init__(self, query: str, candidates: list[AmbiguousCandidate]):
        self.query = query
        self.candidates = tuple(candidates)
        super().__init__(f"recurso ambiguo {query!r}: " + ", ".join(f"{c.name} ({c.id})" for c in candidates))


class ResourceResolver(Generic[T]):
    def __init__(self, *, id_of: Callable[[T], str], name_of: Callable[[T], str],
                 natural_id_of: Callable[[T], str | None] = lambda _: None):
        self.id_of, self.name_of, self.natural_id_of = id_of, name_of, natural_id_of

    def resolve(self, query: str, resources: Iterable[T]) -> T:
        query, items = query.strip(), list(resources)
        exact = [item for item in items if self.id_of(item) == query]
        if len(exact) == 1:
            return exact[0]
        natural = [item for item in items if self.natural_id_of(item) == query]
        if len(natural) == 1:
            return natural[0]
        if len(natural) > 1:
            raise self._ambiguous(query, natural)
        names = [item for item in items if normalize_name(self.name_of(item)) == normalize_name(query)]
        if len(names) == 1:
            return names[0]
        if len(names) > 1:
            raise self._ambiguous(query, names)
        raise NotFoundError(f"recurso no encontrado: {query}")

    def _ambiguous(self, query: str, items: list[T]) -> AmbiguousResourceError:
        return AmbiguousResourceError(query, [AmbiguousCandidate(
            self.id_of(item), self.name_of(item), self.natural_id_of(item)) for item in items])
