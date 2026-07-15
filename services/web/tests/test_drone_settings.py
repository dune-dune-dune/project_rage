"""DroneStore: the DB-backed drone-detection feed config + /api/drone-settings."""

from __future__ import annotations

from app.db import SettingsDb
from app.store import DroneStore, _DRONE_URL_DEFAULT


def _store(tmp_path) -> DroneStore:
    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()  # 0004 seeds the defaults
    return DroneStore(db)


def test_defaults_are_disabled(tmp_path):
    data = _store(tmp_path).load()
    assert data["enabled"] is False
    assert data["url"] == "ws://10.20.100.1:8766"


def test_save_enable_and_url(tmp_path):
    store = _store(tmp_path)
    saved = store.save({"enabled": True, "url": "ws://127.0.0.1:8766"})
    assert saved == {"enabled": True, "url": "ws://127.0.0.1:8766"}
    assert store.load() == saved  # persisted


def test_wss_and_path_accepted(tmp_path):
    saved = _store(tmp_path).save({"url": "wss://drones.example.net:9000/feed"})
    assert saved["url"] == "wss://drones.example.net:9000/feed"


def test_invalid_url_keeps_previous(tmp_path):
    store = _store(tmp_path)
    store.save({"enabled": True, "url": "ws://10.0.0.5:8766"})
    # A non-ws scheme / garbage is rejected; the previous URL is kept, but the
    # enable flag still applies.
    saved = store.save({"enabled": False, "url": "http://evil /x"})
    assert saved["url"] == "ws://10.0.0.5:8766"
    assert saved["enabled"] is False


def test_bad_stored_blob_falls_back_to_default(tmp_path):
    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()
    db.put("drone", {"enabled": "yes", "url": 123})  # wrong types
    data = DroneStore(db).load()
    assert data["enabled"] is True  # bool("yes") -> True
    assert data["url"] == _DRONE_URL_DEFAULT


def test_api_drone_settings_roundtrip(client):
    got = client.get("/api/drone-settings").get_json()
    assert got["enabled"] is False
    res = client.post("/api/drone-settings", json={"enabled": True, "url": "ws://127.0.0.1:8766"})
    assert res.status_code == 200
    assert res.get_json() == {"enabled": True, "url": "ws://127.0.0.1:8766"}
    assert client.get("/api/drone-settings").get_json()["enabled"] is True
