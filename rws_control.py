#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import hashlib
import math
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import termios
import textwrap
import time
import tty
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, TypeVar


RWS_STATUS_PAYLOAD_LEN = 32
RWS_TELEMETRY_PAYLOAD_LEN = 36
TEMPERATURE_REPORT_PAYLOAD_LEN = 50
UGV_TELEMETRY_REPORT_PAYLOAD_LEN = 74
TRACKED_RWS_REPLY_LENGTHS = {RWS_STATUS_PAYLOAD_LEN, RWS_TELEMETRY_PAYLOAD_LEN}
IGNORED_REPLY_PAYLOAD_LENGTHS = {TEMPERATURE_REPORT_PAYLOAD_LEN, UGV_TELEMETRY_REPORT_PAYLOAD_LEN}
DEFAULT_SRC_IP = "192.168.88.33"
DEFAULT_SRC_PORT = 7770
DEFAULT_DST_IP = "192.168.88.56"
DEFAULT_DST_PORT = 7780
PERIOD_MS = 50.0
DEFAULT_TIMEOUT_MS = 5000
SEQUENCE_MODULUS = 0x10000
SEQUENCE_HALF_RANGE = SEQUENCE_MODULUS // 2
MAX_SEQUENCE_TRACKING_GAP = 0xFFFF // 16
MAX_UNEXPECTED_COMMAND_REPLIES = 256


def encode_unit_axis_to_packet_s16(value: float | None) -> int:
    # The reference motion model uses rotV / eleV as normalized velocity commands
    # in the range [-1.0, 1.0]. The packet carries them as signed 16-bit integers.
    if value is None:
        return 0
    return max(-0x8000, min(0x7FFF, int(value * 0x7FFF)))


def decode_packet_axis_s16_to_unit(value: int) -> float:
    return float(value) / 0x7FFF


def encode_angle_rad_to_packet_s32(value: float | None) -> int:
    # The reference motion model uses rotP / eleP as angular position targets in radians.
    # The packet carries them as signed 32-bit integers on the same scale as +/-pi.
    if value is None:
        return 0
    return max(-0x80000000, min(0x7FFFFFFF, int(value / math.pi * 0x7FFFFFFF)))


def decode_packet_angle_s32_to_rad(value: int) -> float:
    return float(value * math.pi) / 0x7FFFFFFF


def format_unit_percent(value: float | None, compact: bool = False) -> str:
    if value is None:
        return "-" if compact else "  -  "
    text = f"{int(value * 100.0):+4d}%"
    return text.strip() if compact else text


def format_angle_degrees(value: float | None, compact: bool = False) -> str:
    if value is None:
        return "-" if compact else "  -  "
    text = f"{round(math.degrees(value)):+4d}°"
    return text.strip() if compact else text


def format_axis_packet_s16(value: int, valid: bool = True) -> str:
    decoded = decode_packet_axis_s16_to_unit(value) if valid else None
    return f"0x{value & 0xFFFF:04x}[{format_unit_percent(decoded, compact=True)}]"


def format_angle_packet_s32(value: int, valid: bool = True) -> str:
    decoded = decode_packet_angle_s32_to_rad(value) if valid else None
    return f"0x{value & 0xFFFFFFFF:08x}[{format_angle_degrees(decoded, compact=True)}]"


