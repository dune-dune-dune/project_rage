#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import os
import select
import shutil
import socket
import subprocess
import sys
import termios
import textwrap
import time
import tty
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from rws_control import (
    DEFAULT_DST_IP,
    DEFAULT_DST_PORT,
    DEFAULT_SRC_IP,
    DEFAULT_SRC_PORT,
    DEFAULT_TIMEOUT_MS,
    FLAGS1_ENABLE,
    FLAGS1_FORCE_HOME,
    FLAGS1_RELOAD,
    FLAGS1_SLOW,
    FLAGS2_ELEVATION_P,
    FLAGS2_ELEVATION_V,
    FLAGS2_ROTATION_P,
    FLAGS2_ROTATION_V,
    FLAGS2_VEL_PRIO,
    PERIOD_MS,
    RWS_STATUS_FLAGS1_ELEVATION_P_VALID,
    RWS_STATUS_FLAGS1_ROTATION_P_VALID,
    RWS_STATUS_PAYLOAD_LEN,
    RWS_TELEMETRY_PAYLOAD_LEN,
    CommandPacket,
    RwsControlChannel,
    RwsReplyTracker,
    RwsReplyWire,
    RwsTelemetryWire,
    RwsTransportEvent,
    build_generated_command_packet,
    decode_packet_angle_s32_to_rad,
    describe_fire_mode,
    encode_angle_rad_to_packet_s32,
    encode_unit_axis_to_packet_s16,
    format_angle_degrees,
    format_angle_packet_s32,
    format_unit_percent,
    wrap_prefixed,
)

DEFAULT_EMBEDDED_SALT = bytes.fromhex("262bd7b673f1371fd274f96f2e819032498f304b4021d3fc87d5db723f8fa277")
DEFAULT_KEYBOARD_SEQUENCE_START = 0x0000
DEFAULT_AXIS_HOLD_MS = 500.0
DEFAULT_UI_RENDER_MS = 100.0
REFERENCE_MAX_AXIS_UNIT = 1.0
REFERENCE_POSITIVE_TARGET_RAD = math.pi
REFERENCE_NEGATIVE_TARGET_RAD = -math.pi
DEFAULT_ROTATION_V_UNIT = REFERENCE_MAX_AXIS_UNIT
DEFAULT_ELEVATION_V_UP_UNIT = REFERENCE_MAX_AXIS_UNIT
DEFAULT_ELEVATION_V_DOWN_UNIT = REFERENCE_MAX_AXIS_UNIT
DEFAULT_SPEED_PERCENT = 100
MIN_SPEED_PERCENT = 10
MAX_SPEED_PERCENT = 100
SPEED_STEP_PERCENT = 10
DEFAULT_FIRE_DURATION_SHORT = 161
DEFAULT_FIRE_DURATION_MEDIUM = 605
ANSI_ALT_SCREEN_ON = "\x1b[?1049h"
ANSI_ALT_SCREEN_OFF = "\x1b[?1049l"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"
ANSI_CURSOR_HOME = "\x1b[H"
FIRE_MODE_SHORT = "short"
FIRE_MODE_MEDIUM = "medium"
FIRE_MODE_MANUAL = "manual"
FIRE_MODE_DURATIONS = {
    FIRE_MODE_SHORT: DEFAULT_FIRE_DURATION_SHORT,
    FIRE_MODE_MEDIUM: DEFAULT_FIRE_DURATION_MEDIUM,
    FIRE_MODE_MANUAL: 0,
}
FORCE_HOME_PULSE_SECONDS = 1.0
REMOVED_COMMANDS = {"list", "show", "send"}
LEGACY_LIVE_ALIASES = {"live", "keyboard"}
RX_KIND_RWS_STATUS = "rws-status"
RX_KIND_RWS_TELEMETRY = "rws-telemetry"
RX_KIND_ORDER = (
    RX_KIND_RWS_STATUS,
    RX_KIND_RWS_TELEMETRY,
)
RX_KIND_LABELS = {
    RX_KIND_RWS_STATUS: "rws status",
    RX_KIND_RWS_TELEMETRY: "rws teleme",
}


@dataclass(frozen=True)
class KeyboardControlConfig:
    interval_seconds: float
    axis_hold_seconds: float
    # These are normalized command amplitudes in the range 0..1.
    # They are encoded into the packet right before transmission.
    rotation_v_unit: float
    elevation_v_up_unit: float
    elevation_v_down_unit: float


