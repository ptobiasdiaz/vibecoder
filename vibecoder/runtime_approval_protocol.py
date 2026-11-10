"""Narrow AF_UNIX protocol used by isolated workers for approvals only."""
from __future__ import annotations

import os
import pwd
import socket
import socketserver
import uuid
from pathlib import Path
from typing import Any, Mapping

from .control_protocol import MAX_MESSAGE_SIZE, PROTOCOL_VERSION, receive_frame, send_frame
from .errors import OperationError

DEFAULT_RUNTIME_APPROVAL_SOCKET = Path("/run/vibecoder/runtime-approval.sock")


class RuntimeApprovalClient:
    def __init__(self, socket_path: Path | None = None, timeout: float = 10):
        self.socket_path = socket_path or Path(os.environ.get("VIBECODER_APPROVAL_SOCKET", DEFAULT_RUNTIME_APPROVAL_SOCKET))
        self.timeout = timeout

    def request(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4()); connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path))
            send_frame(connection, {"version": PROTOCOL_VERSION, "request_id": request_id,
                "operation": operation, "arguments": dict(arguments)})
            response = receive_frame(connection, max_size=MAX_MESSAGE_SIZE)
        except (OSError, EOFError) as exc:
            raise OperationError("broker de aprobaciones no disponible") from exc
        finally: connection.close()
        if not isinstance(response, dict) or response.get("request_id") != request_id or not response.get("ok"):
            error = response.get("error", {}) if isinstance(response, dict) else {}
            raise OperationError(str(error.get("message", "respuesta inválida del broker")))
        result = response.get("result")
        if not isinstance(result, dict): raise OperationError("resultado inválido del broker")
        return result


class RuntimeApprovalRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        from .approvals import RuntimeApprovalService
        from .control_protocol import ControlRequest, failure, success
        from .sqlite import SQLiteRepository

        connection: socket.socket = self.request; request_id = None
        try:
            peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = __import__("struct").unpack("3i", peer)
            username = pwd.getpwuid(uid).pw_name
            payload = receive_frame(connection, max_size=MAX_MESSAGE_SIZE)
            request = ControlRequest.from_payload(payload); request_id = request.request_id
            repository = SQLiteRepository(self.server.database_path)
            try:
                identity = repository.query_one("SELECT worker_id FROM worker_runtime_identities WHERE unix_user=?", (username,))
                if not identity: raise PermissionError("identidad de runtime no autorizada")
                worker_id = str(identity["worker_id"]); service = RuntimeApprovalService(repository)
                if request.operation == "approval.register":
                    result = service.register(worker_id, request.arguments)
                elif request.operation == "approval.claim":
                    if set(request.arguments) != {"approval_id"}: raise ValueError("argumentos no válidos")
                    result = service.claim(worker_id, str(request.arguments["approval_id"]))
                elif request.operation == "approval.confirm":
                    if set(request.arguments) != {"approval_id", "delivered", "diagnostic"}: raise ValueError("argumentos no válidos")
                    result = service.confirm(worker_id, str(request.arguments["approval_id"]),
                        bool(request.arguments["delivered"]), request.arguments["diagnostic"])
                else: raise ValueError("operación runtime no permitida")
            finally: repository.close()
            response = success(request.request_id, result)
        except Exception as exc:
            response = failure(request_id, type(exc).__name__, "solicitud de aprobación rechazada")
        try: send_frame(connection, response)
        except OSError: pass


class RuntimeApprovalServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, socket_path: Path, database_path: Path):
        self.database_path = database_path
        super().__init__(str(socket_path), RuntimeApprovalRequestHandler)

