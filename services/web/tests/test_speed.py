"""Rotation-speed levels (keys 1..N) scale the manual-motion velocity."""

from __future__ import annotations

import time


def _rotation_v(controller, level):
    """Read the steady-state rotation_v for 'holding right' at a speed level.

    Runs enough ticks to let the soft-start ramp reach saturation, so the value
    reflects the level's full velocity rather than an intermediate ramp step.
    """
    controller.apply_input({"right": True, "speed_level": level})
    now = time.monotonic()
    intent = controller._read_intent(now)
    packet = None
    for _ in range(20):  # >> ticks needed to ramp 0 -> full at any level
        packet = controller._build_packet(intent, (False, 0.0, 0.0), now)
    return packet.rotation_v


def test_lower_level_gives_smaller_velocity(controller):
    v1 = _rotation_v(controller, 1)  # 50%
    v2 = _rotation_v(controller, 2)  # 100%
    assert 0 < v1 < v2


def test_fine_level_is_slowest_but_still_moves(controller):
    """Level 3 (1%) must command a real, non-zero velocity — not a rounded-to-0 one."""
    v3 = _rotation_v(controller, 3)  # 1%
    assert 0 < v3 < _rotation_v(controller, 1)
    # 0.8 (axis unit) * 1.00 (global) * 0.01 (level) * 0x7FFF
    assert v3 == 262


def test_default_speed_level_is_fastest(controller):
    levels = controller._s.speed_levels
    level = controller.snapshot()["speed_level"]
    # NOT the last entry: the fine-aim level lives at the end (key `3`).
    assert levels[level - 1] == max(levels)


def test_invalid_speed_level_is_ignored(controller):
    default = controller.snapshot()["speed_level"]
    for bad in (0, 99, "2", 1.5, True):
        controller.apply_input({"speed_level": bad})
        assert controller.snapshot()["speed_level"] == default


def test_valid_speed_level_is_applied(controller):
    controller.apply_input({"speed_level": 1})
    assert controller.snapshot()["speed_level"] == 1


def test_speed_config_shape(controller):
    cfg = controller.speed_config()
    assert cfg["levels"] and isinstance(cfg["levels"], list)
    assert cfg["current"] == controller.snapshot()["speed_level"]
