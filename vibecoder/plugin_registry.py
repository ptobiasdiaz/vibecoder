"""Declarative trusted-plugin discovery. No provider module is imported here."""
from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .domain import AuthenticationMethod, PluginCapability, PluginDescriptor
from .errors import ConflictError, NotFoundError, OperationError
from .resolution import ResourceResolver
from .sqlite import SQLiteRepository
from .github_identity import utc_now

DESCRIPTOR_VERSION = 1
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


@dataclass(frozen=True)
class RegisteredPlugin:
    descriptor: PluginDescriptor
    runtime: Mapping[str, Any] | None
    trusted_distribution: str
    available: bool
    diagnostic: str | None
    test_only: bool
    requirements: Mapping[str, Any]

    def public(self) -> dict[str, Any]:
        return ({"id": self.descriptor.plugin_id, "version": self.descriptor.version,
                "provider": self.descriptor.provider,
                "platforms": sorted(self.descriptor.supported_platforms),
                "authentication_methods": sorted(x.value for x in self.descriptor.authentication_methods),
                "capabilities": sorted(x.value for x in self.descriptor.capabilities),
                "compatible": "linux" in self.descriptor.supported_platforms and platform.system() == "Linux",
                "available": self.available, "diagnostic": self.diagnostic,
                "test_only": self.test_only}
                | {"requirements": dict(self.requirements)})


class PluginRegistry:
    def __init__(self, repository: SQLiteRepository, descriptor_dir: Path | None = None):
        self.repository = repository
        self.descriptor_dir = descriptor_dir or Path(__file__).with_name("plugin_descriptors")
        self._plugins = self._discover()
        self._persist_descriptors()

    def _discover(self) -> dict[tuple[str, str], RegisteredPlugin]:
        plugins: dict[tuple[str, str], RegisteredPlugin] = {}
        for path in sorted(self.descriptor_dir.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise OperationError(f"descriptor de plugin inseguro: {path.name}")
            try: raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc: raise OperationError(f"descriptor de plugin inválido: {path.name}") from exc
            plugin = self._validate(raw)
            key = (plugin.descriptor.plugin_id, plugin.descriptor.version)
            if key in plugins: raise ConflictError(f"plugin duplicado: {key[0]} {key[1]}")
            plugins[key] = plugin
        return plugins

    def _validate(self, raw: Any) -> RegisteredPlugin:
        if not isinstance(raw, dict) or raw.get("schema_version") != DESCRIPTOR_VERSION:
            raise OperationError("versión de descriptor de plugin incompatible")
        plugin_id, version, provider = raw.get("id"), raw.get("version"), raw.get("provider")
        if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id): raise OperationError("ID de plugin no válido")
        if not isinstance(version, str) or not version.strip() or not isinstance(provider, str) or not provider.strip(): raise OperationError("versión o proveedor no válido")
        try:
            auth = frozenset(AuthenticationMethod(x) for x in raw.get("authentication_methods", []))
            capabilities = frozenset(PluginCapability(x) for x in raw.get("capabilities", []))
        except (ValueError, TypeError) as exc: raise OperationError("capacidad o autenticación desconocida") from exc
        platforms = raw.get("platforms")
        if not isinstance(platforms, list) or not all(isinstance(x, str) for x in platforms): raise OperationError("plataformas no válidas")
        runtime = raw.get("runtime")
        if runtime is not None:
            if not isinstance(runtime, dict) or set(runtime) != {"kind", "module"} or runtime["kind"] != "python-module" or not isinstance(runtime["module"], str):
                raise OperationError("runtime declarativo no válido")
            if not runtime["module"].startswith("vibecoder."):
                raise OperationError("sólo se admiten runtimes incluidos y confiables")
        descriptor = PluginDescriptor(plugin_id, version, provider, frozenset(platforms), auth,
                                      capabilities, {"descriptor_schema": DESCRIPTOR_VERSION})
        return RegisteredPlugin(descriptor, runtime, str(raw.get("trusted_distribution", "")),
                                bool(raw.get("available", runtime is not None)),
                                raw.get("diagnostic") if isinstance(raw.get("diagnostic"), str) else None,
                                bool(raw.get("test_only", False)),
                                dict(raw.get("requirements", {})) if isinstance(raw.get("requirements", {}), dict) else {})

    def _persist_descriptors(self) -> None:
        now = utc_now()
        for item in self._plugins.values():
            public = item.public()
            exists = self.repository.query_one("SELECT descriptor_json FROM plugins WHERE id=? AND version=?", (public["id"], public["version"]))
            encoded = json.dumps(public, sort_keys=True)
            if exists and exists["descriptor_json"] != encoded:
                raise ConflictError(f"descriptor instalado cambió sin versión nueva: {public['id']}")
            if not exists:
                self.repository.execute("INSERT INTO plugins VALUES(?,?,?,?,?)", (public["id"], public["version"], public["provider"], encoded, now))

    def list(self) -> list[dict[str, Any]]:
        return [x.public() for x in sorted(self._plugins.values(), key=lambda p: (p.descriptor.plugin_id, p.descriptor.version))]

    def resolve(self, query: str) -> RegisteredPlugin:
        items = list(self._plugins.values())
        return ResourceResolver(id_of=lambda p: f"{p.descriptor.plugin_id}@{p.descriptor.version}",
            name_of=lambda p: p.descriptor.plugin_id,
            natural_id_of=lambda p: f"{p.descriptor.plugin_id}@{p.descriptor.version}").resolve(query, items)
