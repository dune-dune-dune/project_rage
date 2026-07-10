"""Velocity soft-start ramp: manual motion accelerates smoothly, not in a step.

The turret jerked once at movement start because rotation_v stepped 0 -> full on
the first tick. The ramp slews the commanded velocity toward the target so the
first tick is small and full scale is reached over several ticks.
"""

from __future__ import annotations

import time

from app.config import load_settings
from app.turret import TurretController


def _hold_right_packets(controller, ticks):
    """Return rotation_v for each of ``ticks`` consecutive 'holding right' packets."""
    controller.apply_input({"right": True})
    now = time.monotonic()
    intent = controller._read_intent(now)
    out = []
    for _ in range(ticks):
        out.append(controller._build_packet(intent, (False, 0.0, 0.0), now).rotation_v)
    return out


def test_first_tick_is_below_full_speed(controller):
    vs = _hold_right_packets(controller, 10)
    assert vs[0] > 0            # already moving
    assert vs[0] < vs[-1]       # but not yet at full speed


def test_velocity_ramps_up_monotonically(controller):
    vs = _hold_right_packets(controller, 10)
    # Strictly increasing until it saturates, never decreasing.
    assert all(b >= a for a, b in zip(vs, vs[1:]))
    assert vs[1] > vs[0]


def test_reaches_saturation_and_holds(controller):
    vs = _hold_right_packets(controller, 20)
    # ramp_ms=250 at 20 Hz -> ~5 ticks to full; well saturated by tick 20.
    assert vs[-1] == vs[-2]
    full = _hold_right_packets(controller, 20)[-1]
    assert vs[-1] == full


def test_release_ramps_back_down(controller):
    _hold_right_packets(controller, 20)  # ramp up to full
    now = time.monotonic()
    controller.apply_input({})           # release: target 0
    intent = controller._read_intent(now)
    first = controller._build_packet(intent, (False, 0.0, 0.0), now).rotation_v
    second = controller._build_packet(intent, (False, 0.0, 0.0), now).rotation_v
    assert 0 <= second < first           # decelerating toward zero, not an instant stop


def test_ramp_disabled_steps_instantly():
    """ramp_ms=0 restores the original single-tick full-scale step."""
    import dataclasses
    import os

    os.environ["RWS_DRY_RUN"] = "true"
    # Disable the ramp without touching settings.toml; accel_per_tick then returns
    # 1.0, so the commanded velocity reaches full scale in a single tick.
    settings = dataclasses.replace(load_settings(), ramp_ms=0)
    controller = TurretController(settings)
    vs = _hold_right_packets(controller, 3)
    assert vs[0] == vs[1] == vs[2]       # full speed from the very first tick
