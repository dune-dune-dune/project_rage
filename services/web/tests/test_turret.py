"""Core packet-building safety behaviour of the TurretController."""

from __future__ import annotations

import time

_ARM_OFF = b"\x00\x00\x00\x00"
_ARM_ON = b"A\x00\x00\x00"
_FIRE_OFF = b"\x00\x00"
_FIRE_ON = b"F\x00"


def test_deadman_produces_neutral_packet(controller):
    # No input has ever arrived -> the deadman is expired -> neutral packet.
    assert controller._read_intent(time.monotonic()) is None
    packet = controller._neutral_packet()
    assert packet.flags1 == 0
    assert packet.rotation_v == 0 and packet.elevation_v == 0
    assert packet.arm == _ARM_OFF and packet.fire == _FIRE_OFF


def test_fire_blocked_while_safety_engaged(controller):
    controller.apply_input({"fire": True, "safety": False})
    now = time.monotonic()
    packet = controller._build_packet(controller._read_intent(now), (False, 0.0, 0.0), now)
    assert packet.fire == _FIRE_OFF
    assert packet.arm == _ARM_OFF


def test_fire_active_when_safety_off_and_fire_held(controller):
    controller.apply_input({"fire": True, "safety": True})
    now = time.monotonic()
    packet = controller._build_packet(controller._read_intent(now), (False, 0.0, 0.0), now)
    assert packet.fire == _FIRE_ON
    assert packet.arm == _ARM_ON


def test_manual_motion_available_regardless_of_safety(controller):
    # Movement is never gated by the safety toggle.
    controller.apply_input({"right": True, "safety": False})
    now = time.monotonic()
    packet = controller._build_packet(controller._read_intent(now), (False, 0.0, 0.0), now)
    assert packet.rotation_v > 0
