"""Open access: the PIN login gate was removed — every route is reachable."""

from __future__ import annotations


def test_healthz_open(client):
    assert client.get("/healthz").status_code == 200


def test_index_open_without_login(client):
    assert client.get("/").status_code == 200


def test_api_open_without_login(client):
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/input", json={"up": True}).status_code == 204


def test_login_routes_gone(client):
    # The former /login and /logout endpoints no longer exist.
    assert client.get("/login").status_code == 404
    assert client.post("/login", data={"pin": "1234567"}).status_code == 404
    assert client.get("/logout").status_code == 404
