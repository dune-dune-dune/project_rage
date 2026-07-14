"""Operator-tunable settings, persisted in SQLite (see :mod:`app.db`).

Each store owns one section of the ``settings`` table (one JSON blob keyed by
name) and is the single place where that section's values are validated. Every
store clamps rather than rejects, so a malformed payload degrades to a safe
value instead of 500-ing the cockpit — except :class:`NetworkStore`, where an
invalid host/path *keeps the previous value* (a clamped-to-default video host
would silently swap the operator's stream).

These were JSON files (data/*.json) before; the constructor now takes a
:class:`~app.db.SettingsDb` + key instead of a path. ``load()`` / ``save()``
keep their old shape, so the routes and the browser code are unchanged.
"""

from __future__ import annotations

import re
import threading

from .db import KEY_AI, KEY_CROSSHAIR, KEY_MAP, KEY_NETWORK, SettingsDb

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
    """Persisted crosshair offset (percent of the viewport, from centre)."""

    def __init__(self, db: SettingsDb, key: str = KEY_CROSSHAIR) -> None:
        self._db = db
        self._key = key
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            raw = self._db.get(self._key) or {}
            return {"x": _clamp(raw.get("x")), "y": _clamp(raw.get("y"))}

    def save(self, x: object, y: object) -> dict:
        data = {"x": _clamp(x), "y": _clamp(y)}
        with self._lock:
            self._db.put(self._key, data)
        return data


# --- AI (YOLO) detection settings ---------------------------------------------
# The operator-tunable detection thresholds. ``conf`` is the confidence threshold
# (0..1); ``min_size`` is the minimum object size in *source-frame pixels* (the
# box's longer side) below which a detection is ignored — a camera-relative unit,
# independent of the client-side digital zoom.
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
    def __init__(self, db: SettingsDb, key: str = KEY_AI) -> None:
        self._db = db
        self._key = key
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            raw = self._db.get(self._key) or {}
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
            self._db.put(self._key, data)
        return data


# --- Map / turret orientation settings ----------------------------------------
# Lets the cockpit map widget centre on a fixed origin and draw the turret's
# azimuth sector / elevation range. Units are degrees.
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
    def __init__(self, db: SettingsDb, key: str = KEY_MAP) -> None:
        self._db = db
        self._key = key
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
            raw = self._db.get(self._key) or {}
            return self._normalize(raw)

    def save(self, payload: object) -> dict:
        raw = payload if isinstance(payload, dict) else {}
        data = self._normalize(raw)
        with self._lock:
            self._db.put(self._key, data)
        return data


# --- Video / network profiles --------------------------------------------------
# Which video gateway the *browser* pulls WHEP from. The cockpit itself always
# talks to the turret over the LAN; only the operator's browser moves between the
# turret LAN ("local") and the WireGuard VPN ("remote"), and it fetches the WHEP
# offer straight from MediaMTX, so the two need different gateway hosts.
#
# Previously WHEP_URL + VIDEO_GATEWAY_HOST_IP in .env, read once at startup.
# Now DB-backed and switchable from the cockpit UI (⚙ -> Налаштування мережі).
MODE_LOCAL = "local"
MODE_REMOTE = "remote"
_MODES = (MODE_LOCAL, MODE_REMOTE)

# Labels are server-side constants, never writable over HTTP: cockpit.js's
# cameraKind() derives the lens type from the "95"/"96" substring in the label.
_CAM_LABELS = ("CAM 95", "CAM 96")

# MediaMTX WHEP port. Fixed (see services/video_gateway/mediamtx.yml).
_WHEP_PORT = 8889

_NETWORK_DEFAULT = {
    "video_mode": MODE_LOCAL,
    MODE_LOCAL: {
        "host": "192.168.88.33",
        "streams": [
            {"label": "CAM 95", "path": "cam95_h264"},
            {"label": "CAM 96", "path": "cam96_h264"},
        ],
    },
    MODE_REMOTE: {
        "host": "10.20.100.1",
        "streams": [
            {"label": "CAM 95", "path": "cam95_main"},
            {"label": "CAM 96", "path": "cam96_main"},
        ],
    },
}

