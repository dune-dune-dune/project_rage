"""The /api/ws control channel is gated by the same PIN login as the API.

Full-duplex WS streaming is verified manually; here we assert the handshake is
subject to the auth gate (the before_request hook runs before the upgrade), so an
unauthenticated client cannot open the control socket.
"""

from __future__ import annotations


def test_ws_handshake_requires_auth(client):
    # Unauthenticated: the /api gate aborts the handshake with 401 before upgrade.
    assert client.get("/api/ws").status_code == 401


def test_ws_handshake_passes_gate_when_authed(authed_client):
    # Authed: the gate lets it through; the plain test client is not a real
    # WebSocket, so flask-sock rejects the upgrade with a non-401 status.
    assert authed_client.get("/api/ws").status_code != 401


def test_ws_open_when_no_pin(app_factory):
    app = app_factory(pin="")
    c = app.test_client()
    assert c.get("/api/ws").status_code != 401