class KeyboardInputReader:
    def __init__(self) -> None:
        try:
            self._fd = sys.stdin.fileno()
        except (AttributeError, OSError):
            self._fd = None
        self._is_tty = self._fd is not None and sys.stdin.isatty()
        self._saved_termios: list[int] | None = None
        self._buffer = bytearray()
        self._eof = False

    @property
    def is_tty(self) -> bool:
        return self._is_tty

    @property
    def eof(self) -> bool:
        return self._eof

    def fileno(self) -> int | None:
        return self._fd

    def __enter__(self) -> "KeyboardInputReader":
        if self._is_tty and self._fd is not None:
            self._saved_termios = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._saved_termios is not None and self._fd is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_termios)

    def read_events(self) -> list[str]:
        if self._fd is None or self._eof:
            return []

        try:
            chunk = os.read(self._fd, 128)
        except BlockingIOError:
            return []

        if not chunk:
            self._eof = True
            return []

        self._buffer.extend(chunk)
        return self._drain_events()

    def _pop_text_char(self) -> str | None:
        first = self._buffer[0]
        if first < 0x80:
            del self._buffer[0]
            return chr(first)

        if 0xC2 <= first <= 0xDF:
            size = 2
        elif 0xE0 <= first <= 0xEF:
            size = 3
        elif 0xF0 <= first <= 0xF4:
            size = 4
        else:
            del self._buffer[0]
            return ""

        if len(self._buffer) < size:
            return None

        raw = bytes(self._buffer[:size])
        try:
            char = raw.decode("utf-8")
        except UnicodeDecodeError:
            del self._buffer[0]
            return ""

        del self._buffer[:size]
        return char

    def _drain_events(self) -> list[str]:
        events: list[str] = []
        while self._buffer:
            if self._buffer[0] != 0x1B:
                char = self._pop_text_char()
                if char is None:
                    break
                if not char:
                    continue
                event = self._map_char(char)
                if event is not None:
                    events.append(event)
                continue

            if len(self._buffer) < 2:
                break
            if self._buffer[0:2] != b"\x1b[":
                del self._buffer[0]
                continue
            if len(self._buffer) < 3:
                break

            code = chr(self._buffer[2])
            del self._buffer[:3]
            event = {
                "A": "up",
                "B": "down",
                "C": "right",
                "D": "left",
            }.get(code)
            if event is not None:
                events.append(event)
        return events

    @staticmethod
    def _map_char(char: str) -> str | None:
        if char in {"q", "Q", "й", "Й", "\x03"}:
            return "quit"
        if char in {"w", "W", "ц", "Ц"}:
            return "up_latch"
        if char in {"[", "х", "Х"}:
            return "speed_down"
        if char in {
            "]", "ъ", "Ъ"}:
            return "speed_up"
        if char == "1":
            return "toggle_enable"
        if char == "2":
            return "toggle_slow"
        if char == "4":
            return "toggle_reload"
        if char == "5":
            return "toggle_force_home"
        if char in {"s", "S", "ы", "Ы", "і", "І"}:
            return "down_latch"
        if char in {"a", "A", "ф", "Ф"}:
            return "left_latch"
        if char in {"d", "D", "в", "В"}:
            return "right_latch"
        if char == " ":
            return "fire"
        if char == "7":
            return "fire_mode_short"
        if char == "8":
            return "fire_mode_medium"
        if char == "9":
            return "fire_mode_manual"
        if char in {"\b", "\x7f"}:
            return "toggle_arm"
        if char in {"v", "V", "м", "М"}:
            return "stop"
        if char in {"h", "H", "р", "Р", "?"}:
            return "help"
        return None


