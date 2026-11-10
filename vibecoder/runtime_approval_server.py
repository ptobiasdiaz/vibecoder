"""Daemon-owned approval callback server authenticated with SO_PEERCRED."""
from __future__ import annotations

import os
import pwd
import socket
import socketserver
import struct
from pathlib import Path

from .approvals import RuntimeApprovalService
from .control_protocol import ControlRequest, failure, receive_frame, send_frame, success
from .sqlite import SQLiteRepository


class RuntimeApprovalHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        connection: socket.socket = self.request; request_id = None
        try:
            raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw)
            username = pwd.getpwuid(uid).pw_name
            request = ControlRequest.from_payload(receive_frame(connection)); request_id = request.request_id
            repository = SQLiteRepository(self.server.database_path)
            try:
                identity = repository.query_one("SELECT worker_id FROM worker_runtime_identities WHERE unix_user=?", (username,))
                if not identity: raise PermissionError("identidad de runtime no autorizada")
                worker_id = str(identity["worker_id"])
                if request.operation == "approval.register":
                    result = RuntimeApprovalService(repository).register(worker_id, request.arguments)
                elif request.operation == "approval.claim":
                    if set(request.arguments) != {"approval_id"}: raise ValueError("argumentos no válidos")
                    result = RuntimeApprovalService(repository).claim(worker_id, str(request.arguments["approval_id"]))
                elif request.operation == "approval.confirm":
                    if set(request.arguments) != {"approval_id", "delivered", "diagnostic"}: raise ValueError("argumentos no válidos")
                    result = RuntimeApprovalService(repository).confirm(worker_id, str(request.arguments["approval_id"]),
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
        super().__init__(str(socket_path), RuntimeApprovalHandler)
