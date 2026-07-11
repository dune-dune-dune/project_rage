"""Map-settings store + /api/map-settings HTTP surface.

The store validates/clamps on both read and write (like CrosshairStore), and the
routes are thin pass-throughs behind the auth gate. Tests point COCKPIT_DATA_DIR
at a tmp dir so nothing touches the real services/web/data/.
"""

from __future__ import annotations

from app.store import MapSettingsStore

_KEYS = {"lat", "lon", "north_correction", "az_min", "az_max", "ele_min", "ele_max"}


def test_defaults_when_missing(tmp_path):
    store = MapSettingsStore(str(tmp_path / "map_settings.json"))
    data = store.load()
    assert set(data) == _KEYS
    assert data["north_correction"] == 0.0
    assert data["az_min"] == -72.0 and data["az_max"] == 72.0
    assert data["ele_min"] == -8.0 and data["ele_max"] == 30.0
    assert data["lat"] == 0.0 and data["lon"] == 0.0


def test_save_round_trip(tmp_path):
    store = MapSettingsStore(str(tmp_path / "map_settings.json"))
    saved = store.save({"lat": 50.45, "lon": 30.52, "north_correction": 200})
    assert saved["lat"] == 50.45 and saved["lon"] == 30.52
    assert saved["north_correction"] == 200.0
    # Ranges are fixed constants regardless of input.
    assert saved["az_min"] == -72.0 and saved["az_max"] == 72.0
    # Reloading a fresh store from the same file yields identical values.
    reloaded = MapSettingsStore(str(tmp_path / "map_settings.json")).load()
    assert reloaded == saved


def test_clamping_and_bad_input(tmp_path):
    store = MapSettingsStore(str(tmp_path / "map_settings.json"))
    data = store.save({"lat": 999, "lon": -999, "north_correction": 500})
    assert data["lat"] == 90.0 and data["lon"] == -180.0  # clamped to bounds
    assert data["north_correction"] == 360.0  # clamped to 0..360
    # Fixed ranges are never affected by input.
    assert data["az_min"] == -72.0 and data["az_max"] == 72.0
    assert data["ele_min"] == -8.0 and data["ele_max"] == 30.0


def test_bad_north_correction_falls_back(tmp_path):
    store = MapSettingsStore(str(tmp_path / "map_settings.json"))
    data = store.save({"north_correction": "oops"})
    assert data["north_correction"] == 0.0


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "map_settings.json"
    path.write_text("{ not json")
    data = MapSettingsStore(str(path)).load()
    assert data["az_min"] == -72.0  # degraded gracefully


def _authed_client_with_data_dir(app_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path))
    app = app_factory()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["authed"] = True
    return c


def test_route_get_returns_defaults(app_factory, tmp_path, monkeypatch):
    c = _authed_client_with_data_dir(app_factory, tmp_path, monkeypatch)
    data = c.get("/api/map-settings").get_json()
    assert set(data) == _KEYS
    assert data["az_min"] == -72.0


def test_route_post_persists_and_reflects(app_factory, tmp_path, monkeypatch):
    c = _authed_client_with_data_dir(app_factory, tmp_path, monkeypatch)
    resp = c.post("/api/map-settings", json={"lat": 50.4, "lon": 30.5, "north_correction": 90})
    assert resp.status_code == 200
    assert resp.get_json()["lat"] == 50.4
    again = c.get("/api/map-settings").get_json()
    assert again["lat"] == 50.4 and again["north_correction"] == 90.0


def test_route_requires_auth(app_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path))
    app = app_factory()  # default PIN set → gate active
    anon = app.test_client()
    assert anon.get("/api/map-settings").status_code == 401


def test_index_injects_map_settings(app_factory, tmp_path, monkeypatch):
    c = _authed_client_with_data_dir(app_factory, tmp_path, monkeypatch)
    html = c.get("/").get_data(as_text=True)
    assert "window.__MAP__" in html
