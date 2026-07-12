"""Unit tests for the Benewake TF03-180 serial frame parser.

Pure-function tests — no serial port is opened. The reader thread that consumes
this parser is only started when RANGEFINDER_ENABLED is set (Jetson only).
"""

from __future__ import annotations

from app.turret import parse_tf03_frame


def _frame(distance_cm: int, strength: int = 100, temp: int = 0) -> bytes:
    """Build a valid 9-byte TF03 frame with a correct checksum."""
    body = bytes(
        [
            0x59,
            0x59,
            distance_cm & 0xFF,
            (distance_cm >> 8) & 0xFF,
            strength & 0xFF,
            (strength >> 8) & 0xFF,
            temp & 0xFF,
            (temp >> 8) & 0xFF,
        ]
    )
    return body + bytes([sum(body) & 0xFF])


def test_valid_frame_returns_mm():
    # 1234 cm -> 12340 mm.
    assert parse_tf03_frame(_frame(1234)) == 12340


def test_short_range_frame():
    assert parse_tf03_frame(_frame(50)) == 500


def test_wrong_length_returns_none():
    assert parse_tf03_frame(b"\x59\x59\x00") is None
    assert parse_tf03_frame(_frame(100) + b"\x00") is None


def test_bad_header_returns_none():
    frame = bytearray(_frame(100))
    frame[0] = 0x58
    assert parse_tf03_frame(bytes(frame)) is None


def test_bad_checksum_returns_none():
    frame = bytearray(_frame(100))
    frame[8] ^= 0xFF  # corrupt the checksum byte
    assert parse_tf03_frame(bytes(frame)) is None


def test_zero_distance_returns_none():
    # Out of range / no target: TF03 reports distance 0.
    assert parse_tf03_frame(_frame(0)) is None
