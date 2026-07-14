"""Video/network profiles: the store, the camera URLs and /api/network-settings."""

from __future__ import annotations

from app.db import SettingsDb
from app.store import NetworkStore


def _store(tmp_path) -> NetworkStore:
    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()  # 0002 seeds the defaults
    return NetworkStore(db)


def test_defaults_are_the_local_profile(tmp_path):
    data = _store(tmp_path).load()
    assert data["video_mode"] == "local"
    assert data["local"]["host"] == "192.168.88.33"
    assert data["remote"]["host"] == "10.20.100.1"


def test_cameras_follow_the_active_mode(tmp_path):
    store = _store(tmp_path)
    assert store.cameras() == [
        {"label": "CAM 95", "url": "http://192.168.88.33:8889/cam95_h264/whep"},
        {"label": "CAM 96", "url": "http://192.168.88.33:8889/cam96_h264/whep"},
    ]
    store.save({"video_mode": "remote"})
    assert store.cameras() == [
        {"label": "CAM 95", "url": "http://10.20.100.1:8889/cam95_main/whep"},
        {"label": "CAM 96", "url": "http://10.20.100.1:8889/cam96_main/whep"},
    ]


def test_cameras_mode_override_does_not_persist(tmp_path):
    """The GET /?video=local recovery hatch."""
    store = _store(tmp_path)
    store.save({"video_mode": "remote"})
    assert "192.168.88.33" in store.cameras("local")[0]["url"]
    assert store.load()["video_mode"] == "remote"  # unchanged on disk
    assert "10.20.100.1" in store.cameras()[0]["url"]


def test_host_and_path_are_editable(tmp_path):
    store = _store(tmp_path)
    saved = store.save({
        "remote": {"host": "10.20.100.7", "streams": [{"path": "cam95_h264"}, {"path": "cam96_h264"}]},
    })
    assert saved["remote"]["host"] == "10.20.100.7"
    assert [s["path"] for s in saved["remote"]["streams"]] == ["cam95_h264", "cam96_h264"]
    # Labels are server-owned (cockpit.js derives the lens type from them).
    assert [s["label"] for s in saved["remote"]["streams"]] == ["CAM 95", "CAM 96"]


def test_invalid_input_keeps_the_previous_value(tmp_path):
    store = _store(tmp_path)
    store.save({"local": {"host": "192.168.88.44"}})
    saved = store.save({
        "video_mode": "sideways",                       # not a mode
        "local": {"host": "evil.example.com/../x",      # path smuggling
                  "streams": [{"path": "a/b"}, {"path": "ok_96"}]},
    })
    assert saved["video_mode"] == "local"               # unchanged
    assert saved["local"]["host"] == "192.168.88.44"    # previous value, not the default
    assert saved["local"]["streams"][0]["path"] == "cam95_h264"  # rejected
    assert saved["local"]["streams"][1]["path"] == "ok_96"       # accepted


def test_route_round_trip(authed_client):
    data = authed_client.get("/api/network-settings").get_json()
    assert data["video_mode"] == "local"

    resp = authed_client.post("/api/network-settings", json={"video_mode": "remote"})
    assert resp.status_code == 200
    assert resp.get_json()["video_mode"] == "remote"
    assert authed_client.get("/api/network-settings").get_json()["video_mode"] == "remote"


def test_route_requires_auth(client):
    assert client.get("/api/network-settings").status_code == 401
    assert client.post("/api/network-settings", json={}).status_code == 401


def test_index_injects_cameras_for_the_active_mode(authed_client):
    authed_client.post("/api/network-settings", json={"video_mode": "remote"})
    html = authed_client.get("/").get_data(as_text=True)
    assert "window.__NETWORK__" in html
    assert "10.20.100.1:8889/cam95_main/whep" in html

    # ?video=local recovers a cockpit whose saved gateway is unreachable.
    html = authed_client.get("/?video=local").get_data(as_text=True)
    assert "192.168.88.33:8889/cam95_h264/whep" in html


def test_stream_options_offer_sd_hd_and_raw_per_camera():
    """The ⚙ dropdowns: every offered path must exist in video_gateway/mediamtx.yml."""
    options = NetworkStore.stream_options()
    assert [o["path"] for o in options[0]] == ["cam95_h264", "cam95_h264_hd", "cam95_main"]
    assert [o["path"] for o in options[1]] == ["cam96_h264", "cam96_h264_hd", "cam96_main"]


def test_hd_streams_are_selectable(tmp_path):
    store = _store(tmp_path)
    store.save({
        "local": {"streams": [{"path": "cam95_h264_hd"}, {"path": "cam96_h264_hd"}]},
    })
    assert store.cameras() == [
        {"label": "CAM 95", "url": "http://192.168.88.33:8889/cam95_h264_hd/whep"},
        {"label": "CAM 96", "url": "http://192.168.88.33:8889/cam96_h264_hd/whep"},
    ]
