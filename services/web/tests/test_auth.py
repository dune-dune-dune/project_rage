"""PIN login gate: everything but /healthz (and /login, static) needs a session."""

from __future__ import annotations


def test_healthz_open_without_login(client):
    assert client.get("/healthz").status_code == 200


def test_index_redirects_to_login_when_unauthed(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_api_returns_401_when_unauthed(client):
    assert client.post("/api/input", json={}).status_code == 401
    assert client.get("/api/status").status_code == 401


def test_login_wrong_pin_rejected(client):
    resp = client.post("/login", data={"pin": "0000000"})
    assert resp.status_code == 401


def test_login_correct_pin_grants_access(client):
    resp = client.post("/login", data={"pin": "1234567"})
    assert resp.status_code == 302
    # The test client keeps the session cookie, so follow-up requests are authed.
    assert client.get("/").status_code == 200
    assert client.post("/api/input", json={"up": True}).status_code == 204


def test_logout_clears_session(authed_client):
    assert authed_client.get("/api/status").status_code == 200
    authed_client.get("/logout")
    assert authed_client.get("/api/status").status_code == 401


def test_no_pin_means_open_access(app_factory):
    app = app_factory(pin="")
    c = app.test_client()
    assert c.get("/").status_code == 200
    assert c.post("/api/input", json={}).status_code == 204
