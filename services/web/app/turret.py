"""Turret control: owns the UDP channel and streams commands at a fixed rate.

A single :class:`TurretController` instance owns the RWS command channel and the
sequence counter. HTTP handlers only mutate a small, lock-guarded *intent*
(which keys are held) and refresh a deadman timestamp; a dedicated background
thread translates that intent into a 40-byte command packet every ``PERIOD_MS``
and transmits it — exactly like the TTY controller's 20 Hz loop, but driven by
the browser instead of the keyboard.

The protocol primitives are reused from the mature ``rws_control`` library; this
module never re-implements packet building or checksums.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass, field

from .config import Settings

try:  # rws_control.py lives at the repo root (copied next to /app in Docker).
    import rws_control
except ModuleNotFoundError:  # local runs: add the repo root to the path.
    from .config import _REPO_ROOT

    sys.path.insert(0, str(_REPO_ROOT))
    import rws_control

log = logging.getLogger("cockpit.turret")

# Position targets mirror the reference motion model: a held axis commands a
# full-scale +/-pi position target while velocity drives the actual motion.
_POSITIVE_TARGET_RAD = math.pi
_NEGATIVE_TARGET_RAD = -math.pi

_ARM_ON = b"A\x00\x00\x00"
_ARM_OFF = b"\x00\x00\x00\x00"
_FIRE_ON = b"F\x00"
_FIRE_OFF = b"\x00\x00"

# HUD link freshness: the turret replies at ~20 Hz, so no reply for 1 s is stale.
_LINK_STALE_SECONDS = 1.0
# When the source IP is not yet configured, retry the socket bind on this cadence.
_OPEN_RETRY_SECONDS = 1.0


@dataclass
class _Intent:
    """Lock-guarded operator intent set from HTTP requests."""

    up: bool = False
    down: bool = False
    left: bool = False
    right: bool = False
    # F toggle: False = safety engaged (firing blocked). Movement is NOT gated by
    # safety — the turret can always be rotated; safety only affects firing.
    safety_off: bool = False
    fire_held: bool = False   # Space held


_FIRE_MODES = ("short", "medium", "manual")


class TurretController:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.Lock()
        self._intent = _Intent()
        self._fire_mode = settings.fire_mode if settings.fire_mode in _FIRE_MODES else "short"
        self._last_input_monotonic = 0.0

        # State owned exclusively by the sender thread — no lock needed for these.
        self._next_sequence = 0
        self._fire_seq = 0
        self._fire_was_active = False
        self._packets_sent = 0
        self._replies_received = 0
        self._last_reply_monotonic = 0.0
        self._bind_error: str | None = None
        self._next_open_attempt = 0.0

        self._channel: rws_control.RwsControlChannel | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._s.dry_run:
            log.warning("RWS_DRY_RUN enabled: packets are built and logged but NOT transmitted")
        else:
            # Try to bind now; if the source IP is not configured yet, the sender
            # loop keeps retrying so control starts as soon as the IP appears.
            self._try_open_channel(time.monotonic())

        self._thread = threading.Thread(target=self._run_loop, name="rws-sender", daemon=True)
        self._thread.start()
        log.info("Turret sender thread started at %d Hz", self._s.send_rate_hz)

    def _try_open_channel(self, now: float) -> None:
        """Attempt to open the UDP channel; record a clear error on failure."""
        self._next_open_attempt = now + _OPEN_RETRY_SECONDS
        channel = rws_control.RwsControlChannel(
            bind_ip=self._s.src_ip,
            bind_port=self._s.src_port,
            dst_ip=self._s.dst_ip,
            dst_port=self._s.dst_port,
        )
        try:
            channel.open()
        except RuntimeError as exc:
            if self._bind_error is None:  # log once until it recovers
                log.error(
                    "Cannot bind %s:%s — is the address configured on this host? %s",
                    self._s.src_ip, self._s.src_port, exc,
                )
            self._bind_error = str(exc)
            return
        self._channel = channel
        self._bind_error = None
        log.info(
            "RWS channel open %s:%s -> %s:%s",
            self._s.src_ip, self._s.src_port, self._s.dst_ip, self._s.dst_port,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    # --------------------------------------------------------------------- input
    def apply_input(self, payload: dict) -> None:
        """Replace operator intent from a browser POST and refresh the deadman."""
        with self._lock:
            self._intent = _Intent(
                up=bool(payload.get("up", False)),
                down=bool(payload.get("down", False)),
                left=bool(payload.get("left", False)),
                right=bool(payload.get("right", False)),
                safety_off=bool(payload.get("safety", False)),
                fire_held=bool(payload.get("fire", False)),
            )
            mode = payload.get("fire_mode")
            if mode in _FIRE_MODES:
                self._fire_mode = mode
            self._last_input_monotonic = time.monotonic()

    def _read_intent(self, now: float) -> _Intent | None:
        """Return a snapshot of intent, or None if the deadman has expired."""
        with self._lock:
            if now - self._last_input_monotonic > self._s.deadman_seconds:
                return None  # fail-safe: no fresh input -> fully neutral packet
            # Shallow copy so the sender thread reads a stable snapshot.
            return _Intent(**vars(self._intent))

    # ------------------------------------------------------------------ send loop
    def _run_loop(self) -> None:
        period = self._s.period_seconds
        next_send_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_send_at:
                self._stop_event.wait(min(next_send_at - now, period))
                continue

            # Live mode with no socket yet (IP not configured at boot): keep retrying.
            if not self._s.dry_run and self._channel is None and now >= self._next_open_attempt:
                self._try_open_channel(now)

            intent = self._read_intent(now)
            packet = self._neutral_packet() if intent is None else self._build_packet(intent, now)
            try:
                if self._channel is not None:
                    self._channel.send_command(packet)
                    for event in self._channel.poll_events():
                        if event.kind == "reply":
                            self._replies_received += 1
                            self._last_reply_monotonic = now
            except OSError:
                log.exception("RWS send failed")
            self._packets_sent += 1
            if self._packets_sent % self._s.send_rate_hz == 0:
                self._log_heartbeat(packet, now)  # ~once per second

            next_send_at += period
            if now - next_send_at > period:  # fell behind (e.g. GC pause): resync
                next_send_at = now + period

    def _log_heartbeat(self, packet: rws_control.CommandPacket, now: float) -> None:
        if self._s.dry_run:
            log.info("dry-run tx: %s", packet.summary())
        elif self._bind_error is not None:
            log.warning("NOT transmitting: %s", self._bind_error)
        else:
            log.info(
                "tx=%d rx=%d link=%s", self._packets_sent, self._replies_received,
                self._link_state(now),
            )

    def _link_state(self, now: float) -> str:
        if self._replies_received == 0:
            return "offline"
        return "online" if (now - self._last_reply_monotonic) <= _LINK_STALE_SECONDS else "stale"

    def _neutral_packet(self) -> rws_control.CommandPacket:
        """Fully inert packet (motors off, disarmed) used when the deadman fires."""
        self._fire_was_active = False
        return self._make_packet(
            flags1=0, flags2=0, rotation_v=0, elevation_v=0,
            rotation_p=0, elevation_p=0, arm=_ARM_OFF, fire=_FIRE_OFF, fire_duration=0,
        )

    def _build_packet(self, intent: _Intent, now: float) -> rws_control.CommandPacket:
        s = self._s
        speed_scale = s.speed_percent / 100.0

        # --- Motion: always available, independent of the safety toggle. ---
        rotation_direction = int(intent.right) - int(intent.left)
        elevation_direction = int(intent.up) - int(intent.down)

        rotation_v = rws_control.encode_unit_axis_to_packet_s16(
            rotation_direction * s.rotation_v_unit * speed_scale
        )
        if elevation_direction > 0:
            elevation_v = rws_control.encode_unit_axis_to_packet_s16(s.elevation_v_up_unit * speed_scale)
        elif elevation_direction < 0:
            elevation_v = rws_control.encode_unit_axis_to_packet_s16(-s.elevation_v_down_unit * speed_scale)
        else:
            elevation_v = 0

        rotation_p = self._position_target(rotation_direction)
        elevation_p = self._position_target(elevation_direction)

        # ENABLE stays on for the whole live session so the motors HOLD position
        # (a released axis must not sag/spring back). It drops only on the deadman
        # neutral packet. Fire, not motion, is what the safety gates.
        flags1 = rws_control.FLAGS1_ENABLE
        flags2 = rws_control.FLAGS2_ROTATION_V | rws_control.FLAGS2_ELEVATION_V | rws_control.FLAGS2_VEL_PRIO
        if rotation_direction != 0:
            flags2 |= rws_control.FLAGS2_ROTATION_P
        if elevation_direction != 0:
            flags2 |= rws_control.FLAGS2_ELEVATION_P

        # --- Firing: gated by the safety toggle only. ---
        fire_active = intent.safety_off and intent.fire_held
        if fire_active and not self._fire_was_active:
            self._fire_seq = (self._fire_seq + 1) & 0xFF  # edge-triggered shot count
        self._fire_was_active = fire_active

        arm = _ARM_ON if intent.safety_off else _ARM_OFF
        if fire_active:
            fire = _FIRE_ON
            fire_duration = self._fire_duration()
        else:
            fire = _FIRE_OFF
            fire_duration = 0

        return self._make_packet(
            flags1=flags1, flags2=flags2, rotation_v=rotation_v, elevation_v=elevation_v,
            rotation_p=rotation_p, elevation_p=elevation_p, arm=arm, fire=fire,
            fire_duration=fire_duration,
        )

    @staticmethod
    def _position_target(direction: int) -> int:
        if direction > 0:
            return rws_control.encode_angle_rad_to_packet_s32(_POSITIVE_TARGET_RAD)
        if direction < 0:
            return rws_control.encode_angle_rad_to_packet_s32(_NEGATIVE_TARGET_RAD)
        return 0

    def _fire_duration(self) -> int:
        mode = self._fire_mode
        if mode == "medium":
            return self._s.fire_duration_medium
        if mode == "manual":
            return 0
        return self._s.fire_duration_short

    def _make_packet(
        self,
        *,
        flags1: int,
        flags2: int,
        rotation_v: int,
        elevation_v: int,
        rotation_p: int,
        elevation_p: int,
        arm: bytes,
        fire: bytes,
        fire_duration: int,
    ) -> rws_control.CommandPacket:
        packet = rws_control.build_generated_command_packet(
            name="cockpit",
            sequence=self._next_sequence,
            flags1=flags1,
            flags2=flags2,
            flags3=0,
            flags4=0,
            rotation_v=rotation_v,
            elevation_v=elevation_v,
            rotation_p=rotation_p,
            elevation_p=elevation_p,
            arm=arm,
            fire=fire,
            fire_duration=fire_duration,
            cameras_p=0,
            rangefinder_seq=0,
            fire_seq=self._fire_seq,
            salt=self._s.salt,
        )
        self._next_sequence = (self._next_sequence + 1) & 0xFFFF
        return packet

    # -------------------------------------------------------------------- status
    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            intent = self._intent
            fire_mode = self._fire_mode
            input_age_ms = int((now - self._last_input_monotonic) * 1000)
            deadman_active = now - self._last_input_monotonic > self._s.deadman_seconds
        return {
            "dry_run": self._s.dry_run,
            "safety_off": intent.safety_off,
            "fire_held": intent.fire_held,
            "fire_mode": fire_mode,
            "axes": {"up": intent.up, "down": intent.down, "left": intent.left, "right": intent.right},
            "packets_sent": self._packets_sent,
            "replies_received": self._replies_received,
            "sequence": self._next_sequence,
            "fire_seq": self._fire_seq,
            "input_age_ms": input_age_ms,
            "deadman_active": deadman_active,
            "send_rate_hz": self._s.send_rate_hz,
            "link": self._link_state(now),
            "bind_error": self._bind_error,
        }
