"""Persisted crosshair position.

The crosshair offset (horizontal / vertical, as a percentage of the viewport
from centre) is stored in a small JSON file so it survives restarts and can be
consumed by other tooling later. Thread-safe: the cockpit is multi-threaded.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

# Offset range in percent of the viewport, measured from centre.
_LIMIT = 50.0
_DEFAULT = {"x": 0.0, "y": 0.0}


def _clamp(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(-_LIMIT, min(_LIMIT, number))


class CrosshairStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                return dict(_DEFAULT)
            return {"x": _clamp(raw.get("x")), "y": _clamp(raw.get("y"))}

    def save(self, x: object, y: object) -> dict:
        data = {"x": _clamp(x), "y": _clamp(y)}
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data))
        return data


# --- AI (YOLO) detection settings ---------------------------------------------
# The operator-tunable detection thresholds, persisted server-side (like the
# crosshair) so they survive restarts. ``conf`` is the confidence threshold
# (0..1); ``min_size`` is the minimum object size in *source-frame pixels*
# (the box's longer side) below which a detection is ignored — this is a
# camera-relative unit, independent of the client-side digital zoom.
_CONF_DEFAULT = 0.70
_MIN_SIZE_DEFAULT = 24.0
# Percent colour-difference threshold for the Custom (pixel motion) detector.
_MOTION_DEFAULT = 15.0
# Ego-motion search range (working-resolution px/frame) for Custom mode.
_MAX_SHIFT_DEFAULT = 16.0
_MAX_SHIFT_LIMIT = 48.0
# Upper bound for min_size; the model input is 640 px, so anything larger than
# the frame is meaningless. Kept generous for future higher-res inputs.
_MIN_SIZE_LIMIT = 1024.0


def _clamp_conf(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _CONF_DEFAULT
    return max(0.0, min(1.0, number))


def _clamp_min_size(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _MIN_SIZE_DEFAULT
    return max(0.0, min(_MIN_SIZE_LIMIT, number))


def _clamp_motion(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _MOTION_DEFAULT
    return max(1.0, min(100.0, number))


def _clamp_max_shift(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _MAX_SHIFT_DEFAULT
    return max(0.0, min(_MAX_SHIFT_LIMIT, number))


class AiSettingsStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                raw = {}
            return {
                "conf": _clamp_conf(raw.get("conf")),
                "min_size": _clamp_min_size(raw.get("min_size")),
                "motion_thresh": _clamp_motion(raw.get("motion_thresh")),
                "max_shift": _clamp_max_shift(raw.get("max_shift")),
            }

    def save(self, conf: object, min_size: object, motion_thresh: object, max_shift: object) -> dict:
        data = {
            "conf": _clamp_conf(conf),
            "min_size": _clamp_min_size(min_size),
            "motion_thresh": _clamp_motion(motion_thresh),
            "max_shift": _clamp_max_shift(max_shift),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data))
        return data


# --- Map / turret orientation settings ----------------------------------------
# Persisted so the cockpit map widget can centre on a fixed origin and draw the
# turret's azimuth sector / elevation range. Units are degrees.
#   north_correction = compass offset (0..360) added to the turret's telemetry
#                      azimuth to get a map bearing: bearing = az + north_correction.
#   az_min/az_max = the turret's telemetry azimuth limits (deg), the swept sector
#                   — FIXED constants (not user-editable), used to draw the gauges
#                   and the map sector.
#   ele_min/ele_max = the turret's elevation limits (deg) — also fixed.
_MAP_LAT_DEFAULT = 0.0
_MAP_LON_DEFAULT = 0.0
_MAP_NORTH_CORR_DEFAULT = 0.0
_MAP_AZ_MIN_DEFAULT = -72.0
_MAP_AZ_MAX_DEFAULT = 72.0
_MAP_ELE_MIN_DEFAULT = -8.0
_MAP_ELE_MAX_DEFAULT = 30.0


def _clamp_range(value: object, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


class MapSettingsStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(raw: dict) -> dict:
        return {
            "lat": _clamp_range(raw.get("lat"), -90.0, 90.0, _MAP_LAT_DEFAULT),
            "lon": _clamp_range(raw.get("lon"), -180.0, 180.0, _MAP_LON_DEFAULT),
            "north_correction": _clamp_range(raw.get("north_correction"), 0.0, 360.0, _MAP_NORTH_CORR_DEFAULT),
            # Fixed azimuth/elevation ranges (kept for the gauges + map sector).
            "az_min": _MAP_AZ_MIN_DEFAULT,
            "az_max": _MAP_AZ_MAX_DEFAULT,
            "ele_min": _MAP_ELE_MIN_DEFAULT,
            "ele_max": _MAP_ELE_MAX_DEFAULT,
        }

    def load(self) -> dict:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            return self._normalize(raw)

    def save(self, payload: object) -> dict:
        raw = payload if isinstance(payload, dict) else {}
        data = self._normalize(raw)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data))
        return data
