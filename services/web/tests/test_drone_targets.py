"""Unit tests for the drone-detection target parser and snapshot staleness gate.

Pure-function tests plus a snapshot check — no WebSocket is opened (the reader
thread only starts when DRONE_WS_ENABLED is set, Jetson/VPN only).
"""

from __future__ import annotations

import time

from app.turret import _TARGETS_STALE_SECONDS, parse_drone_targets

# Trimmed sample of a real drone-detection status frame: one FPV (type 1), two
# "Molnia" planes (types 2 and 3), one entry with no coordinates (must be skipped).
_SAMPLE = {
    "type": "status",
    "devices": {"1": {"id": 1}},  # ignored
    "targets": {
        "83979": {
            "id": 83979, "target_type_id": 2, "target_name": "Молнія",
            "altitude": 0, "video_freq": 5490,
            "actual_lat": "46.82191189882072", "actual_lon": "33.49079132080079",
        },
        "83996": {
            "id": 83996, "target_type_id": 1, "target_name": "FPV",
            "altitude": 120, "video_freq": 3800,
            "actual_lat": "46.87914089967373", "actual_lon": "33.33251953125001",
        },
        "84002": {
            "id": 84002, "target_type_id": 3, "target_name": "Молнія-2",
            "altitude": 0, "video_freq": 5705,
            "actual_lat": "46.587716417716116", "actual_lon": "32.87658691406251",
        },
        "99999": {  # no coordinates -> dropped
            "id": 99999, "target_type_id": 1, "target_name": "FPV",
            "altitude": 0, "video_freq": 1234,
        },
    },
}


def test_parse_extracts_valid_targets():
    out = parse_drone_targets(_SAMPLE)
    # The coordinate-less entry is skipped; the other three survive.
    assert len(out) == 3
    ids = [t["id"] for t in out]
    assert ids == sorted(ids)  # ordered by id


def test_parse_classifies_kind_and_coords():
    by_id = {t["id"]: t for t in parse_drone_targets(_SAMPLE)}
    assert by_id[83996]["kind"] == "fpv"       # type 1
    assert by_id[83979]["kind"] == "molnia"    # type 2
    assert by_id[84002]["kind"] == "molnia"    # type 3
    fpv = by_id[83996]
    assert isinstance(fpv["lat"], float) and isinstance(fpv["lon"], float)
    assert fpv["video_freq"] == 3800
    assert fpv["altitude"] == 120


def test_parse_handles_missing_or_bad_targets():
    assert parse_drone_targets({}) == []
    assert parse_drone_targets({"targets": None}) == []
    assert parse_drone_targets({"targets": {"1": "not-a-dict"}}) == []
    assert parse_drone_targets({"targets": {"1": {"actual_lat": "x", "actual_lon": "y"}}}) == []


def test_snapshot_serves_fresh_targets(controller):
    targets = parse_drone_targets(_SAMPLE)
    controller._targets = targets
    controller._targets_last_monotonic = time.monotonic()
    assert controller.snapshot()["targets"] == targets


def test_snapshot_drops_stale_targets(controller):
    controller._targets = parse_drone_targets(_SAMPLE)
    # Backdate the last update beyond the staleness window.
    controller._targets_last_monotonic = time.monotonic() - (_TARGETS_STALE_SECONDS + 5)
    assert controller.snapshot()["targets"] == []
