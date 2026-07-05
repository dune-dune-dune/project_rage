"""
Core rws_bridge logic: ownership, fire tracking, RWS command building, observed_state.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from config import BridgeConfig
from protocol import (
    OBS_ELE_P_VALID, OBS_FIRE_PULSE, OBS_LINK_MASK, OBS_LINK_OFFLINE,
    OBS_LINK_ONLINE, OBS_LINK_STALE, OBS_ROT_P_VALID, OBS_RWS_ACTIVE,
    OBS_SAFE_MODE, ControlState, Presence, encode_observed_state,
    FIRE_MODE_MEDIUM, FIRE_MODE_SHORT,
)
from rws import (
    FLAGS1_ENABLE, FLAGS1_FORCE_HOME, FLAGS1_RELOAD, FLAGS1_SLOW,
    FLAGS2_ELEVATION_V, FLAGS2_ROTATION_V, FLAGS2_VEL_PRIO,
    FIRE_DURATION_MEDIUM, FIRE_DURATION_SHORT,
    RWS_STATUS_FLAGS1_ELEVATION_P_VALID, RWS_STATUS_FLAGS1_ROTATION_P_VALID,
    SEQUENCE_MODULUS, TELEM_FLAGS0_FIRE_PULSE, TELEM_FLAGS0_RWS_ACTIVE,
    RwsReplyWire, RwsTelemetryWire, build_rws_packet,
)

if TYPE_CHECKING:
    from server import ControllerSession

logger = logging.getLogger(__name__)


# ── Ownership ──────────────────────────────────────────────────────────────────

@dataclass
class ControllerIdentity:
    session_id: str
    controller_kind: str   # "web_human" | "ai_node"
    instance_id: str
    principal_id: str
    principal_name: str


class OwnershipManager:
    def __init__(self, lease_timeout_ms: int) -> None:
        self._lease_s = lease_timeout_ms / 1000.0
        self._owner_sid: Optional[str] = None
        self._identities: dict[str, ControllerIdentity] = {}
        self._last_seen: dict[str, float] = {}
        self._last_seq: dict[str, int] = {}

    def register(self, identity: ControllerIdentity) -> None:
        self._identities[identity.session_id] = identity
        self._last_seen[identity.session_id] = time.monotonic()
        self._last_seq[identity.session_id] = -1

    def unregister(self, session_id: str) -> bool:
        """Returns True if the owner was just removed."""
        was_owner = self._owner_sid == session_id
        for d in (self._identities, self._last_seen, self._last_seq):
            d.pop(session_id, None)
        if was_owner:
            self._owner_sid = None
        return was_owner

    def take_control(self, session_id: str) -> tuple[bool, str]:
        if session_id not in self._identities:
            return False, "not_registered"
        if self._owner_sid == session_id:
            return True, "already_owner"
        if self._owner_sid is not None:
            return False, "occupied"
        self._owner_sid = session_id
        return True, "granted"

    def release_control(self, session_id: str) -> bool:
        if self._owner_sid != session_id:
            return False
        self._owner_sid = None
        return True

    def update_seen(self, session_id: str, seq: int) -> bool:
        """Returns False and drops the message if seq is stale (≤ last accepted)."""
        if session_id not in self._last_seq:
            return False
        last = self._last_seq[session_id]
        if last >= 0 and seq <= last:
            return False
        self._last_seq[session_id] = seq
        self._last_seen[session_id] = time.monotonic()
        return True

    def check_lease(self, now: float) -> Optional[str]:
        """Returns the session_id that was revoked, or None."""
        if self._owner_sid is None:
            return None
        last = self._last_seen.get(self._owner_sid)
        if last is None:
            return None
        if now - last > self._lease_s:
            sid = self._owner_sid
            self._owner_sid = None
            return sid
        return None

    @property
    def owner_sid(self) -> Optional[str]:
        return self._owner_sid

    def owner_identity(self) -> Optional[ControllerIdentity]:
        if self._owner_sid is None:
            return None
        return self._identities.get(self._owner_sid)

    def ownership_snapshot(self, for_session_id: str) -> dict:
        owner = self.owner_identity()
        return {
            "owner_kind": owner.controller_kind if owner else None,
            "owner_display_name": owner.principal_name if owner else None,
            "you_are_owner": self._owner_sid == for_session_id,
        }


# ── Fire tracking ──────────────────────────────────────────────────────────────

_FIRE_DURATIONS = {
    FIRE_MODE_SHORT: FIRE_DURATION_SHORT,
    FIRE_MODE_MEDIUM: FIRE_DURATION_MEDIUM,
}


class FireTracker:
    def __init__(self) -> None:
        self.fire_seq: int = 0
        self._prev_fire: bool = False
        self._force_home_until: float = 0.0

    def update(self, fire: bool, fire_mode: int, now: float) -> tuple[bytes, int]:
        if fire and not self._prev_fire:
            self.fire_seq = (self.fire_seq + 1) & 0xFF
        self._prev_fire = fire
        if fire:
            duration = _FIRE_DURATIONS.get(fire_mode, 0)
            return b"F\x00", duration
        return b"\x00\x00", 0

    def arm_force_home(self, now: float) -> None:
        # Hold FLAGS1_FORCE_HOME for ~150 ms (3 × 50 ms cycles)
        self._force_home_until = now + 0.15

    def force_home_active(self, now: float) -> bool:
        return now < self._force_home_until


# ── RWS observation snapshot ───────────────────────────────────────────────────

class RwsObservation:
    def __init__(self, stale_timeout_ms: int) -> None:
        self._stale_s = stale_timeout_ms / 1000.0
        self.rotation_p: int = 0
        self.elevation_p: int = 0
        self.rot_valid: bool = False
        self.ele_valid: bool = False
        self.distance_mm: int = 0
        self.shots: int = 0
        self.x_flags: int = 0
        self.y_flags: int = 0
        self.rws_active: bool = False
        self.fire_pulse: bool = False
        self._last_status_ts: Optional[float] = None
        self._last_telem_ts: Optional[float] = None

    def update_status(self, reply: RwsReplyWire, now: float) -> None:
        self.rotation_p = reply.rotation_p
        self.elevation_p = reply.elevation_p
        self.rot_valid = bool(reply.flags1 & RWS_STATUS_FLAGS1_ROTATION_P_VALID)
        self.ele_valid = bool(reply.flags1 & RWS_STATUS_FLAGS1_ELEVATION_P_VALID)
        self.distance_mm = reply.distance_mm
        self.shots = reply.shots
        self._last_status_ts = now

    def update_telemetry(self, telem: RwsTelemetryWire, now: float) -> None:
        self.rws_active = bool(telem.flags0 & TELEM_FLAGS0_RWS_ACTIVE)
        self.fire_pulse = bool(telem.flags0 & TELEM_FLAGS0_FIRE_PULSE)
        self.x_flags = telem.flags2
        self.y_flags = telem.flags3
        self._last_telem_ts = now

    def link_flags(self, now: float) -> int:
        ts = self._last_status_ts or self._last_telem_ts
        if ts is None:
            return OBS_LINK_OFFLINE
        return OBS_LINK_ONLINE if (now - ts) <= self._stale_s else OBS_LINK_STALE


# ── Bridge ─────────────────────────────────────────────────────────────────────

class Bridge:
    def __init__(self, cfg: BridgeConfig) -> None:
        self._cfg = cfg
        self.ownership = OwnershipManager(cfg.lease_timeout_ms)
        self.obs = RwsObservation(cfg.stale_timeout_ms)
        self._fire = FireTracker()
        self._latest_ctrl: Optional[ControlState] = None
        self.safe_mode: bool = True    # starts safe; cleared when owner takes control
        self._rws_seq: int = 0
        self._obs_seq: int = 0
        self._sessions: dict[str, "ControllerSession"] = {}

    # ── Session registration ────────────────────────────────────────────────

    def add_session(self, session: "ControllerSession") -> None:
        self._sessions[session.session_id] = session

    def remove_session(self, session_id: str) -> bool:
        self._sessions.pop(session_id, None)
        was_owner = self.ownership.unregister(session_id)
        if was_owner:
            self._latest_ctrl = None
            self.safe_mode = True
            logger.info("Owner session %s disconnected → safe mode", session_id)
        return was_owner

    # ── Incoming hot-path messages ──────────────────────────────────────────

    def on_control_state(self, session_id: str, cs: ControlState) -> bool:
        if not self.ownership.update_seen(session_id, cs.seq):
            return False
        if self.ownership.owner_sid != session_id:
            return False
        self._latest_ctrl = cs
        if cs.force_home_pulse:
            self._fire.arm_force_home(time.monotonic())
        return True

    def on_presence(self, session_id: str, p: Presence) -> bool:
        return self.ownership.update_seen(session_id, p.seq)

    # ── Control requests ────────────────────────────────────────────────────

    def take_control(self, session_id: str) -> tuple[bool, str]:
        ok, reason = self.ownership.take_control(session_id)
        if ok:
            self.safe_mode = False
            logger.info("Session %s took control", session_id)
        return ok, reason

    def release_control(self, session_id: str) -> bool:
        released = self.ownership.release_control(session_id)
        if released:
            self._latest_ctrl = None
            self.safe_mode = True
            logger.info("Session %s released control → safe mode", session_id)
        return released

    # ── Watchdog ────────────────────────────────────────────────────────────

    def check_lease(self) -> Optional[str]:
        """Call periodically. Returns revoked session_id or None."""
        revoked = self.ownership.check_lease(time.monotonic())
        if revoked:
            self._latest_ctrl = None
            self.safe_mode = True
            logger.warning("Lease timeout for session %s → safe mode", revoked)
        return revoked

    # ── RWS command building ────────────────────────────────────────────────

    def next_rws_command(self) -> bytes:
        now = time.monotonic()
        seq = self._rws_seq
        self._rws_seq = (self._rws_seq + 1) % SEQUENCE_MODULUS

        ctrl = self._latest_ctrl
        always_flags2 = FLAGS2_ROTATION_V | FLAGS2_ELEVATION_V | FLAGS2_VEL_PRIO

        if self.safe_mode or ctrl is None or not ctrl.enable:
            fire_b, fire_d = self._fire.update(False, 0, now)
            return build_rws_packet(
                salt=self._cfg.salt,
                sequence=seq,
                flags1=0,
                flags2=always_flags2,
                rotation_v=0,
                elevation_v=0,
                arm=b"\x00\x00\x00\x00",
                fire=fire_b,
                fire_duration=fire_d,
                fire_seq=self._fire.fire_seq,
            )

        flags1 = FLAGS1_ENABLE
        if ctrl.slow:   flags1 |= FLAGS1_SLOW
        if ctrl.reload: flags1 |= FLAGS1_RELOAD
        if self._fire.force_home_active(now): flags1 |= FLAGS1_FORCE_HOME

        fire_b, fire_d = self._fire.update(ctrl.fire, ctrl.fire_mode, now)

        return build_rws_packet(
            salt=self._cfg.salt,
            sequence=seq,
            flags1=flags1,
            flags2=always_flags2,
            rotation_v=ctrl.axis_x,
            elevation_v=ctrl.axis_y,
            arm=b"A\x00\x00\x00" if ctrl.arm else b"\x00\x00\x00\x00",
            fire=fire_b,
            fire_duration=fire_d,
            fire_seq=self._fire.fire_seq,
        )

    # ── Observed state building ─────────────────────────────────────────────

    def next_observed_state(self) -> bytes:
        now = time.monotonic()
        seq = self._obs_seq
        self._obs_seq = (self._obs_seq + 1) & 0xFFFFFFFF

        sf = 0
        if self.obs.rot_valid:   sf |= OBS_ROT_P_VALID
        if self.obs.ele_valid:   sf |= OBS_ELE_P_VALID
        if self.obs.rws_active:  sf |= OBS_RWS_ACTIVE
        if self.obs.fire_pulse:  sf |= OBS_FIRE_PULSE
        if self.safe_mode:       sf |= OBS_SAFE_MODE
        sf |= self.obs.link_flags(now)

        return encode_observed_state(
            seq=seq,
            state_flags=sf,
            rotation_p=self.obs.rotation_p,
            elevation_p=self.obs.elevation_p,
            distance_mm=self.obs.distance_mm,
            shots=self.obs.shots,
            x_status_flags=self.obs.x_flags,
            y_status_flags=self.obs.y_flags,
        )

    async def broadcast_observed_state(self) -> None:
        data = self.next_observed_state()
        for session in list(self._sessions.values()):
            try:
                await session.send_bytes(data)
            except Exception:
                pass
