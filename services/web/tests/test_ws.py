"""The /api/ws control channel is open (the cockpit has no login gate).

Full-duplex WS streaming is verified manually; here we assert the handshake is
reachable — a plain test client is not a real WebSocket, so flask-sock rejects the
upgrade with a non-401 status rather than an auth failure.
"""

from __future__ import annotations


def test_ws_handshake_reachable(client):
    # No auth gate: the handshake is not blocked. The plain test client is not a
    # real WebSocket, so flask-sock rejects the upgrade with a non-401 status.
    assert client.get("/api/ws").status_code != 401