@dataclass
class KeyboardController:
    config: KeyboardControlConfig
    next_sequence: int
    fire_mode: str = FIRE_MODE_SHORT
    speed_percent: int = DEFAULT_SPEED_PERCENT
    turret_enable: bool = False
    turret_slow: bool = False
    # Direct live movement uses the same velocity-priority style as the reference senders.
    turret_vel_prio: bool = False
    turret_reload: bool = False
    turret_force_home_until: float = 0.0
    safety_arm_enabled: bool = False
    # Packet-level rotP / eleP state produced from the current semantic angular targets.
    rotation_p: int = 0
    elevation_p: int = 0
    fire_seq: int = 0
    horizontal_sign: int = 0
    vertical_sign: int = 0
    horizontal_until: float = 0.0
    vertical_until: float = 0.0
    fire_duration_active: int = 0
    fire_until: float = 0.0
    fire_seq_pending: bool = False
    horizontal_latched: int = 0
    vertical_latched: int = 0
    center_requested: bool = False

    def handle_event(self, event: str, now: float) -> str | None:
        if event == "left":
            self.center_requested = False
            self.turret_vel_prio = True
            self.horizontal_latched = 0
            self.horizontal_sign = -1
            self.horizontal_until = now + self.config.axis_hold_seconds
            return "left pulse"
        if event == "right":
            self.center_requested = False
            self.turret_vel_prio = True
            self.horizontal_latched = 0
            self.horizontal_sign = 1
            self.horizontal_until = now + self.config.axis_hold_seconds
            return "right pulse"
        if event == "up":
            self.center_requested = False
            self.turret_vel_prio = True
            self.vertical_latched = 0
            self.vertical_sign = 1
            self.vertical_until = now + self.config.axis_hold_seconds
            return "up pulse"
        if event == "down":
            self.center_requested = False
            self.turret_vel_prio = True
            self.vertical_latched = 0
            self.vertical_sign = -1
            self.vertical_until = now + self.config.axis_hold_seconds
            return "down pulse"
        if event == "left_latch":
            self.center_requested = False
            self.turret_vel_prio = True
            self.horizontal_sign = 0
            self.horizontal_until = 0.0
            if self.horizontal_latched == -1:
                self.horizontal_latched = 0
                return "left axis off"
            self.horizontal_latched = -1
            return "left axis latched"
        if event == "right_latch":
            self.center_requested = False
            self.turret_vel_prio = True
            self.horizontal_sign = 0
            self.horizontal_until = 0.0
            if self.horizontal_latched == 1:
                self.horizontal_latched = 0
                return "right axis off"
            self.horizontal_latched = 1
            return "right axis latched"
        if event == "up_latch":
            self.center_requested = False
            self.turret_vel_prio = True
            self.vertical_sign = 0
            self.vertical_until = 0.0
            if self.vertical_latched == 1:
                self.vertical_latched = 0
                return "up axis off"
            self.vertical_latched = 1
            return "up axis latched"
        if event == "down_latch":
            self.center_requested = False
            self.turret_vel_prio = True
            self.vertical_sign = 0
            self.vertical_until = 0.0
            if self.vertical_latched == -1:
                self.vertical_latched = 0
                return "down axis off"
            self.vertical_latched = -1
            return "down axis latched"
        if event == "toggle_enable":
            self.turret_enable = not self.turret_enable
            return f"turret_enable={'on' if self.turret_enable else 'off'}"
        if event == "toggle_slow":
            self.turret_slow = not self.turret_slow
            return f"turret_slow={'on' if self.turret_slow else 'off'}"
        if event == "toggle_reload":
            self.turret_reload = not self.turret_reload
            return f"turret_reload={'on' if self.turret_reload else 'off'}"
        if event == "toggle_force_home":
            self.turret_force_home_until = now + FORCE_HOME_PULSE_SECONDS
            return f"turret_force_home pulse({FORCE_HOME_PULSE_SECONDS:.1f}s)"
        if event == "toggle_arm":
            self.safety_arm_enabled = not self.safety_arm_enabled
            return f"safetyARM={'ARM' if self.safety_arm_enabled else 'off'}"
        if event == "speed_down":
            self.speed_percent = max(MIN_SPEED_PERCENT, self.speed_percent - SPEED_STEP_PERCENT)
            return f"speed={self.speed_percent}%"
        if event == "speed_up":
            self.speed_percent = min(MAX_SPEED_PERCENT, self.speed_percent + SPEED_STEP_PERCENT)
            return f"speed={self.speed_percent}%"
        if event == "fire_mode_short":
            self.fire_mode = FIRE_MODE_SHORT
            return f"fire_mode={describe_fire_mode(self.fire_mode)}"
        if event == "fire_mode_medium":
            self.fire_mode = FIRE_MODE_MEDIUM
            return f"fire_mode={describe_fire_mode(self.fire_mode)}"
        if event == "fire_mode_manual":
            self.fire_mode = FIRE_MODE_MANUAL
            return f"fire_mode={describe_fire_mode(self.fire_mode)}"
        if event == "stop":
            self.turret_vel_prio = True
            self.horizontal_sign = 0
            self.vertical_sign = 0
            self.horizontal_until = 0.0
            self.vertical_until = 0.0
            self.fire_until = 0.0
            self.fire_duration_active = 0
            self.fire_seq_pending = False
            self.horizontal_latched = 0
            self.vertical_latched = 0
            self.center_requested = False
            self.rotation_p = 0
            self.elevation_p = 0
            return "stop"
        if event == "center":
            # Reserved for future re-enable; center is intentionally disabled for now.
            return None
        if event == "fire":
            fire_was_active = self.is_fire_active(now)
            self.fire_duration_active = FIRE_MODE_DURATIONS[self.fire_mode]
            self.fire_until = now + self.config.axis_hold_seconds
            if not fire_was_active:
                self.fire_seq_pending = True
                return f"fire hold ({describe_fire_mode(self.fire_mode)})"
            return None
        return None

    def turret_force_home_active(self, now: float) -> bool:
        return now < self.turret_force_home_until

    def _flags1(self, now: float) -> int:
        flags = 0x00
        if self.turret_enable:
            flags |= FLAGS1_ENABLE
        if self.turret_slow:
            flags |= FLAGS1_SLOW
        if self.turret_reload:
            flags |= FLAGS1_RELOAD
        if self.turret_force_home_active(now):
            flags |= FLAGS1_FORCE_HOME
        return flags

    def _flags2_validity(self, rotation_direction: int, elevation_direction: int) -> tuple[bool, bool, bool, bool]:
        rotation_v_valid = not self.center_requested
        elevation_v_valid = not self.center_requested
        rotation_p_valid = self.center_requested or rotation_direction != 0
        elevation_p_valid = self.center_requested or elevation_direction != 0
        return rotation_v_valid, elevation_v_valid, rotation_p_valid, elevation_p_valid

    def _flags2(self, rotation_direction: int, elevation_direction: int) -> int:
        rotation_v_valid, elevation_v_valid, rotation_p_valid, elevation_p_valid = self._flags2_validity(
            rotation_direction,
            elevation_direction,
        )
        flags = 0x00
        if rotation_v_valid:
            flags |= FLAGS2_ROTATION_V
        if elevation_v_valid:
            flags |= FLAGS2_ELEVATION_V
        if rotation_p_valid:
            flags |= FLAGS2_ROTATION_P
        if elevation_p_valid:
            flags |= FLAGS2_ELEVATION_P
        if self.turret_vel_prio:
            flags |= FLAGS2_VEL_PRIO
        return flags

    def _arm_bytes(self) -> bytes:
        if self.safety_arm_enabled:
            return b"A\x00\x00\x00"
        return b"\x00\x00\x00\x00"

    def is_fire_active(self, now: float) -> bool:
        return now < self.fire_until

    def describe_fire_state(self, now: float) -> str:
        if self.is_fire_active(now):
            return f"hold(duration={self.fire_duration_active})"
        return "off"

    def speed_scale(self) -> float:
        return self.speed_percent / 100.0

    def motion_display_values(
        self, now: float
    ) -> tuple[float | None, float | None, float | None, float | None]:
        rotation_direction, elevation_direction = self._active_motion_directions(now)
        speed_scale = self.speed_scale()
        rotation_v_unit: float | None = rotation_direction * self.config.rotation_v_unit * speed_scale
        if elevation_direction > 0:
            elevation_v_unit: float | None = self.config.elevation_v_up_unit * speed_scale
        elif elevation_direction < 0:
            elevation_v_unit = -self.config.elevation_v_down_unit * speed_scale
        else:
            elevation_v_unit = 0.0

        rotation_p_rad: float | None = None
        elevation_p_rad: float | None = None
        if self.center_requested:
            rotation_p_rad = decode_packet_angle_s32_to_rad(self.rotation_p)
            elevation_p_rad = decode_packet_angle_s32_to_rad(self.elevation_p)
        elif rotation_direction > 0:
            rotation_p_rad = REFERENCE_POSITIVE_TARGET_RAD
        elif rotation_direction < 0:
            rotation_p_rad = REFERENCE_NEGATIVE_TARGET_RAD
        if self.center_requested:
            pass
        elif elevation_direction > 0:
            elevation_p_rad = REFERENCE_POSITIVE_TARGET_RAD
        elif elevation_direction < 0:
            elevation_p_rad = REFERENCE_NEGATIVE_TARGET_RAD

        flags2 = self._flags2(rotation_direction, elevation_direction)

        if not (flags2 & FLAGS2_ROTATION_V):
            rotation_v_unit = None
        if not (flags2 & FLAGS2_ELEVATION_V):
            elevation_v_unit = None
        if rotation_p_rad is None and (flags2 & FLAGS2_ROTATION_P):
            rotation_p_rad = decode_packet_angle_s32_to_rad(self.rotation_p)
        if elevation_p_rad is None and (flags2 & FLAGS2_ELEVATION_P):
            elevation_p_rad = decode_packet_angle_s32_to_rad(self.elevation_p)
        if not (flags2 & FLAGS2_ROTATION_P):
            rotation_p_rad = None
        if not (flags2 & FLAGS2_ELEVATION_P):
            elevation_p_rad = None
        return rotation_v_unit, elevation_v_unit, rotation_p_rad, elevation_p_rad

    def build_cmd_packet(self, salt: bytes, now: float) -> CommandPacket:
        rotation_direction, elevation_direction = self._active_motion_directions(now)
        speed_scale = self.speed_scale()
        if self.center_requested:
            self.rotation_p = 0
            self.elevation_p = 0
        elif rotation_direction > 0:
            self.rotation_p = encode_angle_rad_to_packet_s32(REFERENCE_POSITIVE_TARGET_RAD)
        elif rotation_direction < 0:
            self.rotation_p = encode_angle_rad_to_packet_s32(REFERENCE_NEGATIVE_TARGET_RAD)
        else:
            self.rotation_p = 0

        if self.center_requested:
            pass
        elif elevation_direction > 0:
            self.elevation_p = encode_angle_rad_to_packet_s32(REFERENCE_POSITIVE_TARGET_RAD)
        elif elevation_direction < 0:
            self.elevation_p = encode_angle_rad_to_packet_s32(REFERENCE_NEGATIVE_TARGET_RAD)
        else:
            self.elevation_p = 0

        rotation_v = encode_unit_axis_to_packet_s16(rotation_direction * self.config.rotation_v_unit * speed_scale)
        if elevation_direction > 0:
            elevation_v = encode_unit_axis_to_packet_s16(self.config.elevation_v_up_unit * speed_scale)
        elif elevation_direction < 0:
            elevation_v = encode_unit_axis_to_packet_s16(-self.config.elevation_v_down_unit * speed_scale)
        else:
            elevation_v = 0
        flags2 = self._flags2(rotation_direction, elevation_direction)

        arm = self._arm_bytes()
        fire = b"\x00\x00"
        fire_duration = 0
        if self.is_fire_active(now):
            if self.fire_seq_pending:
                self.fire_seq = (self.fire_seq + 1) & 0xFF
                self.fire_seq_pending = False
            fire = b"F\x00"
            fire_duration = self.fire_duration_active

        return self._build_packet(
            salt=salt,
            name="keyboard_live",
            flags1=self._flags1(now),
            flags2=flags2,
            rotation_v=rotation_v,
            elevation_v=elevation_v,
            arm=arm,
            fire=fire,
            fire_duration=fire_duration,
        )

    def _active_motion_directions(self, now: float) -> tuple[int, int]:
        # Returns the currently active movement directions for rotation / elevation:
        # -1 = negative direction, 0 = idle, +1 = positive direction.
        # Arrow keys stay active until the synthetic hold timeout expires; WASD creates a latched direction.
        if now > self.horizontal_until:
            self.horizontal_sign = 0
        if now > self.vertical_until:
            self.vertical_sign = 0
        rotation_direction = (
            self.horizontal_latched if self.horizontal_latched != 0 else self.horizontal_sign
        )
        elevation_direction = (
            self.vertical_latched if self.vertical_latched != 0 else self.vertical_sign
        )
        return rotation_direction, elevation_direction

    def _build_packet(
        self,
        salt: bytes,
        name: str,
        flags1: int,
        flags2: int,
        rotation_v: int,
        elevation_v: int,
        arm: bytes,
        fire: bytes,
        fire_duration: int,
    ) -> CommandPacket:
        sequence = self.next_sequence
        packet = build_generated_command_packet(
            name=name,
            sequence=sequence,
            flags1=flags1,
            flags2=flags2,
            flags3=0,
            flags4=0,
            rotation_v=rotation_v,
            elevation_v=elevation_v,
            rotation_p=self.rotation_p,
            elevation_p=self.elevation_p,
            arm=arm,
            fire=fire,
            fire_duration=fire_duration,
            cameras_p=0,
            rangefinder_seq=0,
            fire_seq=self.fire_seq,
            salt=salt,
        )
        self.next_sequence = (self.next_sequence + 1) & 0xFFFF
        return packet

