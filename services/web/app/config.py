"""Configuration for the Flask turret cockpit.

Three layers of settings are intentionally kept separate:

* Deployment / turret network / secrets -> environment variables (``.env``).
* Control tuning (axis speeds, fire)     -> ``settings.toml`` committed to the repo.
* Operator-tunable runtime settings      -> SQLite (:mod:`app.db` + :mod:`app.store`):
  crosshair, AI thresholds, map origin and the video/network profiles. These are
  editable from the cockpit UI and must NOT be re-added here — the video gateway
  address used to live in ``.env`` (``WHEP_URL`` / ``VIDEO_GATEWAY_HOST_IP``),
  which meant a redeploy just to move between the turret LAN and the VPN.

The first two are merged into a single immutable :class:`Settings` object at
startup; the third is read per request from the database.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The 32-byte shared salt authenticating every command. It lives in
# ``test_rws_control.py`` (DEFAULT_EMBEDDED_SALT); duplicated here so the web
# service does not import the POSIX-only TTY controller. Overridable via
# ``RWS_SALT_FILE``.
DEFAULT_EMBEDDED_SALT = bytes.fromhex(
    "262bd7b673f1371fd274f96f2e819032498f304b4021d3fc87d5db723f8fa277"
)

# Repository root, used only as a fallback so local runs can import rws_control.py
# from the repo root. In the container the library sits next to the app on the
# path, so this fallback is never exercised — guard against a shallow tree.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parents[-1]
_DEFAULT_SETTINGS_PATH = _HERE.parents[1] / "settings.toml"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    # --- RWS network (turret) ---
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    dry_run: bool
    salt: bytes

    # --- Targets feed (separate VM, reached over its own WireGuard tunnel) ---
    # The browser opens a WebSocket to this host:port for the live targets stream
    # (see static/ws-client.js). It is NOT the cockpit host: the targets server
    # lives on another VM (default 10.31.0.100), reachable only through the
    # wg-targets tunnel brought up by the compose sidecar. Empty host => the client
    # falls back to window.location.hostname (same host as the page).
    targets_ws_host: str
    targets_ws_port: int

    # --- Rangefinder (Benewake TF03-180, serial) ---
    # Only enabled on the Jetson (production), where the LiDAR is wired to a USB
    # serial port. When disabled, distance falls back to the turret status reply.
    rangefinder_enabled: bool
    rangefinder_port: str
    rangefinder_baud: int
    # Minimum spacing (ms) between turret rangefinder requests while the operator
    # holds the measure key (Shift). Independent of the serial TF03 above: this
    # paces the edge-triggered `rangefinder_seq` sent over the RWS command stream.
    rangefinder_measure_interval_ms: int

    # --- Control tuning (settings.toml) ---
    send_rate_hz: int
    deadman_ms: int
    # Velocity soft-start: time (ms) to ramp a manual axis from 0 to full scale.
    # Removes the one-time jerk at movement start. 0 disables the ramp (instant).
    ramp_ms: int
    speed_percent: int
    # Discrete rotation-speed levels (percent) selectable with keys 1..N. Each is
    # a multiplier applied to the manual-motion velocity. The list is NOT assumed
    # to be sorted — the boot default is the *highest* percent (see
    # `default_speed_index`), so a fine-aim level can be appended without making
    # it the level the cockpit starts on. See TurretController._build_packet.
    speed_levels: tuple[float, ...]
    rotation_v_unit: float
    elevation_v_up_unit: float
    elevation_v_down_unit: float
    fire_mode: str
    fire_duration_short: int
    fire_duration_medium: int

    # --- AI auto-track tuning (settings.toml [track]) ---
    # Proportional visual-servo gain, normalised deadzone and velocity cap, plus
    # the aim command freshness window and the model input size fed to the
    # browser-side YOLO (ONNX). No FOV calibration exists, so tracking is a
    # closed-loop pixel servo — these tune its feel, not an absolute mapping.
    track_gain: float
    track_deadzone: float
    track_max_velocity: float
    aim_timeout_ms: int
    ai_imgsz: int

    # --- Persistence ---
    # SQLite file holding every operator-tunable setting (crosshair, AI, map,
    # video/network profiles). Schema: app/migrations/*.sql.
    db_file: str
    # Pre-SQLite JSON files. Kept only as the source of the one-time import into
    # the database (app.db.import_legacy_json), which renames them afterwards.
    crosshair_file: str
    ai_settings_file: str
    map_settings_file: str
    # Pre-model-library ONNX weights (data/model/best.onnx) and their class-names
    # sidecar. Kept only as the source of the one-time import into the model
    # library (app.store.import_builtin_model); the cockpit serves the ACTIVE
    # model out of ``models_dir`` instead.
    model_file: str
    classes_file: str

    # --- AI model library ---
    # data/models/<id>/{source.pt|source.onnx, model.onnx, classes.json}. One
    # directory per uploaded model; the registry itself lives in SQLite.
    models_dir: str
    # Upper bound on an uploaded weights file. Flask buffers the whole multipart
    # body, so this must be set deliberately (413 above it).
    max_upload_mb: int
    # The exporter sidecar that owns ultralytics/torch and turns an uploaded .pt
    # into ONNX. Deliberately a separate container: a torch export can peg the
    # CPU for minutes, and this process also runs the 20 Hz turret loop.
    exporter_url: str
    # Where the exporter container sees the shared ./data bind mount. The two
    # containers mount the same directory at different paths, so the cockpit must
    # translate a model directory into the exporter's namespace before asking for it.
    exporter_data_dir: str

    @property
    def period_seconds(self) -> float:
        return 1.0 / self.send_rate_hz

    @property
    def deadman_seconds(self) -> float:
        return self.deadman_ms / 1000.0

    @property
    def accel_per_tick(self) -> float:
        """Max normalised velocity change per send tick for the soft-start ramp.

        Reaching full scale (1.0) takes ``ramp_ms``. ``ramp_ms <= 0`` yields 1.0,
        i.e. a full-scale step in one tick (ramp disabled, original behaviour).
        """
        if self.ramp_ms <= 0:
            return 1.0
        return self.period_seconds / (self.ramp_ms / 1000.0)

    @property
    def aim_timeout_seconds(self) -> float:
        return self.aim_timeout_ms / 1000.0

    @property
    def rangefinder_measure_interval_seconds(self) -> float:
        return self.rangefinder_measure_interval_ms / 1000.0

    @property
    def default_speed_index(self) -> int:
        """Index of the level the cockpit boots on: the fastest one.

        Deliberately an argmax, not ``len - 1``: the fine-aim level (1%) sits at
        the end of the list so it lands on key `3`, and booting into it would
        leave the operator with a turret that looks dead.
        """
        levels = self.speed_levels
        return max(range(len(levels)), key=levels.__getitem__)


def load_env_file() -> None:
    """Load services/web/.env into the environment for native (non-Docker) runs.

    A no-op under Docker Compose (env_file already populated the environment) and
    when python-dotenv is unavailable. Never overrides already-set variables.
    """
    env_path = _HERE.parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(env_path, override=False)


def _parse_speed_levels(raw: object) -> tuple[float, ...]:
    """Coerce the [control] speed_levels list into clamped percent multipliers.

    Each entry is clamped to 1..100 — the floor is 1 %, not 10 %, so a fine-aim
    level exists at all (1 % of full scale is int16 262, well above zero). Falls
    back to (50.0, 100.0) when the value is missing, not a list, or yields no
    valid entries.
    """
    default = (50.0, 100.0)
    if not isinstance(raw, (list, tuple)):
        return default
    levels: list[float] = []
    for item in raw:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        levels.append(max(1.0, min(100.0, value)))
    return tuple(levels) if levels else default


def _load_salt() -> bytes:
    salt_file = os.environ.get("RWS_SALT_FILE", "").strip()
    if not salt_file:
        return DEFAULT_EMBEDDED_SALT
    data = Path(salt_file).read_bytes()
    if len(data) != 32:
        raise ValueError(f"RWS_SALT_FILE must be exactly 32 bytes, got {len(data)}")
    return data


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_settings(settings_path: Path | None = None) -> Settings:
    """Build the merged, immutable settings object read once at startup."""
    load_env_file()
    toml = _load_toml(settings_path or _DEFAULT_SETTINGS_PATH)
    control = toml.get("control", {})
    axes = toml.get("axes", {})
    fire = toml.get("fire", {})
    track = toml.get("track", {})

    return Settings(
        src_ip=os.environ.get("RWS_SRC_IP", "192.168.88.33"),
        src_port=_env_int("RWS_SRC_PORT", 7770),
        dst_ip=os.environ.get("RWS_DST_IP", "192.168.88.56"),
        dst_port=_env_int("RWS_DST_PORT", 7780),
        dry_run=_env_bool("RWS_DRY_RUN", True),
        salt=_load_salt(),
        targets_ws_host=os.environ.get("TARGETS_WS_HOST", "10.31.0.100").strip(),
        targets_ws_port=_env_int("TARGETS_WS_PORT", 8766),
        rangefinder_enabled=_env_bool("RANGEFINDER_ENABLED", False),
        rangefinder_port=os.environ.get("RANGEFINDER_PORT", "/dev/ttyUSB0"),
        rangefinder_baud=_env_int("RANGEFINDER_BAUD", 115200),
        rangefinder_measure_interval_ms=int(control.get("rangefinder_measure_interval_ms", 250)),
        send_rate_hz=int(control.get("send_rate_hz", 20)),
        deadman_ms=int(control.get("deadman_ms", 400)),
        ramp_ms=int(control.get("ramp_ms", 250)),
        speed_percent=int(control.get("speed_percent", 100)),
        speed_levels=_parse_speed_levels(control.get("speed_levels", [50, 100])),
        rotation_v_unit=float(axes.get("rotation_v_unit", 0.5)),
        elevation_v_up_unit=float(axes.get("elevation_v_up_unit", 0.5)),
        elevation_v_down_unit=float(axes.get("elevation_v_down_unit", 0.5)),
        fire_mode=str(fire.get("mode", "short")),
        fire_duration_short=int(fire.get("duration_short", 161)),
        fire_duration_medium=int(fire.get("duration_medium", 605)),
        track_gain=float(track.get("gain", 2.5)),
        track_deadzone=float(track.get("deadzone", 0.02)),
        track_max_velocity=float(track.get("max_velocity", 0.5)),
        aim_timeout_ms=int(track.get("aim_timeout_ms", 500)),
        ai_imgsz=int(track.get("imgsz", 640)),
        db_file=str(_data_file("cockpit.db")),
        crosshair_file=str(_data_file("crosshair.json")),
        ai_settings_file=str(_data_file("ai_settings.json")),
        map_settings_file=str(_data_file("map_settings.json")),
        model_file=str(_data_file("model", "best.onnx")),
        classes_file=str(_data_file("model", "classes.json")),
        models_dir=str(_data_file("models")),
        max_upload_mb=_env_int("MODEL_MAX_UPLOAD_MB", 512),
        exporter_url=os.environ.get("EXPORTER_URL", "http://127.0.0.1:8901").rstrip("/"),
        exporter_data_dir=os.environ.get("EXPORTER_DATA_DIR", "/data"),
    )


def _data_dir() -> Path:
    override = os.environ.get("COCKPIT_DATA_DIR", "").strip()
    return Path(override) if override else _HERE.parents[1] / "data"


def _data_file(*parts: str) -> Path:
    return _data_dir().joinpath(*parts)
