"""Phase 1 egress instrumentation (plan v2.4 section 12: egress-off row, stage 1).

With ``EGRESS_MODE=off`` the app must perform zero non-loopback socket
operations. The ASGI test transport is in-process, so any ``connect()`` or
``getaddrinfo()`` aimed at a non-loopback host would indicate the application
itself reaching out - this test fails loudly if it ever happens. The full
socket-instrumented suite across the whole test run arrives at Phase 6 (M1 gate
8); this is the Phase 1 config-rejection stage of that check.
"""

from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from rag_platform.config import Settings
from rag_platform.main import create_app


def _is_loopback_host(host: str) -> bool:
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith("127.")
        or host.startswith("::1")
    )


def test_health_performs_no_outbound_sockets_with_egress_off(monkeypatch) -> None:
    connect_attempts: list[object] = []
    dns_attempts: list[object] = []

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address: object) -> object:
        host = address[0] if isinstance(address, tuple) and address else str(address)
        if not _is_loopback_host(str(host)):
            connect_attempts.append(address)
        return original_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> object:
        if not _is_loopback_host(str(host)):
            dns_attempts.append(host)
        return original_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    # App construction happens under the guard too: config validation itself
    # must open no sockets.
    app = create_app(Settings(_env_file=None, egress_mode="off"))
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert connect_attempts == []
    assert dns_attempts == []