# Fire timing stays capture-derived because those bytes are pinned down by captures.
RWS_PACKET_TYPE = 0x01
TRANSPORT_PAD0 = 0x00
# The packet stores rotP / eleP as signed int32 fields.
# The integer is the raw wire container first; the semantic meaning comes from the
# sender contract. In the reference movement model used here, these fields carry
# encoded angular targets.
# Command byte 4 (flags1) carries axis-unit mode toggles.
FLAGS1_ENABLE = 0x01
FLAGS1_SLOW = 0x02
FLAGS1_RELOAD = 0x04
FLAGS1_FORCE_HOME = 0x08
# Command byte 5 (flags2) carries validity selectors for rotV/eleV/rotP/eleP.
FLAGS2_ROTATION_V = 0x01
FLAGS2_ELEVATION_V = 0x02
FLAGS2_ROTATION_P = 0x04
FLAGS2_ELEVATION_P = 0x08
# The reference sender sets both vel-priority bits together.
# When velocity and position targets are both present, velPrio means that the receiver
# should obey rotV / eleV first and treat rotP / eleP as secondary guidance.
FLAGS2_VEL_PRIO_LOW = 0x10
FLAGS2_VEL_PRIO_HIGH = 0x20
FLAGS2_VEL_PRIO = FLAGS2_VEL_PRIO_LOW | FLAGS2_VEL_PRIO_HIGH
# Reply byte 5 (status flags1) only has two confirmed validity bits in the reference stack.
RWS_STATUS_FLAGS1_ROTATION_P_VALID = 0x04
RWS_STATUS_FLAGS1_ELEVATION_P_VALID = 0x08
COMMAND_BODY_LEN = 36
# Fire timing stays capture-derived because those bytes are pinned down by captures.
DEFAULT_FIRE_DURATION_SHORT = 161
DEFAULT_FIRE_DURATION_MEDIUM = 605
# Command bytes 6 and 7 (flags3/flags4) are currently reserved and stay zero in captures.
FIRE_MODE_SHORT = "short"
FIRE_MODE_MEDIUM = "medium"
FIRE_MODE_MANUAL = "manual"
FIRE_MODE_DURATIONS = {
    FIRE_MODE_SHORT: DEFAULT_FIRE_DURATION_SHORT,
    FIRE_MODE_MEDIUM: DEFAULT_FIRE_DURATION_MEDIUM,
    FIRE_MODE_MANUAL: 0,
}


WireBytes2 = ctypes.c_uint8 * 2
WireBytes4 = ctypes.c_uint8 * 4


class WireStruct(ctypes.BigEndianStructure):
    # BigEndianStructure keeps field order byte-accurate with the wire payload.
    _pack_ = 1

    @classmethod
    def byte_size(cls) -> int:
        return ctypes.sizeof(cls)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "WireStruct":
        if len(raw) != ctypes.sizeof(cls):
            raise ValueError(f"{cls.__name__}: expected {ctypes.sizeof(cls)} bytes, got {len(raw)}")
        return cls.from_buffer_copy(raw)

    def to_bytes(self) -> bytes:
        return bytes(memoryview(self))