def parse_args() -> argparse.Namespace:
    argv = sys.argv[1:]
    if argv and argv[0] in LEGACY_LIVE_ALIASES:
        argv = argv[1:]
    elif argv and argv[0] in REMOVED_COMMANDS:
        raise ValueError(
            f"mode '{argv[0]}' removed; only live mode remains. Run test_control.py [options]"
        )

    parser = argparse.ArgumentParser(
        description=(
            "Run live 40-byte RWS control traffic "
            f"from {DEFAULT_SRC_IP}:{DEFAULT_SRC_PORT} to {DEFAULT_DST_IP}:{DEFAULT_DST_PORT}."
        ),
        epilog=(
            "Keys: W/A/S/D=latch axes, arrows=momentary move, 7=short fire (161), 8=medium fire (605), "
            "9=manual fire hold, 1=toggle enable, 2=toggle slow, 4=toggle reload, 5=forceHome pulse, Backspace=toggle safetyARM, "
            "[=speed-10%, ]=speed+10%, "
            "Space=fire while held, "
            "v=stop, h or ?=help, q=quit."
        ),
    )
    parser.add_argument("--bind-ip", default=DEFAULT_SRC_IP, help=f"Source IPv4 to bind (default: {DEFAULT_SRC_IP})")
    parser.add_argument("--bind-port", type=int, default=DEFAULT_SRC_PORT, help=f"Source UDP port (default: {DEFAULT_SRC_PORT})")
    parser.add_argument("--dst-ip", default=DEFAULT_DST_IP, help=f"Destination IPv4 (default: {DEFAULT_DST_IP})")
    parser.add_argument("--dst-port", type=int, default=DEFAULT_DST_PORT, help=f"Destination UDP port (default: {DEFAULT_DST_PORT})")
    parser.add_argument(
        "--sequence-start",
        default=f"0x{DEFAULT_KEYBOARD_SEQUENCE_START:04x}",
        help="Starting sequence number for generated packets (default: 0x0000)",
    )
    parser.add_argument(
        "--salt-file",
        default="",
        help=(
            "Optional binary salt/key override used to compute command checksum as sha256(body36 + salt)[:4]. "
            "When omitted, uses the built-in 32-byte salt recovered from salt.bin"
        ),
    )
    parser.add_argument(
        "--interval-ms",
        type=float,
        default=PERIOD_MS,
        help="Packet interval in ms for generated control traffic (default: 50)",
    )
    parser.add_argument(
        "--ui-render-ms",
        type=float,
        default=DEFAULT_UI_RENDER_MS,
        help="TTY screen refresh interval in ms (default: 100)",
    )
    parser.add_argument(
        "--axis-hold-ms",
        type=float,
        default=DEFAULT_AXIS_HOLD_MS,
        help=(
            "Synthetic key-release timeout in ms for arrows and fire. Because the terminal reports "
            "key presses/repeats but not key release, momentary inputs remain active until this "
            "timeout expires after the last repeat. WASD uses latched axes."
        ),
    )
    parser.add_argument(
        "--rotation-v",
        dest="rotation_v",
        type=float,
        default=DEFAULT_ROTATION_V_UNIT,
        help="Absolute normalized rotationV amplitude used for left/right motion in range 0..1 (default: 1.0)",
    )
    parser.add_argument(
        "--axis-x",
        dest="rotation_v",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--elevation-v-up",
        dest="elevation_v_up",
        type=float,
        default=DEFAULT_ELEVATION_V_UP_UNIT,
        help="Positive normalized elevationV amplitude used for up motion in range 0..1 (default: 1.0)",
    )
    parser.add_argument(
        "--axis-y-up",
        dest="elevation_v_up",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--elevation-v-down",
        dest="elevation_v_down",
        type=float,
        default=DEFAULT_ELEVATION_V_DOWN_UNIT,
        help="Absolute normalized elevationV amplitude used for down motion in range 0..1 (default: 1.0)",
    )
    parser.add_argument(
        "--axis-y-down",
        dest="elevation_v_down",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--fire-mode",
        choices=(FIRE_MODE_SHORT, FIRE_MODE_MEDIUM, FIRE_MODE_MANUAL),
        default=FIRE_MODE_SHORT,
        help=(
            "Initial fire mode for Space: short=161, medium=605, manual=0 while held "
            "(default: short)"
        ),
    )
    parser.add_argument(
        "--packet-limit",
        type=int,
        default=0,
        help="Stop after this many packets total (default: unlimited)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate packets without opening a UDP socket",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every generated packet instead of only state changes",
    )
    return parser.parse_args(argv)


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    ip_cmd = shutil.which("ip")
    if ip_cmd is not None:
        result = subprocess.run(
            [ip_cmd, "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if "inet" not in parts:
                    continue
                inet_index = parts.index("inet")
                if inet_index + 1 < len(parts):
                    addresses.add(parts[inet_index + 1].split("/")[0])

    if not addresses:
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM)
        except socket.gaierror:
            infos = []
        for info in infos:
            addresses.add(info[4][0])

    return sorted(addresses)


def warn_if_bind_ip_missing(bind_ip: str) -> None:
    local_ips = local_ipv4_addresses()
    if not local_ips:
        print(
            f"warning: could not determine local IPv4 addresses; expected {bind_ip}",
            file=sys.stderr,
        )
        return
    if bind_ip not in local_ips:
        print(
            f"warning: {bind_ip} is not assigned locally; detected IPv4 addresses: {', '.join(local_ips)}",
            file=sys.stderr,
        )


def parse_integer_token(token: str, option_name: str) -> int:
    try:
        return int(token, 0)
    except ValueError as exc:
        raise ValueError(f"invalid {option_name} selector: {token}") from exc


def load_binary_file(path_text: str) -> bytes:
    path = Path(path_text)
    if not path.is_file():
        raise ValueError(f"binary file not found: {path}")
    return path.read_bytes()


def resolve_salt_bytes(path_text: str) -> bytes:
    if not path_text:
        return DEFAULT_EMBEDDED_SALT
    return load_binary_file(path_text)


def keyboard_signature(packet: CommandPacket) -> tuple[object, ...]:
    return (
        packet.flags1,
        packet.flags2,
        packet.flags3,
        packet.flags4,
        packet.rotation_v,
        packet.elevation_v,
        packet.rotation_p,
        packet.elevation_p,
        packet.arm,
        packet.fire,
        packet.fire_duration,
        packet.cameras_p,
        packet.rangefinder_seq,
        packet.fire_seq,
    )


def keyboard_control_help_lines(bind_ip: str, dst_ip: str, is_tty: bool, axis_hold_ms: float) -> list[str]:
    return [
        f"src={bind_ip} RWS={dst_ip} | ",
        "  W/A/S/D: latched axes | arrows: momentary move | 7: fire short(161) | 8: fire medium(605) | 9: fire manual hold",
        "  1: enable | 2: slow | 4: reload | 5: forceHome pulse | [: speed-10% | ]: speed+10% | velPrio: auto in direct control | backspace: safetyARM | space: fire while held | v: stop | h or ?: help | q: quit",
        (
            f"Arrows and fire stay active until {axis_hold_ms:.0f} ms timeout expires after the last repeat."
        ),
    ]


def print_keyboard_controls(bind_ip: str, dst_ip: str, is_tty: bool, axis_hold_ms: float) -> None:
    for line in keyboard_control_help_lines(bind_ip, dst_ip, is_tty, axis_hold_ms):
        print(line)


def process_transport_events(
    events: list[RwsTransportEvent],
    reply_tracker: RwsReplyTracker,
    renderer: "KeyboardScreenRenderer | None" = None,
    log_replies: bool = False,
) -> None:
    for event in events:
        if event.kind != "reply":
            message = event.message if event.message is not None else f"recv len={len(event.data)} from {event.addr[0]}:{event.addr[1]}"
            if renderer is not None:
                renderer.add_log(message)
            else:
                print(message)
            continue

        data = event.data
        addr = event.addr
        reply_tracker.record(data)
        if renderer is not None:
            renderer.update_reply(data, addr, log=log_replies)
        elif log_replies:
            for line in format_reply_lines(data, addr):
                print(line)


def validate_keyboard_args(args: argparse.Namespace) -> KeyboardControlConfig:
    if args.interval_ms <= 0:
        raise ValueError("--interval-ms must be > 0")
    if args.ui_render_ms <= 0:
        raise ValueError("--ui-render-ms must be > 0")
    if args.axis_hold_ms < 0:
        raise ValueError("--axis-hold-ms must be >= 0")
    if args.packet_limit < 0:
        raise ValueError("--packet-limit must be >= 0")
    if args.rotation_v <= 0:
        raise ValueError("--rotation-v must be > 0")
    if args.elevation_v_up <= 0:
        raise ValueError("--elevation-v-up must be > 0")
    if args.elevation_v_down <= 0:
        raise ValueError("--elevation-v-down must be > 0")
    if args.rotation_v > REFERENCE_MAX_AXIS_UNIT:
        raise ValueError("--rotation-v must be <= 1.0")
    if args.elevation_v_up > REFERENCE_MAX_AXIS_UNIT:
        raise ValueError("--elevation-v-up must be <= 1.0")
    if args.elevation_v_down > REFERENCE_MAX_AXIS_UNIT:
        raise ValueError("--elevation-v-down must be <= 1.0")

    return KeyboardControlConfig(
        interval_seconds=args.interval_ms / 1000.0,
        axis_hold_seconds=args.axis_hold_ms / 1000.0,
        rotation_v_unit=args.rotation_v,
        elevation_v_up_unit=args.elevation_v_up,
        elevation_v_down_unit=args.elevation_v_down,
    )


def replay_keyboard_control(args: argparse.Namespace) -> int:
    warn_if_bind_ip_missing(args.bind_ip)
    config = validate_keyboard_args(args)
    sequence_start = parse_integer_token(args.sequence_start, "--sequence-start")
    if sequence_start < 0 or sequence_start > 0xFFFF:
        raise ValueError("--sequence-start must be in range 0..0xffff")

    salt = resolve_salt_bytes(args.salt_file)
    controller = KeyboardController(config=config, next_sequence=sequence_start, fire_mode=args.fire_mode)
    reply_tracker = RwsReplyTracker()
    input_reader = KeyboardInputReader()
    transport = None
    if not args.dry_run:
        transport = RwsControlChannel(
            bind_ip=args.bind_ip,
            bind_port=args.bind_port,
            dst_ip=args.dst_ip,
            dst_port=args.dst_port,
        ).open()
    sent_packets = 0
    last_signature: tuple[object, ...] | None = None
    next_send_at = time.monotonic()
    ui_render_interval_seconds = args.ui_render_ms / 1000.0
    next_render_at = next_send_at
    renderer = KeyboardScreenRenderer(
        enabled=input_reader.is_tty and sys.stdout.isatty(),
        bind_ip=args.bind_ip,
        dst_ip=args.dst_ip,
        is_input_tty=input_reader.is_tty,
        axis_hold_ms=args.axis_hold_ms,
    )
    interrupted = False

    def maybe_render(packet_count: int, force: bool = False) -> None:
        ''' Render a new screen if enough time has passed since the last render, or if forced.'''
        nonlocal next_render_at
        if not renderer.enabled:
            return
        now = time.monotonic()
        if not force and now < next_render_at:
            return
        renderer.render(controller, reply_tracker, packet_count, args.packet_limit)
        next_render_at = now + ui_render_interval_seconds

    if renderer.enabled:
        renderer.start()
        maybe_render(sent_packets, force=True)
    else:
        print_keyboard_controls(args.bind_ip, args.dst_ip, input_reader.is_tty, args.axis_hold_ms)

    try:
        with input_reader:
            while True:
                if args.packet_limit and sent_packets >= args.packet_limit:
                    break

                readers: list[object] = []
                input_fd = input_reader.fileno()
                if input_fd is not None and not input_reader.eof:
                    readers.append(input_fd)

                now = time.monotonic()
                next_wake_at = min(next_send_at, next_render_at) if renderer.enabled else next_send_at
                timeout = max(0.0, next_wake_at - now)
                if readers:
                    readable, _, _ = select.select(readers, [], [], timeout)
                else:
                    readable = []
                    if timeout > 0:
                        time.sleep(timeout)

                if not readable:
                    now = time.monotonic()
                    if renderer.enabled and now >= next_render_at:
                        maybe_render(sent_packets)
                        if transport is not None:
                            events = transport.poll_events(timeout_seconds=0.0)
                            process_transport_events(
                                events,
                                reply_tracker,
                                renderer=renderer if renderer.enabled else None,
                                log_replies=args.verbose,
                            )
                        continue
                    packet = controller.build_cmd_packet(salt, time.monotonic())

                    if transport is not None:
                        transport.send_command(packet)
                        reply_tracker.record_send_sequence(packet.sequence)

                    signature = keyboard_signature(packet)
                    should_log_packet = args.verbose or args.dry_run or signature != last_signature
                    if renderer.enabled:
                        renderer.update_packet(packet, log=should_log_packet)
                        maybe_render(sent_packets + 1, force=should_log_packet)
                    elif should_log_packet:
                        for line in format_packet_lines("send tx cmd", packet):
                            print(line)
                        last_signature = signature
                    elif not renderer.enabled:
                        last_signature = signature

                    if renderer.enabled and should_log_packet:
                        last_signature = signature

                    sent_packets += 1
                    next_send_at += config.interval_seconds
                    if transport is not None:
                        events = transport.poll_events(timeout_seconds=0.0)
                        process_transport_events(
                            events,
                            reply_tracker,
                            renderer=renderer if renderer.enabled else None,
                            log_replies=args.verbose,
                        )
                    continue

                if input_fd is not None and input_fd in readable:
                    for event in input_reader.read_events():
                        if event == "quit":
                            raise KeyboardInterrupt()
                        if event == "help":
                            if renderer.enabled:
                                renderer.add_log("help is always visible in the top half")
                                maybe_render(sent_packets, force=True)
                            else:
                                print_keyboard_controls(args.bind_ip, args.dst_ip, input_reader.is_tty, args.axis_hold_ms)
                            continue
                        note = controller.handle_event(event, time.monotonic())
                        if note is not None:
                            if renderer.enabled:
                                renderer.add_log(f"event: {note}")
                                maybe_render(sent_packets, force=True)
                            else:
                                print(f"event: {note}")

                if transport is not None:
                    events = transport.poll_events(timeout_seconds=0.0)
                    process_transport_events(
                        events,
                        reply_tracker,
                        renderer=renderer if renderer.enabled else None,
                        log_replies=args.verbose,
                    )
                    maybe_render(sent_packets)
    except KeyboardInterrupt:
        interrupted = True
        if renderer.enabled:
            renderer.add_log("Stopping keyboard control")
            maybe_render(sent_packets, force=True)
        else:
            print("Stopping keyboard control")
    finally:
        if transport is not None:
            transport.close()
        if renderer.enabled:
            renderer.stop()

    if interrupted and renderer.enabled:
        print("Stopping keyboard control")

    if transport is not None:
        print(reply_tracker.summary_text())
    return 0


def format_packet_lines(label: str, packet: CommandPacket) -> list[str]:
    indent = " " * (len(label) + 2)
    summary_lines = packet.summary_lines()
    return [
        f"{label}: {summary_lines[0]}",
        f"{indent}{summary_lines[1]}",
        f"{indent}{summary_lines[2]}",
        f"{indent}raw={packet.raw_hex()}",
    ]


def reply_kind_from_payload(data: bytes) -> str | None:
    length = len(data)
    if length == RWS_STATUS_PAYLOAD_LEN:
        return RX_KIND_RWS_STATUS
    if length == RWS_TELEMETRY_PAYLOAD_LEN:
        return RX_KIND_RWS_TELEMETRY
    return None


def split_summary_text(summary: str) -> list[str]:
    lines = summary.splitlines()
    return lines or [""]


def default_reply_summaries() -> dict[str, str]:
    return {kind: "<none yet>" for kind in RX_KIND_ORDER}


def default_reply_raws() -> dict[str, str]:
    return {kind: "<none yet>" for kind in RX_KIND_ORDER}


def decode_reply_summary(data: bytes, _addr: tuple[str, int]) -> str:
    msg_type = data[0:2].hex()
    seq = int.from_bytes(data[2:4], "big") if len(data) >= 4 else 0
    prefix = f"recv len={len(data)} type={msg_type} seq=0x{seq:04x}"

    if len(data) == RwsReplyWire.byte_size():
        wire = RwsReplyWire.from_bytes(data)
        rotation_p_valid = bool(wire.flags1 & RWS_STATUS_FLAGS1_ROTATION_P_VALID)
        elevation_p_valid = bool(wire.flags1 & RWS_STATUS_FLAGS1_ELEVATION_P_VALID)
        return "\n".join(
            (
                f"{prefix} rws-status flags={wire.flags0:02x}/{wire.flags1:02x}/{wire.flags2:02x}/{wire.flags3:02x}",
                f"rotP={format_angle_packet_s32(wire.rotation_p, valid=rotation_p_valid)} "
                f"eleP={format_angle_packet_s32(wire.elevation_p, valid=elevation_p_valid)} "
                f"shots={wire.shots} checksum={bytes(wire.checksum).hex()}",
            )
        )

    if len(data) == RwsTelemetryWire.byte_size():
        wire = RwsTelemetryWire.from_bytes(data)
        return "\n".join(
            (
                f"{prefix} rws-telemetry flags={wire.flags0:02x}/{wire.flags1:02x}/{wire.flags2:02x}/{wire.flags3:02x}",
                f"xRpm={wire.rpm_x} xU={wire.voltage_x * 0.01:.2f} xI={wire.amperage_x} xT={wire.temperature_x} "
                f"yRpm={wire.rpm_y} yU={wire.voltage_y * 0.01:.2f} yI={wire.amperage_y} yT={wire.temperature_y}",
                f"batU={wire.voltage_bat * 0.01:.2f} fireU={wire.voltage_fire * 0.01:.2f} cpuU={wire.voltage_cpu * 0.01:.2f} "
                f"batPct={wire.battery_percent / 0xFFFF * 100.0:.1f} checksum={bytes(wire.checksum).hex()}",
            )
        )

    return prefix


def format_reply_lines(data: bytes, addr: tuple[str, int]) -> list[str]:
    return [
        *split_summary_text(decode_reply_summary(data, addr)),
        f"raw={data.hex()}",
    ]


@dataclass
class KeyboardScreenRenderer:
    enabled: bool
    bind_ip: str
    dst_ip: str
    is_input_tty: bool
    axis_hold_ms: float
    log_entries: deque[str] = field(default_factory=deque)
    last_packet: CommandPacket | None = None
    last_reply_by_kind: dict[str, str] = field(default_factory=default_reply_summaries)
    last_reply_raw_by_kind: dict[str, str] = field(default_factory=default_reply_raws)

    def start(self) -> None:
        if not self.enabled:
            return
        sys.stdout.write(ANSI_ALT_SCREEN_ON + ANSI_HIDE_CURSOR + ANSI_CURSOR_HOME)
        sys.stdout.flush()

    def stop(self) -> None:
        if not self.enabled:
            return
        sys.stdout.write(ANSI_SHOW_CURSOR + ANSI_ALT_SCREEN_OFF)
        sys.stdout.flush()

    def add_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_entries.append(f"[{timestamp}] {message}")
        while len(self.log_entries) > 400:
            self.log_entries.popleft()

    def update_packet(self, packet: CommandPacket, log: bool) -> None:
        self.last_packet = packet
        if log:
            for line in format_packet_lines("tx cmd", packet):
                self.add_log(line)

    def update_reply(self, data: bytes, addr: tuple[str, int], log: bool) -> None:
        summary = decode_reply_summary(data, addr)
        kind = reply_kind_from_payload(data)
        if kind is not None:
            self.last_reply_by_kind[kind] = summary
            self.last_reply_raw_by_kind[kind] = data.hex()
        if log:
            for line in format_reply_lines(data, addr):
                self.add_log(line)

    def render(
        self,
        controller: KeyboardController,
        reply_tracker: RwsReplyTracker,
        sent_packets: int,
        packet_limit: int,
    ) -> None:
        if not self.enabled:
            return

        width, height = shutil.get_terminal_size((80, 24))
        # top_height = min(max(10, height // 2 + 5), max(1, height - 3))
        top_height = min(max(10, height // 2 + 6), max(1, height - 5))
        log_height = height - top_height
        now = time.monotonic()
        rotation_v_unit, elevation_v_unit, rotation_p_rad, elevation_p_rad = controller.motion_display_values(now)

        top_lines: list[str] = []
        help_lines = keyboard_control_help_lines(self.bind_ip, self.dst_ip, self.is_input_tty, self.axis_hold_ms)
        link_line = reply_tracker.describe_connection(now)
        traffic_line = (
            f"sent={sent_packets} complete={reply_tracker.complete_replies} pending={reply_tracker.pending_packets}"
        )
        if packet_limit:
            traffic_line += f" limit={packet_limit}"
        if help_lines:
            top_lines.extend(wrap_prefixed("", f"{help_lines[0]}link: {link_line} | {traffic_line}", width))
            top_lines.extend(help_lines[1:])
        else:
            top_lines.extend(wrap_prefixed("", f"link: {link_line} | {traffic_line}", width))
        top_lines.extend(
            wrap_prefixed(
                "  state: ",
                (
                    f"turret_enable={'on' if controller.turret_enable else 'off'} "
                    f"turret_slow={'on' if controller.turret_slow else 'off'} "
                    f"turret_vel_prio={'on' if controller.turret_vel_prio else 'off'} "
                    f"turret_reload={'on' if controller.turret_reload else 'off'} "
                    f"turret_force_home={'on' if controller.turret_force_home_active(now) else 'off'} "
                    f"safetyARM={'ARM' if controller.safety_arm_enabled else 'off'} "
                    f"fireMode={describe_fire_mode(controller.fire_mode)} "
                    f"fireSeq={controller.fire_seq} fireState={controller.describe_fire_state(now)} nextSeq=0x{controller.next_sequence:04x}"
                ),
                width,
            )
        )
        top_lines.extend(
            wrap_prefixed(
                "  motion: ",
                (
                    f"speed={controller.speed_percent}% "
                    f"horizontal={controller.horizontal_sign} vertical={controller.vertical_sign} "
                    f"latched=({controller.horizontal_latched},{controller.vertical_latched}) "
                    f"rotV={format_unit_percent(rotation_v_unit)} "
                    f"eleV={format_unit_percent(elevation_v_unit)} "
                    f"rotP={format_angle_degrees(rotation_p_rad)} "
                    f"eleP={format_angle_degrees(elevation_p_rad)}"
                ),
                width,
            )
        )

        if self.last_packet is None:
            top_lines.append("last tx: <no packets sent yet>")
        else:
            packet_summary_lines = self.last_packet.summary_lines()
            top_lines.extend(
                wrap_prefixed(
                    f"last tx cmd: ",
                    packet_summary_lines[0],
                    width,
                )
            )
            top_lines.extend(wrap_prefixed("             ", packet_summary_lines[1], width))
            top_lines.extend(wrap_prefixed("             ", packet_summary_lines[2], width))
            top_lines.extend(wrap_prefixed("        raw: ", self.last_packet.raw_hex(), width))

        for kind in RX_KIND_ORDER:
            prefix = f"{RX_KIND_LABELS[kind]}: "
            continuation_prefix = " " * len(prefix)
            reply_lines = split_summary_text(self.last_reply_by_kind[kind])
            top_lines.extend(wrap_prefixed(prefix, reply_lines[0], width))
            for reply_line in reply_lines[1:]:
                top_lines.extend(wrap_prefixed(continuation_prefix, reply_line, width))
            top_lines.extend(
                wrap_prefixed(
                    f"       raw: ",
                    self.last_reply_raw_by_kind[kind],
                    width,
                )
            )

        top_lines = top_lines[:top_height]
        while len(top_lines) < top_height:
            top_lines.append("")

        wrapped_log_lines: list[str] = []
        for entry in self.log_entries:
            wrapped_log_lines.extend(wrap_prefixed("", entry, width))

        bottom_lines = ["Log".center(width, "-")]
        bottom_lines.extend(wrapped_log_lines[-(log_height - 1):])
        while len(bottom_lines) < log_height:
            bottom_lines.append("")

        screen_lines = top_lines + bottom_lines
        screen_lines = [line[:width].ljust(width) for line in screen_lines[:height]]
        while len(screen_lines) < height:
            screen_lines.append(" " * width)

        sys.stdout.write(ANSI_CURSOR_HOME)
        for index, line in enumerate(screen_lines):
            if index + 1 == len(screen_lines):
                sys.stdout.write(line)
            else:
                sys.stdout.write(line + "\n")
        sys.stdout.flush()



def main() -> int:
    try:
        args = parse_args()
        return replay_keyboard_control(args)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


