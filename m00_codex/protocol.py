"""Enumeraciones de la API JSON-RPC de Codex App Server.

Sus nombres son deliberadamente distintos de tipos internos presentes en las
respuestas. Por ejemplo, ``thread/start.sandbox`` acepta ``workspace-write`` y
no ``workspaceWrite``.
"""

from enum import Enum


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class ApprovalPolicy(str, Enum):
    UNTRUSTED = "untrusted"
    ON_REQUEST = "on-request"
    NEVER = "never"


class SortDirection(str, Enum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class TurnItemsView(str, Enum):
    NOT_LOADED = "notLoaded"
    SUMMARY = "summary"
    FULL = "full"
