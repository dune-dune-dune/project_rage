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

# Legacy full-scale position targets, used only as a fallback until the turret
# has reported its current angle (see _axis_position).
_POSITIVE_TARGET_RAD = math.pi
_NEGATIVE_TARGET_RAD = -math.pi

# Modest directional lead (radians) added to the *current* angle to form the
# position target while an axis is moving. The reference controller keeps the
# position-valid bits set continuously and commands a target a small step ahead
# of the current angle (never a full +/-pi jump) — that is what avoids the
# one-time kick when the P-valid bit rises and the target jumps to +/-pi. The
# lead is refreshed from telemetry each tick, so the turret chases a carrot that
# stays this far ahead while VEL_PRIO drives the actual speed.
_POSITION_LEAD_RAD = math.pi / 2

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


def _clamp_unit(value: object) -> float:
    """Clamp an analog aim velocity to the normalised [-1.0, 1.0] range."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, number))


def _approach(current: float, target: float, step: float) -> float:
    """Move ``current`` toward ``target`` by at most ``step`` (slew-rate limit)."""
    if step <= 0.0 or abs(target - current) <= step:
        return target
    return current + step if target > current else current - step


class TurretController:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.Lock()
        self._intent = _Intent()
        self._fire_mode = settings.fire_mode if settings.fire_mode in _FIRE_MODES else "short"
        # Rotation-speed level as a 0-based index into settings.speed_levels.
        # Defaults to the last (fastest) level so behaviour is unchanged until the
        # operator picks a slower level with keys 1..N.
        self._speed_index = len(settings.speed_levels) - 1
        self._last_input_monotonic = 0.0

        # --- Auto-track (visual servo) aim override, set from POST /api/track. ---
        # When active and fresh, these normalised velocities replace the manual
        # WASD-derived rotation/elevation velocities. They NEVER touch arm/fire —
        # auto-track only aims; firing stays fully manual.
        self._aim_active = False
        self._aim_rot = 0.0
        self._aim_ele = 0.0
        self._last_aim_monotonic = 0.0

        # Ramped (soft-start) commanded velocities in normalised units, slewed
        # toward the manual target each tick to avoid a 0->full step — that step
        # is what makes the turret jerk once on movement start. Owned by the
        # sender thread. Auto-track bypasses the ramp (see _build_packet).
        self._cmd_rot_v = 0.0
        self._cmd_ele_v = 0.0

        # Latest turret-reported angles (raw int32, +/-pi scale), parsed from the
        # 32-byte status replies. None until the first valid reply. Used as the
        # idle position target so the P-valid bits can stay on continuously
        # (matching the reference) without the turret drifting.
        self._cur_rotation_p: int | None = None
        self._cur_elevation_p: int | None = None

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
            level = payload.get("speed_level")
            if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= len(self._s.speed_levels):
                self._speed_index = level - 1
            self._last_input_monotonic = time.monotonic()

    def apply_track(self, payload: dict) -> None:
        """Set the auto-track aim override from a browser POST.

        ``payload`` is ``{active: bool, rot: float, ele: float}`` where rot/ele
        are normalised velocities in [-1, 1] produced by the client-side visual
        servo (target pixel error -> velocity). Refreshes the deadman so the
        turret stays alive while tracking with no manual keys held.
        """
        active = bool(payload.get("active", False))
        rot = _clamp_unit(payload.get("rot", 0.0)) if active else 0.0
        ele = _clamp_unit(payload.get("ele", 0.0)) if active else 0.0
        with self._lock:
            self._aim_active = active
            self._aim_rot = rot
            self._aim_ele = ele
            now = time.monotonic()
            self._last_aim_monotonic = now
            self._last_input_monotonic = now  # keep the deadman fed while tracking

    def _read_intent(self, now: float) -> _Intent | None:
        """Return a snapshot of intent, or None if the deadman has expired."""
        with self._lock:
            if now - self._last_input_monotonic > self._s.deadman_seconds:
                return None  # fail-safe: no fresh input -> fully neutral packet
            # Shallow copy so the sender thread reads a stable snapshot.
            return _Intent(**vars(self._intent))

    def _read_aim(self, now: float) -> tuple[bool, float, float]:
        """Return (active, rot_v, ele_v) for auto-track, or inert if stale."""
        with self._lock:
            if not self._aim_active or now - self._last_aim_monotonic > self._s.aim_timeout_seconds:
                return (False, 0.0, 0.0)
            return (True, self._aim_rot, self._aim_ele)

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
            if intent is None:
                packet = self._neutral_packet()
            else:
                packet = self._build_packet(intent, self._read_aim(now), now)
            try:
                if self._channel is not None:
                    self._channel.send_command(packet)
                    for event in self._channel.poll_events():
                        if event.kind == "reply":
                            self._replies_received += 1
                            self._last_reply_monotonic = now
                            if event.data is not None:
                                self._update_angles_from_reply(event.data)
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
        # Reset the ramp so control resumes with a fresh soft-start from zero.
        self._cmd_rot_v = 0.0
        self._cmd_ele_v = 0.0
        return self._make_packet(
            flags1=0, flags2=0, rotation_v=0, elevation_v=0,
            rotation_p=0, elevation_p=0, arm=_ARM_OFF, fire=_FIRE_OFF, fire_duration=0,
        )

    def _build_packet(
        self, intent: _Intent, aim: tuple[bool, float, float], now: float
    ) -> rws_control.CommandPacket:
        s = self._s
        speed_scale = s.speed_percent / 100.0
        # Selected rotation-speed level (keys 1..N) scales manual motion only.
        level_scale = s.speed_levels[self._speed_index] / 100.0
        aim_active, aim_rot, aim_ele = aim

        # ENABLE stays on for the whole live session so the motors HOLD position
        # (a released axis must not sag/spring back). It drops only on the deadman
        # neutral packet. Fire, not motion, is what the safety gates.
        flags1 = rws_control.FLAGS1_ENABLE
        flags2 = rws_control.FLAGS2_ROTATION_V | rws_control.FLAGS2_ELEVATION_V | rws_control.FLAGS2_VEL_PRIO

        if aim_active:
            # --- Auto-track: analog visual-servo velocities override manual WASD.
            # Use the EXACT same packet recipe as a held manual key (velocity +
            # a full-scale +/-pi position target + the *_P valid bits), only with
            # a proportional velocity magnitude. Manual motion demonstrably moves
            # the turret this way, so auto-track must command it identically — a
            # velocity-only packet (no position target / no P bits) did not move it.
            rotation_direction = 1 if aim_rot > 0 else (-1 if aim_rot < 0 else 0)
            elevation_direction = 1 if aim_ele > 0 else (-1 if aim_ele < 0 else 0)
            # Auto-track velocities come from a closed-loop visual servo and are
            # already smooth, so they bypass the ramp. Keep the ramp state in sync
            # with what we actually command so a later aim->manual handoff resumes
            # from the current velocity instead of stepping.
            self._cmd_rot_v = aim_rot * speed_scale
            self._cmd_ele_v = aim_ele * speed_scale
            rotation_v = rws_control.encode_unit_axis_to_packet_s16(self._cmd_rot_v)
            elevation_v = rws_control.encode_unit_axis_to_packet_s16(self._cmd_ele_v)
            rotation_p, rot_p_valid = self._axis_position(rotation_direction, self._cur_rotation_p)
            elevation_p, ele_p_valid = self._axis_position(elevation_direction, self._cur_elevation_p)
            if rot_p_valid:
                flags2 |= rws_control.FLAGS2_ROTATION_P
            if ele_p_valid:
                flags2 |= rws_control.FLAGS2_ELEVATION_P
        else:
            # --- Manual motion: always available, independent of the safety toggle.
            rotation_direction = int(intent.right) - int(intent.left)
            elevation_direction = int(intent.up) - int(intent.down)

            # Target normalised velocities (pre-encode). The ramp slews the actual
            # commanded velocity toward these each tick so movement starts smoothly
            # instead of stepping 0->full (the cause of the one-time start jerk).
            target_rot_v = rotation_direction * s.rotation_v_unit * speed_scale * level_scale
            if elevation_direction > 0:
                target_ele_v = s.elevation_v_up_unit * speed_scale * level_scale
            elif elevation_direction < 0:
                target_ele_v = -s.elevation_v_down_unit * speed_scale * level_scale
            else:
                target_ele_v = 0.0

            step = s.accel_per_tick
            self._cmd_rot_v = _approach(self._cmd_rot_v, target_rot_v, step)
            self._cmd_ele_v = _approach(self._cmd_ele_v, target_ele_v, step)
            rotation_v = rws_control.encode_unit_axis_to_packet_s16(self._cmd_rot_v)
            elevation_v = rws_control.encode_unit_axis_to_packet_s16(self._cmd_ele_v)

            rotation_p, rot_p_valid = self._axis_position(rotation_direction, self._cur_rotation_p)
            elevation_p, ele_p_valid = self._axis_position(elevation_direction, self._cur_elevation_p)
            if rot_p_valid:
                flags2 |= rws_control.FLAGS2_ROTATION_P
            if ele_p_valid:
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

    def _axis_position(self, direction: int, current: int | None) -> tuple[int, bool]:
        """Return (position_target_int32, p_valid) for one axis.

        Reference-faithful: keep the position-valid bit **on continuously** and
        command the *current* angle when idle (so the turret holds without the
        P-valid bit toggling), leading it by ``_POSITION_LEAD_RAD`` in the travel
        direction while moving (a modest step, never a 0->+/-pi jump). This removes
        the P-valid rising edge + far-target jump that kicks the turret once on
        movement start. Until the turret reports an angle, fall back to the old
        scheme (P valid only while moving, +/-pi target).
        """
        if current is None:
            if direction == 0:
                return 0, False
            return self._position_target(direction), True
        current_rad = rws_control.decode_packet_angle_s32_to_rad(current)
        target_rad = current_rad + direction * _POSITION_LEAD_RAD
        target_rad = max(-math.pi, min(math.pi, target_rad))
        return rws_control.encode_angle_rad_to_packet_s32(target_rad), True

    def _update_angles_from_reply(self, data: bytes) -> None:
        """Cache the turret's current pan/tilt angles from a 32-byte status reply.

        Only the status reply carries angles; telemetry (36 B) is ignored here.
        Each axis updates only when its validity bit is set. Not checksum-verified
        (consistent with the rest of the reply handling — see the known gaps)."""
        if len(data) != rws_control.RWS_STATUS_PAYLOAD_LEN:
            return
        try:
            reply = rws_control.RwsReplyWire.from_bytes(data)
        except ValueError:
            return
        if reply.flags1 & rws_control.RWS_STATUS_FLAGS1_ROTATION_P_VALID:
            self._cur_rotation_p = int(reply.rotation_p)
        if reply.flags1 & rws_control.RWS_STATUS_FLAGS1_ELEVATION_P_VALID:
            self._cur_elevation_p = int(reply.elevation_p)

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
            speed_index = self._speed_index
            input_age_ms = int((now - self._last_input_monotonic) * 1000)
            deadman_active = now - self._last_input_monotonic > self._s.deadman_seconds
            track_active = self._aim_active and now - self._last_aim_monotonic <= self._s.aim_timeout_seconds
        return {
            "dry_run": self._s.dry_run,
            "safety_off": intent.safety_off,
            "fire_held": intent.fire_held,
            "fire_mode": fire_mode,
            "speed_level": speed_index + 1,
            "speed_levels": len(self._s.speed_levels),
            "track_active": track_active,
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
            # Turret-reported angles (deg), or None until a valid status reply
            # arrives. Also signals whether the position-hold telemetry is live.
            "angle_rot_deg": self._angle_deg(self._cur_rotation_p),
            "angle_ele_deg": self._angle_deg(self._cur_elevation_p),
        }

    @staticmethod
    def _angle_deg(raw: int | None) -> float | None:
        if raw is None:
            return None
        return round(math.degrees(rws_control.decode_packet_angle_s32_to_rad(raw)), 1)

    def speed_config(self) -> dict:
        """Rotation-speed levels for the client HUD: percents + current level."""
        with self._lock:
            current = self._speed_index + 1
        return {"levels": list(self._s.speed_levels), "current": current}