class RwsCommandWire(WireStruct):
    # Full 40-byte command payload: transport header + command body + 4-byte digest.
    # flags1..flags4 match the reference control packet layout.
    # Byte 4: enable/slow/reload/forceHome. Byte 5: rotV/eleV/rotP/eleP validity + velPrio.
    # Bytes 6 and 7 are reserved in the current reference traffic.
    _fields_ = [
        ("packet_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("flags1", ctypes.c_uint8),
        ("flags2", ctypes.c_uint8),
        ("flags3", ctypes.c_uint8),
        ("flags4", ctypes.c_uint8),
        ("rotation_v", ctypes.c_int16),
        ("elevation_v", ctypes.c_int16),
        ("rotation_p", ctypes.c_int32),
        ("elevation_p", ctypes.c_int32),
        ("arm", WireBytes4),
        ("fire", WireBytes2),
        ("fire_duration", ctypes.c_uint16),
        ("cameras_p", ctypes.c_int32),
        ("rangefinder_seq", ctypes.c_uint8),
        ("fire_seq", ctypes.c_uint8),
        ("reserved_tail", WireBytes2),
        ("checksum", WireBytes4),
    ]


class RwsReplyWire(WireStruct):
    # Full 32-byte reply payload: transport header + reply body + 4-byte digest.
    # The reference code exposes the first four reply body bytes only as flags0..flags3,
    # so the per-bit meaning is intentionally kept raw here as well.
    _fields_ = [
        ("packet_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("flags0", ctypes.c_uint8),
        ("flags1", ctypes.c_uint8),
        ("flags2", ctypes.c_uint8),
        ("flags3", ctypes.c_uint8),
        ("rotation_p", ctypes.c_int32),
        ("elevation_p", ctypes.c_int32),
        ("cameras_p", ctypes.c_int32),
        ("distance_mm", ctypes.c_uint32),
        ("shots", ctypes.c_uint16),
        ("rangefinder_seq", ctypes.c_uint8),
        ("fire_seq", ctypes.c_uint8),
        ("checksum", WireBytes4),
    ]


class RwsTelemetryWire(WireStruct):
    # Full 36-byte telemetry payload: transport header + RWS telemetry body + digest.
    _fields_ = [
        ("packet_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("flags0", ctypes.c_uint8),
        ("flags1", ctypes.c_uint8),
        ("flags2", ctypes.c_uint8),
        ("flags3", ctypes.c_uint8),
        ("rpm_x", ctypes.c_int16),
        ("voltage_x", ctypes.c_int16),
        ("amperage_x", ctypes.c_int16),
        ("temperature_x", ctypes.c_int16),
        ("rpm_y", ctypes.c_int16),
        ("voltage_y", ctypes.c_int16),
        ("amperage_y", ctypes.c_int16),
        ("temperature_y", ctypes.c_int16),
        ("voltage_bat", ctypes.c_int16),
        ("voltage_fire", ctypes.c_int16),
        ("voltage_cpu", ctypes.c_int16),
        ("battery_percent", ctypes.c_uint16),
        ("checksum", WireBytes4),
    ]


# Packet layout used by this file:
# - transport header: packet_type, pad0, sequence
# - authenticated command/reply body fields
# - trailing digest: 4 bytes from sha256(header + body + salt)[:4]
# That means commands are 40 bytes total with a 36-byte authenticated prefix,
# and immediate replies are 32 bytes total with a 28-byte authenticated prefix.
class PendingReplyEntry(Protocol):
    sequence: int
    reply_lengths: set[int]


PendingReplyEntryT = TypeVar("PendingReplyEntryT", bound=PendingReplyEntry)


def sequence_distance(received_sequence: int, expected_sequence: int) -> int:
    return (
        (received_sequence - expected_sequence + SEQUENCE_HALF_RANGE) % SEQUENCE_MODULUS
    ) - SEQUENCE_HALF_RANGE


def match_pending_reply(
    pending_entries: deque[PendingReplyEntryT],
    received_sequence: int,
    reply_length: int,
) -> PendingReplyEntryT | None:
    while pending_entries:
        entry = pending_entries[0]
        delta = sequence_distance(received_sequence, entry.sequence)
        if delta == 0:
            entry.reply_lengths.add(reply_length)
            pending_entries.popleft()
            return entry
        if delta < 0 or abs(delta) > MAX_SEQUENCE_TRACKING_GAP:
            return None
        pending_entries.popleft()
    return None


@dataclass
class RwsPendingReply:
    sequence: int
    reply_lengths: set[int] = field(default_factory=set)

    def is_complete(self) -> bool:
        return self.reply_lengths >= TRACKED_RWS_REPLY_LENGTHS


@dataclass
class RwsReplyTracker:
    sent_packets: int = 0
    complete_replies: int = 0
    pending_packets: int = 0
    last_rx_monotonic: float | None = None
    last_rws_reply_monotonic: float | None = None
    unexpected_command_reply_count: int = 0
    pending_by_length: dict[int, deque[RwsPendingReply]] = field(
        default_factory=lambda: {length: deque() for length in TRACKED_RWS_REPLY_LENGTHS}
    )
    unexpected_command_replies: deque[tuple[int, int]] = field(
        default_factory=lambda: deque(maxlen=MAX_UNEXPECTED_COMMAND_REPLIES)
    )

    def record_send_sequence(self, sequence: int) -> None:
        self.sent_packets += 1
        self.pending_packets += 1
        entry = RwsPendingReply(sequence=sequence)
        for reply_length in TRACKED_RWS_REPLY_LENGTHS:
            self.pending_by_length[reply_length].append(entry)

    def record_send(self, packet: "CommandPacket") -> None:
        self.record_send_sequence(packet.sequence)

    def record(self, data: bytes) -> None:
        length = len(data)
        now = time.monotonic()
        self.last_rx_monotonic = now
        if length not in TRACKED_RWS_REPLY_LENGTHS or len(data) < 4:
            return

        self.last_rws_reply_monotonic = now

        sequence = int.from_bytes(data[2:4], "big")
        pending = self.pending_by_length[length]
        matched_entry = match_pending_reply(pending, sequence, length)
        if matched_entry is None:
            self.unexpected_command_reply_count += 1
            self.unexpected_command_replies.append((sequence, length))
            return

        if matched_entry.is_complete():
            self.complete_replies += 1
            self.pending_packets -= 1

    def summary_lines(self) -> list[str]:
        lines = [
            "Interactive reply summary",
            f"  packets sent: {self.sent_packets}",
            f"  packets with both 32/36-byte RWS RX: {self.complete_replies}",
            f"  packets still missing tracked 32/36-byte RWS RX: {self.pending_packets}",
        ]
        if self.unexpected_command_reply_count:
            lines.append(
                "  unexpected command replies without a pending matching send: "
                f"{self.unexpected_command_reply_count}"
            )
        return lines

    def summary_text(self) -> str:
        return "\n".join(self.summary_lines())

    @staticmethod
    def _age_text(last_monotonic: float | None, now: float) -> str:
        if last_monotonic is None:
            return "-"
        return f"{int((now - last_monotonic) * 1000)}ms"

    def describe_connection(self, now: float) -> str:
        timeout_seconds = DEFAULT_TIMEOUT_MS / 1000.0
        if self.last_rx_monotonic is None:
            return "waiting for RX"

        link_state = "online" if (now - self.last_rx_monotonic) <= timeout_seconds else "stale"
        return (
            f"{link_state} rxAge={self._age_text(self.last_rx_monotonic, now)} "
            f"rwsAge={self._age_text(self.last_rws_reply_monotonic, now)}"
        )


def open_bound_socket(bind_ip: str, bind_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((bind_ip, bind_port))
    except OSError as exc:
        sock.close()
        raise RuntimeError(
            f"Could not bind UDP socket to {bind_ip}:{bind_port}. "
            "Check that this IP is configured locally and that the real controller is not still bound to the same port."
        ) from exc
    sock.setblocking(False)
    return sock


def send_datagram(sock: socket.socket, dst_ip: str, dst_port: int, payload: bytes) -> None:
    sock.sendto(payload, (dst_ip, dst_port))


def recv_available_datagrams(sock: socket.socket, max_size: int = 4096) -> list[tuple[bytes, tuple[str, int]]]:
    datagrams: list[tuple[bytes, tuple[str, int]]] = []
    while True:
        try:
            data, addr = sock.recvfrom(max_size)
        except BlockingIOError:
            break
        datagrams.append((data, addr))
    return datagrams


@dataclass(frozen=True)
class RwsTransportEvent:
    kind: str
    data: bytes
    addr: tuple[str, int]
    message: str | None = None


class RwsControlChannel:
    def __init__(
        self,
        bind_ip: str,
        bind_port: int,
        dst_ip: str,
        dst_port: int,
        tracked_reply_lengths: set[int] | None = None,
        ignored_reply_lengths: set[int] | None = None,
        max_datagram_size: int = 4096,
    ) -> None:
        self.bind_ip = bind_ip
        self.bind_port = bind_port
        self.dst_ip = dst_ip
        self.dst_port = dst_port
        self.tracked_reply_lengths = (
            set(TRACKED_RWS_REPLY_LENGTHS) if tracked_reply_lengths is None else set(tracked_reply_lengths)
        )
        self.ignored_reply_lengths = (
            set(IGNORED_REPLY_PAYLOAD_LENGTHS) if ignored_reply_lengths is None else set(ignored_reply_lengths)
        )
        self.max_datagram_size = max_datagram_size
        self._sock: socket.socket | None = None

    def __enter__(self) -> "RwsControlChannel":
        return self.open()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def open(self) -> "RwsControlChannel":
        if self._sock is None:
            self._sock = open_bound_socket(self.bind_ip, self.bind_port)
        return self

    def close(self) -> None:
        if self._sock is None:
            return
        self._sock.close()
        self._sock = None

    def send_payload(self, payload: bytes) -> None:
        if self._sock is None:
            raise RuntimeError("transport is not open")
        send_datagram(self._sock, self.dst_ip, self.dst_port, payload)

    def send_command(self, packet: "CommandPacket") -> None:
        self.send_payload(packet.to_bytes())

    def poll_events(self, timeout_seconds: float = 0.0) -> list[RwsTransportEvent]:
        if self._sock is None:
            return []
        if timeout_seconds < 0:
            timeout_seconds = 0.0

        readable, _, _ = select.select([self._sock], [], [], timeout_seconds)
        if not readable:
            return []

        events: list[RwsTransportEvent] = []
        for data, addr in recv_available_datagrams(self._sock, max_size=self.max_datagram_size):
            if addr[0] != self.dst_ip:
                events.append(
                    RwsTransportEvent(
                        kind="unexpected-source",
                        data=data,
                        addr=addr,
                        message=f"recv len={len(data)} from unexpected {addr[0]}:{addr[1]}",
                    )
                )
                continue
            if len(data) in self.ignored_reply_lengths:
                continue
            if len(data) not in self.tracked_reply_lengths:
                events.append(
                    RwsTransportEvent(
                        kind="unexpected-length",
                        data=data,
                        addr=addr,
                        message=f"recv len={len(data)} from {addr[0]}:{addr[1]} (unexpected payload length)",
                    )
                )
                continue

            events.append(RwsTransportEvent(kind="reply", data=data, addr=addr))
        return events


def wrap_prefixed(prefix: str, text: str, width: int) -> list[str]:
    if width <= len(prefix):
        width = len(prefix) + 1
    wrapped = textwrap.wrap(
        text,
        width=width - len(prefix),
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not wrapped:
        return [prefix]
    lines = [prefix + wrapped[0]]
    continuation_prefix = " " * len(prefix)
    lines.extend(continuation_prefix + chunk for chunk in wrapped[1:])
    return lines


def describe_flags1(value: int) -> str:
    labels: list[str] = []
    if value & FLAGS1_ENABLE:
        labels.append("enable")
    if value & FLAGS1_SLOW:
        labels.append("slow")
    if value & FLAGS1_RELOAD:
        labels.append("reload")
    if value & FLAGS1_FORCE_HOME:
        labels.append("forceHome")
    return ",".join(labels) if labels else "none"


def describe_flags2(value: int) -> str:
    labels: list[str] = []
    if value & FLAGS2_ROTATION_V:
        labels.append("rotV")
    if value & FLAGS2_ELEVATION_V:
        labels.append("eleV")
    if value & FLAGS2_ROTATION_P:
        labels.append("rotP")
    if value & FLAGS2_ELEVATION_P:
        labels.append("eleP")
    velocity_priority_bits = value & FLAGS2_VEL_PRIO
    if velocity_priority_bits == FLAGS2_VEL_PRIO:
        labels.append("velPrio")
    elif velocity_priority_bits == FLAGS2_VEL_PRIO_LOW:
        labels.append("velPrioBit4")
    elif velocity_priority_bits == FLAGS2_VEL_PRIO_HIGH:
        labels.append("velPrioBit5")
    return ",".join(labels) if labels else "none"


def describe_arm_bytes(value: bytes) -> str:
    if value == b"\x00\x00\x00\x00":
        return "off"
    if value[:1] == b"A":
        return "ARM"
    return value.hex()


def describe_fire_bytes(value: bytes) -> str:
    if value == b"\x00\x00":
        return "idle"
    if value[:1] == b"F":
        return "fire"
    return value.hex()


def describe_fire_mode(mode: str) -> str:
    duration = FIRE_MODE_DURATIONS[mode]
    if mode == FIRE_MODE_MANUAL:
        return "manual(duration=0, hold space)"
    return f"{mode}(duration={duration})"


@dataclass(frozen=True)
class CommandPacket:
    name: str
    sequence: int
    flags1: int
    flags2: int
    flags3: int
    flags4: int
    rotation_v: int
    elevation_v: int
    rotation_p: int
    elevation_p: int
    arm: bytes
    fire: bytes
    fire_duration: int
    cameras_p: int
    rangefinder_seq: int
    fire_seq: int
    checksum: bytes

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.sequence > 0xFFFF:
            raise ValueError(f"{self.name}: sequence must be in range 0..0xffff")
        for field_name in ("flags1", "flags2", "flags3", "flags4"):
            value = getattr(self, field_name)
            if value < 0 or value > 0xFF:
                raise ValueError(f"{self.name}: {field_name} must be in range 0..255")
        if self.rotation_v < -0x8000 or self.rotation_v > 0x7FFF:
            raise ValueError(f"{self.name}: rotation_v must fit signed 16-bit")
        if self.elevation_v < -0x8000 or self.elevation_v > 0x7FFF:
            raise ValueError(f"{self.name}: elevation_v must fit signed 16-bit")
        if self.rotation_p < -0x80000000 or self.rotation_p > 0x7FFFFFFF:
            raise ValueError(f"{self.name}: rotation_p must fit signed 32-bit")
        if self.elevation_p < -0x80000000 or self.elevation_p > 0x7FFFFFFF:
            raise ValueError(f"{self.name}: elevation_p must fit signed 32-bit")
        if len(self.arm) != 4:
            raise ValueError(f"{self.name}: arm must be 4 bytes")
        if len(self.fire) != 2:
            raise ValueError(f"{self.name}: fire must be 2 bytes")
        if self.fire_duration < 0 or self.fire_duration > 0xFFFF:
            raise ValueError(f"{self.name}: fire_duration must be in range 0..65535")
        if self.cameras_p < -0x80000000 or self.cameras_p > 0x7FFFFFFF:
            raise ValueError(f"{self.name}: cameras_p must fit signed 32-bit")
        if self.rangefinder_seq < 0 or self.rangefinder_seq > 0xFF:
            raise ValueError(f"{self.name}: rangefinder_seq must be in range 0..255")
        if self.fire_seq < 0 or self.fire_seq > 0xFF:
            raise ValueError(f"{self.name}: fire_seq must be in range 0..255")
        if len(self.checksum) != 4:
            raise ValueError(f"{self.name}: checksum must be 4 bytes")

    def body_bytes(self) -> bytes:
        wire = RwsCommandWire(
            packet_type=RWS_PACKET_TYPE,
            pad0=TRANSPORT_PAD0,
            sequence=self.sequence,
            flags1=self.flags1,
            flags2=self.flags2,
            flags3=self.flags3,
            flags4=self.flags4,
            rotation_v=self.rotation_v,
            elevation_v=self.elevation_v,
            rotation_p=self.rotation_p,
            elevation_p=self.elevation_p,
            arm=WireBytes4(*self.arm),
            fire=WireBytes2(*self.fire),
            fire_duration=self.fire_duration,
            cameras_p=self.cameras_p,
            rangefinder_seq=self.rangefinder_seq,
            fire_seq=self.fire_seq,
            reserved_tail=WireBytes2(0x00, 0x00),
            checksum=WireBytes4(*self.checksum),
        )
        return wire.to_bytes()[:COMMAND_BODY_LEN]

    def to_bytes(self) -> bytes:
        wire = RwsCommandWire(
            packet_type=RWS_PACKET_TYPE,
            pad0=TRANSPORT_PAD0,
            sequence=self.sequence,
            flags1=self.flags1,
            flags2=self.flags2,
            flags3=self.flags3,
            flags4=self.flags4,
            rotation_v=self.rotation_v,
            elevation_v=self.elevation_v,
            rotation_p=self.rotation_p,
            elevation_p=self.elevation_p,
            arm=WireBytes4(*self.arm),
            fire=WireBytes2(*self.fire),
            fire_duration=self.fire_duration,
            cameras_p=self.cameras_p,
            rangefinder_seq=self.rangefinder_seq,
            fire_seq=self.fire_seq,
            reserved_tail=WireBytes2(0x00, 0x00),
            checksum=WireBytes4(*self.checksum),
        )
        return wire.to_bytes()

    def raw_hex(self) -> str:
        return self.to_bytes().hex()

    def summary_lines(self) -> tuple[str, str, str]:
        rotation_v_valid = bool(self.flags2 & FLAGS2_ROTATION_V)
        elevation_v_valid = bool(self.flags2 & FLAGS2_ELEVATION_V)
        rotation_p_valid = bool(self.flags2 & FLAGS2_ROTATION_P)
        elevation_p_valid = bool(self.flags2 & FLAGS2_ELEVATION_P)
        rotation_v_text = format_unit_percent(
            decode_packet_axis_s16_to_unit(self.rotation_v) if rotation_v_valid else None,
            compact=True,
        )
        elevation_v_text = format_unit_percent(
            decode_packet_axis_s16_to_unit(self.elevation_v) if elevation_v_valid else None,
            compact=True,
        )
        rotation_p_text = format_angle_degrees(
            decode_packet_angle_s32_to_rad(self.rotation_p) if rotation_p_valid else None,
            compact=True,
        )
        elevation_p_text = format_angle_degrees(
            decode_packet_angle_s32_to_rad(self.elevation_p) if elevation_p_valid else None,
            compact=True,
        )
        return (
            f"seq=0x{self.sequence:04x} "
            f"flags1={self.flags1:02x}[{describe_flags1(self.flags1)}] "
            f"flags2={self.flags2:02x}[{describe_flags2(self.flags2)}] "
            f"flags3={self.flags3:02x} flags4={self.flags4:02x}",
            f"rotV=0x{self.rotation_v & 0xFFFF:04x}[{rotation_v_text}] "
            f"eleV=0x{self.elevation_v & 0xFFFF:04x}[{elevation_v_text}] "
            f"rotP=0x{self.rotation_p & 0xFFFFFFFF:08x}[{rotation_p_text}] "
            f"eleP=0x{self.elevation_p & 0xFFFFFFFF:08x}[{elevation_p_text}]",
            f"arm={self.arm.hex()}[{describe_arm_bytes(self.arm)}] "
            f"fire={self.fire.hex()}[{describe_fire_bytes(self.fire)}] "
            f"fireDuration={self.fire_duration} "
            f"fireSeq={self.fire_seq}",
        )

    def summary(self) -> str:
        return " ".join(self.summary_lines())


def compute_command_checksum(body: bytes, salt: bytes) -> bytes:
    return hashlib.sha256(body + salt).digest()[:4]


def build_generated_command_packet(
    name: str,
    sequence: int,
    flags1: int,
    flags2: int,
    flags3: int,
    flags4: int,
    rotation_v: int,
    elevation_v: int,
    rotation_p: int,
    elevation_p: int,
    arm: bytes,
    fire: bytes,
    fire_duration: int,
    cameras_p: int,
    rangefinder_seq: int,
    fire_seq: int,
    salt: bytes,
) -> CommandPacket:
    packet = CommandPacket(
        name=name,
        sequence=sequence,
        flags1=flags1,
        flags2=flags2,
        flags3=flags3,
        flags4=flags4,
        rotation_v=rotation_v,
        elevation_v=elevation_v,
        rotation_p=rotation_p,
        elevation_p=elevation_p,
        arm=arm,
        fire=fire,
        fire_duration=fire_duration,
        cameras_p=cameras_p,
        rangefinder_seq=rangefinder_seq,
        fire_seq=fire_seq,
        checksum=b"\x00\x00\x00\x00",
    )
    return replace(packet, checksum=compute_command_checksum(packet.body_bytes(), salt))
