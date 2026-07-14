"""HTTP surface for the speed feature: status exposes it, input mutates it."""

from __future__ import annotations


def test_status_includes_speed_fields(authed_client, app):
    snap = authed_client.get("/api/status").get_json()
    assert "speed_level" in snap and "speed_levels" in snap
    levels = app.config["SETTINGS"].speed_levels
    # Default is the FASTEST level, which is no longer the last one: [50, 100, 1].
    assert snap["speed_levels"] == len(levels)
    assert levels[snap["speed_level"] - 1] == max(levels)


def test_input_changes_speed_level(authed_client):
    assert authed_client.post("/api/input", json={"speed_level": 1}).status_code == 204
    snap = authed_client.get("/api/status").get_json()
    assert snap["speed_level"] == 1


def test_index_injects_speed_config(authed_client):
    html = authed_client.get("/").get_data(as_text=True)
    assert "window.__SPEED__" in html