# A host is interpolated into a URL the operator's browser then POSTs its SDP to,
# so it must not be able to smuggle a path, port, scheme or credentials.
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
# MediaMTX path name (services/video_gateway/mediamtx.yml).
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class NetworkStore:
    """Video gateway profiles + the active one (local LAN vs remote VPN)."""

    def __init__(self, db: SettingsDb, key: str = KEY_NETWORK) -> None:
        self._db = db
        self._key = key
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            return self._normalize(self._db.get(self._key) or {})

    def save(self, payload: object) -> dict:
        raw = payload if isinstance(payload, dict) else {}
        with self._lock:
            current = self._normalize(self._db.get(self._key) or {})
            data = self._merge(current, raw)
            self._db.put(self._key, data)
        return data

    def cameras(self, mode: str | None = None) -> list[dict]:
        """The [{label, url}] camera list for the TAB switcher.

        ``mode`` overrides the stored one without persisting it — the recovery
        path for a saved-but-unreachable host (GET /?video=local).
        """
        data = self.load()
        profile = data[mode if mode in _MODES else data["video_mode"]]
        host = profile["host"]
        return [
            {"label": stream["label"], "url": f"http://{host}:{_WHEP_PORT}/{stream['path']}/whep"}
            for stream in profile["streams"]
        ]

    # --- normalisation ---------------------------------------------------------

    @classmethod
    def _normalize(cls, raw: dict) -> dict:
        mode = raw.get("video_mode")
        return {
            "video_mode": mode if mode in _MODES else MODE_LOCAL,
            MODE_LOCAL: cls._profile(raw.get(MODE_LOCAL), _NETWORK_DEFAULT[MODE_LOCAL]),
            MODE_REMOTE: cls._profile(raw.get(MODE_REMOTE), _NETWORK_DEFAULT[MODE_REMOTE]),
        }

    @staticmethod
    def _profile(raw: object, default: dict) -> dict:
        source = raw if isinstance(raw, dict) else {}
        host = source.get("host")
        streams = source.get("streams")
        if not isinstance(streams, list):
            streams = []
        out_streams = []
        for index, label in enumerate(_CAM_LABELS):
            entry = streams[index] if index < len(streams) and isinstance(streams[index], dict) else {}
            path = entry.get("path")
            if not (isinstance(path, str) and _PATH_RE.match(path)):
                path = default["streams"][index]["path"]
            out_streams.append({"label": label, "path": path})
        return {
            "host": host if isinstance(host, str) and _HOST_RE.match(host) else default["host"],
            "streams": out_streams,
        }

    @staticmethod
    def _merge(current: dict, patch: dict) -> dict:
        """Apply a client patch over the current settings.

        Unlike the other stores, an invalid value here keeps the *current* value
        rather than snapping back to the built-in default: a typo in the host
        field must not silently repoint video at a different gateway.
        """
        data = {
            "video_mode": current["video_mode"],
            MODE_LOCAL: {"host": current[MODE_LOCAL]["host"], "streams": list(current[MODE_LOCAL]["streams"])},
            MODE_REMOTE: {"host": current[MODE_REMOTE]["host"], "streams": list(current[MODE_REMOTE]["streams"])},
        }
        mode = patch.get("video_mode")
        if mode in _MODES:
            data["video_mode"] = mode
        for name in _MODES:
            incoming = patch.get(name)
            if not isinstance(incoming, dict):
                continue
            host = incoming.get("host")
            if isinstance(host, str) and _HOST_RE.match(host.strip()):
                data[name]["host"] = host.strip()
            streams = incoming.get("streams")
            if not isinstance(streams, list):
                continue
            for index in range(len(_CAM_LABELS)):
                entry = streams[index] if index < len(streams) and isinstance(streams[index], dict) else {}
                path = entry.get("path")
                if isinstance(path, str) and _PATH_RE.match(path.strip()):
                    data[name]["streams"][index] = {"label": _CAM_LABELS[index], "path": path.strip()}
        return data
