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

import json
import logging
import re
import shutil
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .db import KEY_AI, KEY_CROSSHAIR, KEY_DRONE, KEY_MAP, KEY_MODELS, KEY_NETWORK, SettingsDb

log = logging.getLogger("cockpit.store")

# Offset range in percent of the viewport, measured from centre. The operator
# aims in 0.01 % steps, so the stored value keeps two decimals.
_LIMIT = 50.0
_DECIMALS = 2
_DEFAULT = {"x": 0.0, "y": 0.0}


def _clamp(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return round(max(-_LIMIT, min(_LIMIT, number)), _DECIMALS)


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

# The stream (= video quality) catalogue offered per camera in the settings
# dropdowns. Every path must exist in services/video_gateway/mediamtx.yml:
#   *_h264     -> camera sub-stream av0_1 (640x480), transcoded to H264
#   *_h264_hd  -> camera main stream av0_0 (1080p), transcoded to H264 — heavier
#                 on the gateway CPU; if ffmpeg cannot keep up, latency grows
#   *_main     -> camera main stream av0_0 as-is: H265, which only Safari decodes
#                 over WebRTC (elsewhere the connection succeeds, picture is black)
# This list only *offers* paths; save() still accepts any syntactically valid path
# (see _merge), so a path added to mediamtx.yml later is not locked out.
_STREAM_OPTIONS = (
    (
        {"path": "cam95_h264", "label": "SD 640 · H264"},
        {"path": "cam95_h264_hd", "label": "HD 1080 · H264"},
        {"path": "cam95_main", "label": "HD 1080 · H265 (лише Safari)"},
    ),
    (
        {"path": "cam96_h264", "label": "SD 640 · H264"},
        {"path": "cam96_h264_hd", "label": "HD 1080 · H264"},
        {"path": "cam96_main", "label": "HD 1080 · H265 (лише Safari)"},
    ),
)


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

    @staticmethod
    def stream_options() -> list[list[dict]]:
        """Selectable streams per camera, in _CAM_LABELS order (for the UI dropdowns)."""
        return [[dict(option) for option in camera] for camera in _STREAM_OPTIONS]

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


# --- Drone-detection feed ------------------------------------------------------
# The external server (far end of the WireGuard tunnel) streams target lat/lon
# over a WebSocket; the reader thread in TurretController consumes it. Unlike the
# network store (browser-side) this is read server-side, so it is edited from the
# cockpit ⚙ panel and hot-applied by the reader thread — no page reload.
_DRONE_URL_DEFAULT = "ws://10.20.100.1:8766"
# A WebSocket URL: ws:// or wss://, a hostname/IP, optional :port and path. Kept
# strict since it is opened by websocket-client server-side; an invalid value
# keeps the previous one (like NetworkStore) rather than snapping to a default.
_WS_URL_RE = re.compile(r"^wss?://[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?(/[^\s]*)?$")


class DroneStore:
    """Drone-detection WS feed: {"enabled": bool, "url": "ws://host:port"}."""

    def __init__(self, db: SettingsDb, key: str = KEY_DRONE) -> None:
        self._db = db
        self._key = key
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(raw: dict) -> dict:
        url = raw.get("url")
        return {
            "enabled": bool(raw.get("enabled", False)),
            "url": url.strip() if isinstance(url, str) and _WS_URL_RE.match(url.strip()) else _DRONE_URL_DEFAULT,
        }

    def load(self) -> dict:
        with self._lock:
            return self._normalize(self._db.get(self._key) or {})

    def save(self, payload: object) -> dict:
        patch = payload if isinstance(payload, dict) else {}
        with self._lock:
            current = self._normalize(self._db.get(self._key) or {})
            # enabled always applies; an invalid url keeps the current value.
            data = {"enabled": bool(patch.get("enabled", current["enabled"])), "url": current["url"]}
            url = patch.get("url")
            if isinstance(url, str) and _WS_URL_RE.match(url.strip()):
                data["url"] = url.strip()
            self._db.put(self._key, data)
        return data


# --- AI model library ----------------------------------------------------------
# The operator can upload new YOLO weights from the cockpit and switch between
# them at runtime. Each model owns a directory under ``data/models/<id>/``:
#
#   source.pt                 the uploaded checkpoint, kept so it can be re-exported
#   model.onnx                what the browser's ONNX Runtime actually loads (an
#                             uploaded .onnx IS this file — it is not converted)
#   classes.json              index -> class name, for the detection labels
#
# The registry (name, status, class names, size) is a real SQL table rather than
# a settings blob — see app/migrations/0003_models.sql. Only which model is
# *active* is a settings key (KEY_MODELS), so it round-trips like every other
# operator setting.

STATUS_PENDING = "pending"
STATUS_CONVERTING = "converting"
STATUS_READY = "ready"
STATUS_ERROR = "error"

SOURCE_PT = "pt"
SOURCE_ONNX = "onnx"

MODEL_FILENAME = "model.onnx"
CLASSES_FILENAME = "classes.json"

# Operator-facing label. Ukrainian letters are expected, so this is a blocklist of
# the characters that would break the UI rather than an ASCII whitelist — the name
# never reaches the filesystem (the directory is named by the generated id).
_MODEL_NAME_MAX = 48
_MODEL_NAME_BAD = re.compile(r"[\x00-\x1f<>]")
# Generated server-side, and the ONLY thing interpolated into a filesystem path
# and a URL — hence the strict whitelist.
_MODEL_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")

_IMGSZ_MIN, _IMGSZ_MAX = 32, 4096


def _clean_model_name(value: object, fallback: str = "модель") -> str:
    name = value.strip() if isinstance(value, str) else ""
    name = _MODEL_NAME_BAD.sub("", name)[:_MODEL_NAME_MAX].strip()
    return name or fallback


class ModelStore:
    """The AI model library: rows in `models` + the active-model setting."""

    def __init__(self, db: SettingsDb, models_dir: str, default_imgsz: int, key: str = KEY_MODELS) -> None:
        self._db = db
        self._dir = Path(models_dir)
        self._default_imgsz = default_imgsz
        self._key = key
        self._lock = threading.Lock()

    # --- paths -----------------------------------------------------------------

    def dir_for(self, model_id: str) -> Path | None:
        """The model's directory, or None if the id is not one we could have made."""
        if not _MODEL_ID_RE.match(model_id or ""):
            return None
        return self._dir / model_id

    def file_for(self, model_id: str, filename: str) -> Path | None:
        if filename not in (MODEL_FILENAME, CLASSES_FILENAME):
            return None
        directory = self.dir_for(model_id)
        return None if directory is None else directory / filename

    # --- reads -----------------------------------------------------------------

    def list(self) -> list[dict]:
        with closing(self._db.connect()) as conn:
            rows = conn.execute(
                "SELECT id, name, status, error, source, imgsz, classes, size_bytes, builtin, created_at "
                "FROM models ORDER BY builtin DESC, created_at ASC"
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, model_id: str) -> dict | None:
        if not _MODEL_ID_RE.match(model_id or ""):
            return None
        with closing(self._db.connect()) as conn:
            row = conn.execute(
                "SELECT id, name, status, error, source, imgsz, classes, size_bytes, builtin, created_at "
                "FROM models WHERE id = ?",
                (model_id,),
            ).fetchone()
        return self._row(row) if row else None

    def active(self) -> dict | None:
        """The model the cockpit serves — or None when the library is empty.

        Falls back rather than fails: a stored id that no longer exists (or is not
        ready) degrades to the builtin model, then to any ready model. Losing the
        active row must never leave the AI mode with no weights to load.
        """
        stored = (self._db.get(self._key) or {}).get("active")
        models = self.list()
        ready = [m for m in models if m["status"] == STATUS_READY]
        if not ready:
            return None
        for model in ready:
            if model["id"] == stored:
                return model
        for model in ready:
            if model["builtin"]:
                return model
        return ready[0]

    # --- writes ----------------------------------------------------------------

    def set_active(self, model_id: str) -> dict | None:
        model = self.get(model_id)
        if model is None or model["status"] != STATUS_READY:
            return None
        with self._lock:
            self._db.put(self._key, {"active": model["id"]})
        return model

    def create(self, name: object, source: str, builtin: bool = False) -> dict:
        """Register a new model and create its (empty) directory.

        The caller then writes the uploaded file into ``dir_for(id)`` and either
        finishes it with :meth:`set_status` (a ready-made .onnx) or hands it to the
        exporter (a .pt), which finishes it asynchronously.
        """
        model_id = uuid.uuid4().hex[:12]
        row = {
            "id": model_id,
            "name": _clean_model_name(name),
            "status": STATUS_PENDING,
            "error": "",
            "source": SOURCE_ONNX if source == SOURCE_ONNX else SOURCE_PT,
            "imgsz": self._default_imgsz,
            "classes": "{}",
            "size_bytes": 0,
            "builtin": 1 if builtin else 0,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (self._dir / model_id).mkdir(parents=True, exist_ok=True)
        with self._db.write_lock, closing(self._db.connect()) as conn:
            conn.execute(
                "INSERT INTO models (id, name, status, error, source, imgsz, classes, size_bytes, builtin, created_at)"
                " VALUES (:id, :name, :status, :error, :source, :imgsz, :classes, :size_bytes, :builtin, :created_at)",
                row,
            )
        return self.get(model_id)  # type: ignore[return-value]  # just inserted

    def set_status(
        self,
        model_id: str,
        status: str,
        error: str = "",
        imgsz: object = None,
        classes: object = None,
        size_bytes: object = None,
    ) -> dict | None:
        if self.get(model_id) is None:
            return None
        fields: list[str] = ["status = ?", "error = ?"]
        values: list[object] = [status, str(error or "")[:500]]
        if imgsz is not None:
            fields.append("imgsz = ?")
            values.append(int(_clamp_range(imgsz, _IMGSZ_MIN, _IMGSZ_MAX, self._default_imgsz)))
        if classes is not None:
            fields.append("classes = ?")
            values.append(json.dumps(_clean_classes(classes), ensure_ascii=False))
        if size_bytes is not None:
            fields.append("size_bytes = ?")
            values.append(max(0, int(size_bytes)))
        values.append(model_id)
        with self._db.write_lock, closing(self._db.connect()) as conn:
            conn.execute(f"UPDATE models SET {', '.join(fields)} WHERE id = ?", values)
        return self.get(model_id)

    def rename(self, model_id: str, name: object) -> dict | None:
        model = self.get(model_id)
        if model is None:
            return None
        with self._db.write_lock, closing(self._db.connect()) as conn:
            conn.execute("UPDATE models SET name = ? WHERE id = ?", (_clean_model_name(name, model["name"]), model_id))
        return self.get(model_id)

    def delete(self, model_id: str) -> tuple[bool, str]:
        """Remove a model and its files. Returns ``(ok, reason)``.

        The builtin and the active model are protected: the cockpit must always
        have weights to fall back to, and deleting what the browser is currently
        running would 404 the next AI toggle.
        """
        model = self.get(model_id)
        if model is None:
            return False, "Модель не знайдено"
        if model["builtin"]:
            return False, "Базову модель видалити не можна"
        active = self.active()
        if active and active["id"] == model_id:
            return False, "Спочатку зробіть активною іншу модель"
        with self._db.write_lock, closing(self._db.connect()) as conn:
            conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        directory = self.dir_for(model_id)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        return True, ""

    def fail_interrupted(self) -> int:
        """Mark conversions that a restart killed mid-flight as failed.

        The job lives in a daemon thread and the export in a sidecar container, so
        a `docker compose down` (every deploy) leaves the row stuck at
        ``converting`` forever. Reset them at startup instead.
        """
        with self._db.write_lock, closing(self._db.connect()) as conn:
            cursor = conn.execute(
                "UPDATE models SET status = ?, error = ? WHERE status IN (?, ?)",
                (STATUS_ERROR, "Конвертацію перервано перезапуском", STATUS_PENDING, STATUS_CONVERTING),
            )
            return cursor.rowcount or 0

    # --- normalisation ---------------------------------------------------------

    def _row(self, row: tuple) -> dict:
        try:
            classes = json.loads(row[6])
        except ValueError:
            classes = {}
        return {
            "id": row[0],
            "name": row[1],
            "status": row[2],
            "error": row[3],
            "source": row[4],
            "imgsz": int(row[5]),
            "classes": classes if isinstance(classes, dict) else {},
            "size_bytes": int(row[7]),
            "builtin": bool(row[8]),
            "created_at": row[9],
        }


def _clean_classes(value: object) -> dict:
    """Coerce a class map into {"0": "name"}; anything unusable becomes empty."""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(name)[:64] for key, name in list(value.items())[:1000]}


def import_builtin_model(store: ModelStore, settings) -> None:
    """One-time import of the pre-library data/model/best.onnx into the library.

    Copies (never moves) the file, so the old fixed path stays as a safety net.
    Only runs while the library is empty — and since the builtin model cannot be
    deleted, "empty" only ever means the first boot after this feature shipped.
    """
    if store.list():
        return
    source = Path(settings.model_file)
    if not source.exists():
        return
    model = store.create("Базова модель", SOURCE_ONNX, builtin=True)
    target_dir = store.dir_for(model["id"])
    assert target_dir is not None  # id is generated by create()
    shutil.copyfile(source, target_dir / MODEL_FILENAME)

    classes: dict = {}
    legacy_classes = Path(settings.classes_file)
    if legacy_classes.exists():
        try:
            parsed = json.loads(legacy_classes.read_text())
            classes = parsed if isinstance(parsed, dict) else {}
        except (ValueError, OSError):
            log.warning("legacy %s is unreadable — importing the model without class names", legacy_classes)
    (target_dir / CLASSES_FILENAME).write_text(json.dumps(classes, ensure_ascii=False, indent=2))

    store.set_status(
        model["id"],
        STATUS_READY,
        imgsz=settings.ai_imgsz,
        classes=classes,
        size_bytes=source.stat().st_size,
    )
    store.set_active(model["id"])
    log.info("imported %s into the model library as the builtin model", source.name)
