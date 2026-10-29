"""Resolución y composición segura de documentación e instrucciones de agentes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Mapping

from .errors import ConflictError, ValidationError


class DocumentCategory(StrEnum):
    INTERNAL = "INTERNAL"
    PUBLIC = "PUBLIC"
    CONTEXT = "CONTEXT"
    AGENT_INSTRUCTIONS = "AGENT_INSTRUCTIONS"


@dataclass(frozen=True)
class ResolvedDocument:
    logical_name: str
    category: DocumentCategory
    path: Path | None
    content: str | None
    sha256: str | None


@dataclass(frozen=True)
class InstructionContext:
    text: str
    versions: Mapping[str, str]


class DocumentResolver:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)

    def resolve(
        self, logical_name: str, category: DocumentCategory | str
    ) -> ResolvedDocument:
        category = DocumentCategory(category)
        relative = self._relative(logical_name)
        candidates = {
            DocumentCategory.INTERNAL: (PurePosixPath(".vibecoder") / relative,),
            DocumentCategory.PUBLIC: (PurePosixPath("doc") / relative,),
            DocumentCategory.CONTEXT: (
                PurePosixPath(".vibecoder") / relative,
                PurePosixPath("doc") / relative,
            ),
            DocumentCategory.AGENT_INSTRUCTIONS: (
                PurePosixPath(".vibecoder") / "agents" / relative / "AGENTS.md",
            ),
        }[category]
        for candidate in candidates:
            path = self._safe(candidate, must_exist=False)
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                return ResolvedDocument(
                    logical_name, category, path, content, self._hash(content)
                )
        return ResolvedDocument(logical_name, category, None, None, None)

    def write(self, destination: str, content: str, *, overwrite: bool = False) -> Path:
        relative = self._relative(destination)
        if relative != PurePosixPath("AGENTS.md") and relative.parts[0] not in {
            ".vibecoder", "doc"
        }:
            raise ValidationError("destino documental fuera de ubicaciones autorizadas")
        path = self._safe(relative, must_exist=False)
        if path.exists() and not overwrite:
            raise ConflictError(f"el documento ya existe: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink_chain(path.parent)
        path.write_text(content, encoding="utf-8")
        return path

    def _safe(self, relative: PurePosixPath, *, must_exist: bool) -> Path:
        path = self.workspace.joinpath(*relative.parts)
        self._assert_no_symlink_chain(path.parent)
        resolved = path.resolve(strict=must_exist)
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValidationError("ruta documental fuera del workspace") from exc
        if path.is_symlink():
            raise ValidationError("los documentos no pueden ser enlaces simbólicos")
        return resolved

    def _assert_no_symlink_chain(self, path: Path) -> None:
        current = self.workspace
        try:
            relative = path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValidationError("ruta documental fuera del workspace") from exc
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValidationError("ruta documental atraviesa un enlace simbólico")

    @staticmethod
    def _relative(value: str) -> PurePosixPath:
        if not value.strip() or "\\" in value:
            raise ValidationError("ruta documental no válida")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValidationError("ruta documental no válida")
        return path

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class InstructionLoader:
    """Construye contexto sin depender del descubrimiento implícito de Codex."""

    _RULE = re.compile(
        r"^\s*[-*]\s*Rule:\s*([A-Za-z0-9_.-]+)\s*=\s*(\S.*?)\s*$", re.IGNORECASE
    )

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.resolver = DocumentResolver(self.workspace)

    def load(self, agent_id: str, task_context: str, *, include_root_text: bool = False) -> InstructionContext:
        agent_path = DocumentResolver._relative(agent_id)
        if len(agent_path.parts) != 1:
            raise ValidationError("identificador de agente no válido")
        documents: list[tuple[str, str]] = []
        versions: dict[str, str] = {}
        root = self.workspace / "AGENTS.md"
        if root.exists():
            if root.is_symlink():
                raise ValidationError("AGENTS.md no puede ser un enlace simbólico")
            root_text = root.read_text(encoding="utf-8")
            versions["AGENTS.md"] = DocumentResolver._hash(root_text)
            if include_root_text:
                documents.append(("Reglas globales", root_text))
        specific = self.resolver.resolve(agent_id, DocumentCategory.AGENT_INSTRUCTIONS)
        if specific.content is not None and specific.path is not None:
            key = str(specific.path.relative_to(self.workspace))
            versions[key] = specific.sha256 or ""
            documents.append((f"Instrucciones específicas de {agent_id}", specific.content))
        self._detect_conflicts(root.read_text(encoding="utf-8") if root.exists() else "", specific.content or "")
        documents.append(("Tarea actual", task_context))
        text = "\n\n".join(f"## {title}\n\n{body.strip()}" for title, body in documents)
        return InstructionContext(text=text.rstrip() + "\n", versions=versions)

    def _detect_conflicts(self, global_text: str, specific_text: str) -> None:
        global_rules = self._rules(global_text)
        specific_rules = self._rules(specific_text)
        conflicts = sorted(
            key for key in global_rules.keys() & specific_rules.keys()
            if global_rules[key] != specific_rules[key]
        )
        if conflicts:
            raise ConflictError(
                "instrucciones específicas contradicen reglas globales: " + ", ".join(conflicts)
            )

    def _rules(self, text: str) -> dict[str, str]:
        rules: dict[str, str] = {}
        for line in text.splitlines():
            match = self._RULE.match(line)
            if match:
                key, value = match.group(1).casefold(), match.group(2).strip().casefold()
                if key in rules and rules[key] != value:
                    raise ConflictError(f"regla explícita contradictoria: {key}")
                rules[key] = value
        return rules
