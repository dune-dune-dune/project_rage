"""Configuration for the Flask turret cockpit.

Two layers of settings are intentionally kept separate:

* Deployment / network / secrets  -> environment variables (``.env``).
* Control tuning (axis speeds, fire) -> ``settings.toml`` committed to the repo.

Both are merged into a single immutable :class:`Settings` object at startup.
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

    # --- Video ---
    # List of {"label": str, "url": str} for the TAB camera switcher.
    cameras: list[dict]

    # --- Control tuning (settings.toml) ---
    send_rate_hz: int
    deadman_ms: int
    # Velocity soft-start: time (ms) to ramp a manual axis from 0 to full scale.
    # Removes the one-time jerk at movement start. 0 disables the ramp (instant).
    ramp_ms: int
    speed_percent: int
    # Discrete rotation-speed levels (percent) selectable with keys 1..N. Each is
    # a multiplier applied to the manual-motion velocity; the last level is the
    # default (fastest). See TurretController._build_packet.
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

    # --- Auth ---
    # 7-digit login PIN and the Flask session secret, both from .env. When ``pin``
    # is empty the login gate is disabled and the cockpit is served openly.
    pin: str
    secret_key: str

    # --- Persistence ---
    crosshair_file: str
    ai_settings_file: str
    map_settings_file: str
    # Absolute path to the exported ONNX weights served to the browser, and the
    # optional class-names sidecar written by the export script.
    model_file: str
    classes_file: str

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

    Each entry is clamped to 10..100. Falls back to (50.0, 100.0) when the value
    is missing, not a list, or yields no valid entries.
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
        levels.append(max(10.0, min(100.0, value)))
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


def _build_cameras(video: dict) -> list[dict]:
    """Build the [{label, url}] camera list for the TAB switcher.

    The gateway base comes from WHEP_BASE, else is derived from WHEP_URL, else
    from VIDEO_GATEWAY_HOST_IP. Stream paths/labels come from settings.toml.
    Falls back to a single camera from WHEP_URL when no streams are configured.
    """
    whep_url = os.environ.get("WHEP_URL", "").strip()
    base = os.environ.get("WHEP_BASE", "").strip().rstrip("/")
    if not base and whep_url:
        # http://host:8889/cam95_h264/whep -> http://host:8889
        base = whep_url.rsplit("/", 2)[0]
    if not base:
        ip = os.environ.get("VIDEO_GATEWAY_HOST_IP", "192.168.88.33").strip() or "192.168.88.33"
        base = f"http://{ip}:8889"

    cameras: list[dict] = []
    for entry in video.get("streams", []):
        path = str(entry.get("path", "")).strip()
        if not path:
            continue
        cameras.append({"label": str(entry.get("label", path)), "url": f"{base}/{path}/whep"})

    if not cameras and whep_url:
        cameras.append({"label": "CAM", "url": whep_url})
    return cameras


def load_settings(settings_path: Path | None = None) -> Settings:
    """Build the merged, immutable settings object read once at startup."""
    load_env_file()
    toml = _load_toml(settings_path or _DEFAULT_SETTINGS_PATH)
    control = toml.get("control", {})
    axes = toml.get("axes", {})
    fire = toml.get("fire", {})
    video = toml.get("video", {})
    track = toml.get("track", {})

    return Settings(
        src_ip=os.environ.get("RWS_SRC_IP", "192.168.88.33"),
        src_port=_env_int("RWS_SRC_PORT", 7770),
        dst_ip=os.environ.get("RWS_DST_IP", "192.168.88.56"),
        dst_port=_env_int("RWS_DST_PORT", 7780),
        dry_run=_env_bool("RWS_DRY_RUN", True),
        salt=_load_salt(),
        cameras=_build_cameras(video),
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
        pin=os.environ.get("COCKPIT_PIN", "").strip(),
        secret_key=os.environ.get("SECRET_KEY", "").strip(),
        crosshair_file=str(_data_file("crosshair.json")),
        ai_settings_file=str(_data_file("ai_settings.json")),
        map_settings_file=str(_data_file("map_settings.json")),
        model_file=str(_data_file("model", "best.onnx")),
        classes_file=str(_data_file("model", "classes.json")),
    )


def _data_dir() -> Path:
    override = os.environ.get("COCKPIT_DATA_DIR", "").strip()
    return Path(override) if override else _HERE.parents[1] / "data"


def _data_file(*parts: str) -> Path:
    return _data_dir().joinpath(*parts)
