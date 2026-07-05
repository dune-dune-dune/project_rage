"""
Async RWS UDP driver + low-level protocol helpers.

Extracted from rws_control.py — only the pieces the bridge needs:
  - Wire structs (ctypes BigEndian)
  - Command packet builder + SHA-256 checksum
  - Reply/telemetry parsers
  - Async UDP driver via asyncio.DatagramProtocol
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

RWS_PACKET_TYPE = 0x01
TRANSPORT_PAD0  = 0x00

FLAGS1_ENABLE     = 0x01
FLAGS1_SLOW       = 0x02
FLAGS1_RELOAD     = 0x04
FLAGS1_FORCE_HOME = 0x08

FLAGS2_ROTATION_V  = 0x01
FLAGS2_ELEVATION_V = 0x02
FLAGS2_ROTATION_P  = 0x04
FLAGS2_ELEVATION_P = 0x08
FLAGS2_VEL_PRIO    = 0x30   # both bits 4 and 5

RWS_STATUS_PAYLOAD_LEN    = 32
RWS_TELEMETRY_PAYLOAD_LEN = 36
SEQUENCE_MODULUS           = 0x10000
COMMAND_BODY_LEN           = 36

RWS_STATUS_FLAGS1_ROTATION_P_VALID  = 0x04
RWS_STATUS_FLAGS1_ELEVATION_P_VALID = 0x08

# telemetry flags0 bits — verified from research/reverse_protocol/unit_protocol.md
TELEM_FLAGS0_RWS_ACTIVE  = 0x01
TELEM_FLAGS0_FIRE_PULSE  = 0x02

FIRE_DURATION_SHORT  = 161
FIRE_DURATION_MEDIUM = 605


# ── Wire structs ───────────────────────────────────────────────────────────────

class _WireBase(ctypes.BigEndianStructure):
    _pack_ = 1

    @classmethod
    def from_raw(cls, raw: bytes) -> "_WireBase":
        if len(raw) != ctypes.sizeof(cls):
            raise ValueError(f"{cls.__name__}: expected {ctypes.sizeof(cls)} bytes, got {len(raw)}")
        return cls.from_buffer_copy(raw)

    def to_bytes(self) -> bytes:
        return bytes(memoryview(self))


_Bytes2 = ctypes.c_uint8 * 2
_Bytes4 = ctypes.c_uint8 * 4


class RwsCommandWire(_WireBase):
    _fields_ = [
        ("packet_type",    ctypes.c_uint8),
        ("pad0",           ctypes.c_uint8),
        ("sequence",       ctypes.c_uint16),
        ("flags1",         ctypes.c_uint8),
        ("flags2",         ctypes.c_uint8),
        ("flags3",         ctypes.c_uint8),
        ("flags4",         ctypes.c_uint8),
        ("rotation_v",     ctypes.c_int16),
        ("elevation_v",    ctypes.c_int16),
        ("rotation_p",     ctypes.c_int32),
        ("elevation_p",    ctypes.c_int32),
        ("arm",            _Bytes4),
        ("fire",           _Bytes2),
        ("fire_duration",  ctypes.c_uint16),
        ("cameras_p",      ctypes.c_int32),
        ("rangefinder_seq",ctypes.c_uint8),
        ("fire_seq",       ctypes.c_uint8),
        ("reserved_tail",  _Bytes2),
        ("checksum",       _Bytes4),
    ]


class RwsReplyWire(_WireBase):
    _fields_ = [
        ("packet_type", ctypes.c_uint8),
        ("pad0",        ctypes.c_uint8),
        ("sequence",    ctypes.c_uint16),
        ("flags0",      ctypes.c_uint8),
        ("flags1",      ctypes.c_uint8),
        ("flags2",      ctypes.c_uint8),
        ("flags3",      ctypes.c_uint8),
        ("rotation_p",  ctypes.c_int32),
        ("elevation_p", ctypes.c_int32),
        ("cameras_p",   ctypes.c_int32),
        ("distance_mm", ctypes.c_uint32),
        ("shots",       ctypes.c_uint16),
        ("rangefinder_seq", ctypes.c_uint8),
        ("fire_seq",    ctypes.c_uint8),
        ("checksum",    _Bytes4),
    ]


class RwsTelemetryWire(_WireBase):
    _fields_ = [
        ("packet_type",   ctypes.c_uint8),
        ("pad0",          ctypes.c_uint8),
        ("sequence",      ctypes.c_uint16),
        ("flags0",        ctypes.c_uint8),   # rws_active | fire_pulse_active
        ("flags1",        ctypes.c_uint8),
        ("flags2",        ctypes.c_uint8),   # x_status_flags
        ("flags3",        ctypes.c_uint8),   # y_status_flags
        ("rpm_x",         ctypes.c_int16),
        ("voltage_x",     ctypes.c_int16),
        ("amperage_x",    ctypes.c_int16),
        ("temperature_x", ctypes.c_int16),
        ("rpm_y",         ctypes.c_int16),
        ("voltage_y",     ctypes.c_int16),
        ("amperage_y",    ctypes.c_int16),
        ("temperature_y", ctypes.c_int16),
        ("voltage_bat",   ctypes.c_int16),
        ("voltage_fire",  ctypes.c_int16),
        ("voltage_cpu",   ctypes.c_int16),
        ("battery_percent", ctypes.c_uint16),
        ("checksum",      _Bytes4),
    ]


# ── Command builder ────────────────────────────────────────────────────────────

def _compute_checksum(body: bytes, salt: bytes) -> bytes:
    return hashlib.sha256(body + salt).digest()[:4]


def build_rws_packet(
    *,
    salt: bytes,
    sequence: int,
    flags1: int,
    flags2: int,
    rotation_v: int,
    elevation_v: int,
    arm: bytes,          # 4 bytes: b"\x00\x00\x00\x00" or b"A\x00\x00\x00"
    fire: bytes,         # 2 bytes: b"\x00\x00" or b"F\x00"
    fire_duration: int,
    fire_seq: int,
) -> bytes:
    wire = RwsCommandWire(
        packet_type=RWS_PACKET_TYPE,
        pad0=TRANSPORT_PAD0,
        sequence=sequence,
        flags1=flags1,
        flags2=flags2,
        flags3=0,
        flags4=0,
        rotation_v=rotation_v,
        elevation_v=elevation_v,
        rotation_p=0,
        elevation_p=0,
        arm=_Bytes4(*arm),
        fire=_Bytes2(*fire),
        fire_duration=fire_duration,
        cameras_p=0,
        rangefinder_seq=0,
        fire_seq=fire_seq,
        reserved_tail=_Bytes2(0, 0),
        checksum=_Bytes4(0, 0, 0, 0),
    )
    body = wire.to_bytes()[:COMMAND_BODY_LEN]
    checksum = _compute_checksum(body, salt)
    wire.checksum = _Bytes4(*checksum)
    return wire.to_bytes()


# ── Async UDP driver ───────────────────────────────────────────────────────────

class _RwsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: Callable[[bytes], None]) -> None:
        self._on_datagram = on_datagram
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._on_datagram(data)
        except Exception:
            logger.exception("Error in datagram handler")

    def error_received(self, exc: Exception) -> None:
        logger.error("UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.warning("UDP connection lost: %s", exc)


class RwsDriver:
    def __init__(
        self,
        bind_ip: str,
        bind_port: int,
        dst_ip: str,
        dst_port: int,
    ) -> None:
        self._bind = (bind_ip, bind_port)
        self._dst = (dst_ip, dst_port)
        self._protocol: Optional[_RwsUdpProtocol] = None
        self._last_status_ts: Optional[float] = None
        self._last_telem_ts: Optional[float] = None

        # Parsed snapshot (updated from replies)
        self.reply: Optional[RwsReplyWire] = None
        self.telemetry: Optional[RwsTelemetryWire] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        _, proto = await loop.create_datagram_endpoint(
            lambda: _RwsUdpProtocol(self._on_datagram),
            local_addr=self._bind,
            remote_addr=self._dst,
        )
        self._protocol = proto  # type: ignore[assignment]
        logger.info("RWS UDP bound %s → %s", self._bind, self._dst)

    def send(self, payload: bytes) -> None:
        if self._protocol and self._protocol.transport:
            self._protocol.transport.sendto(payload)

    def _on_datagram(self, data: bytes) -> None:
        length = len(data)
        now = time.monotonic()
        if length == RWS_STATUS_PAYLOAD_LEN:
            try:
                self.reply = RwsReplyWire.from_raw(data)
                self._last_status_ts = now
            except Exception:
                logger.warning("Failed to parse RWS status reply")
        elif length == RWS_TELEMETRY_PAYLOAD_LEN:
            try:
                self.telemetry = RwsTelemetryWire.from_raw(data)
                self._last_telem_ts = now
            except Exception:
                logger.warning("Failed to parse RWS telemetry reply")
        # other lengths are silently ignored (temperature / UGV telemetry)

    def last_rx_age(self, now: float) -> Optional[float]:
        ts = self._last_status_ts or self._last_telem_ts
        return (now - ts) if ts is not None else None
