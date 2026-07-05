"""
Binary message encode/decode for the external control channel (frontend ↔ backend ↔ rws_bridge).

control_state  — 12 bytes, message_type=1
presence       — 6 bytes,  message_type=2
observed_state — 24 bytes, message_type=3
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Union

VERSION = 1
MSG_CONTROL_STATE = 1
MSG_PRESENCE = 2
MSG_OBSERVED_STATE = 3

CONTROL_STATE_LEN = 12
PRESENCE_LEN = 6
OBSERVED_STATE_LEN = 24

# state_flags bits (control_state offset 6)
FLAG_ENABLE = 0x01
FLAG_SLOW   = 0x02
FLAG_RELOAD = 0x04
FLAG_ARM    = 0x08
FLAG_FIRE   = 0x10

# aux_flags bits (control_state offset 7)
AUX_FORCE_HOME = 0x01
AUX_CENTER     = 0x02
AUX_FIRE_MODE  = 0xC0  # bits 6-7: 0=short, 1=medium, 2=manual

FIRE_MODE_SHORT  = 0
FIRE_MODE_MEDIUM = 1
FIRE_MODE_MANUAL = 2

# observed_state state_flags bits (observed_state offset 6)
OBS_ROT_P_VALID  = 0x01
OBS_ELE_P_VALID  = 0x02
OBS_RWS_ACTIVE   = 0x04
OBS_FIRE_PULSE   = 0x08
OBS_SAFE_MODE    = 0x10
OBS_LINK_MASK    = 0x60
OBS_LINK_OFFLINE = 0x00
OBS_LINK_STALE   = 0x20
OBS_LINK_ONLINE  = 0x40


@dataclass
class ControlState:
    seq: int
    state_flags: int
    aux_flags: int
    axis_x: int   # int16, already scaled by speed_percent client-side
    axis_y: int   # int16

    @property
    def enable(self) -> bool:
        return bool(self.state_flags & FLAG_ENABLE)

    @property
    def slow(self) -> bool:
        return bool(self.state_flags & FLAG_SLOW)

    @property
    def reload(self) -> bool:
        return bool(self.state_flags & FLAG_RELOAD)

    @property
    def arm(self) -> bool:
        return bool(self.state_flags & FLAG_ARM)

    @property
    def fire(self) -> bool:
        return bool(self.state_flags & FLAG_FIRE)

    @property
    def force_home_pulse(self) -> bool:
        return bool(self.aux_flags & AUX_FORCE_HOME)

    @property
    def fire_mode(self) -> int:
        return (self.aux_flags & AUX_FIRE_MODE) >> 6


@dataclass
class Presence:
    seq: int


def parse_datagram(data: bytes) -> Optional[Union[ControlState, Presence]]:
    if len(data) < 2 or data[0] != VERSION:
        return None
    msg_type = data[1]
    if msg_type == MSG_CONTROL_STATE:
        if len(data) != CONTROL_STATE_LEN:
            return None
        seq, sf, af, ax, ay = struct.unpack_from(">IBBhh", data, 2)
        return ControlState(seq=seq, state_flags=sf, aux_flags=af, axis_x=ax, axis_y=ay)
    if msg_type == MSG_PRESENCE:
        if len(data) != PRESENCE_LEN:
            return None
        (seq,) = struct.unpack_from(">I", data, 2)
        return Presence(seq=seq)
    return None


def encode_observed_state(
    seq: int,
    state_flags: int,
    rotation_p: int,
    elevation_p: int,
    distance_mm: int,
    shots: int,
    x_status_flags: int,
    y_status_flags: int,
) -> bytes:
    # ">BBIBBiiIHBB" = 1+1+4+1+1+4+4+4+2+1+1 = 24 bytes
    return struct.pack(
        ">BBIBBiiIHBB",
        VERSION,
        MSG_OBSERVED_STATE,
        seq,
        state_flags,
        0,              # reserved0
        rotation_p,
        elevation_p,
        distance_mm,
        shots,
        x_status_flags,
        y_status_flags,
    )
