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


def test_hold_packet_keeps_motors_energized_but_inert(controller):
    # A brief input gap holds position with ENABLE on and no motion/fire — the
    # motors stay energized so an unstable link does not sag the aim.
    now = time.monotonic()
    packet = controller._hold_packet(now)
    assert packet.flags1 != 0  # ENABLE stays set (position hold)
    assert packet.rotation_v == 0 and packet.elevation_v == 0
    assert packet.arm == _ARM_OFF and packet.fire == _FIRE_OFF


def test_two_stage_deadman_holds_then_neutralises(controller):
    controller.apply_input({"right": True})
    now = time.monotonic()
    # Within the motion deadman: normal drive.
    assert controller._read_intent(now) is not None
    # Past the motion deadman but within the failsafe: hold (not yet expired).
    stale = now + controller._s.deadman_seconds + 0.01
    assert controller._read_intent(stale) is None
    assert controller._input_expired(stale) is False
    # Past the failsafe: fully de-energize.
    expired = now + controller._s.failsafe_seconds + 0.01
    assert controller._input_expired(expired) is True


def test_failsafe_disabled_never_expires(controller):
    # failsafe_ms <= 0 disables full neutralization: the turret holds aim forever.
    object.__setattr__(controller._s, "failsafe_ms", 0)
    controller.apply_input({"right": True})
    now = time.monotonic()
    # Even an hour later, input never "expires" -> hold, never full neutral.
    assert controller._input_expired(now + 3600.0) is False
