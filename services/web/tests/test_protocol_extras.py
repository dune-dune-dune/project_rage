"""Tests for the protocol features the cockpit uses: the held-Shift rangefinder
trigger and the extended telemetry surfaced in /api/status.

The cockpit no longer drives the SLOW flag or the camera axis (the key-4 / key-5
modes were removed), so the command stream must never set either — the two tests
below are regression guards for exactly that. `FLAGS1_SLOW` / `cameras_p` still
exist in `rws_control.py`: that is the wire protocol, shared with the TTY
controller.
"""

from __future__ import annotations

import math
import time

from app.turret import TurretController  # noqa: F401  (ensures rws_control is importable)

import rws_control


def _intent_at(controller, now):
    return controller._read_intent(now)


def test_slow_flag_is_never_set(controller):
    controller.apply_input({"right": True, "slow": True})  # stale clients may still send it
    now = time.monotonic()
    packet = controller._build_packet(_intent_at(controller, now), (False, 0.0, 0.0), now)
    assert not (packet.flags1 & rws_control.FLAGS1_SLOW)


def test_camera_axis_is_never_commanded(controller):
    controller.apply_input({"up": True, "camera_mode": True})
    now = time.monotonic()
    intent = _intent_at(controller, now)
    packet = None
    for _ in range(5):
        packet = controller._build_packet(intent, (False, 0.0, 0.0), now)
    assert packet.cameras_p == 0
    assert packet.elevation_v > 0  # W drives the TURRET elevation, always


def test_rangefinder_seq_paced_while_held(controller):
    controller.apply_input({"rangefinder": True})
    interval = controller._s.rangefinder_measure_interval_seconds
    intent = _intent_at(controller, time.monotonic())
    t0 = time.monotonic()
    controller._build_packet(intent, (False, 0.0, 0.0), t0)
    assert controller._rangefinder_seq == 1
    # A second build at the same instant must NOT issue another measurement.
    controller._build_packet(intent, (False, 0.0, 0.0), t0)
    assert controller._rangefinder_seq == 1
    # After the configured interval, one more measurement is issued.
    controller._build_packet(intent, (False, 0.0, 0.0), t0 + interval + 0.001)
    assert controller._rangefinder_seq == 2


def test_rangefinder_seq_stable_when_not_held(controller):
    controller.apply_input({})
    now = time.monotonic()
    controller._build_packet(_intent_at(controller, now), (False, 0.0, 0.0), now)
    assert controller._rangefinder_seq == 0


def _telemetry_bytes(**overrides) -> bytes:
    fields = dict(
        packet_type=12,
        pad0=0,
        sequence=1,
        flags0=0,
        flags1=0,
        flags2=0,
        flags3=0,
        rpm_x=1200,
        voltage_x=2450,
        amperage_x=150,
        temperature_x=40,
        rpm_y=-800,
        voltage_y=2400,
        amperage_y=120,
        temperature_y=38,
        voltage_bat=2510,
        voltage_fire=2480,
        voltage_cpu=500,
        battery_percent=0x8000,
    )
    fields.update(overrides)
    wire = rws_control.RwsTelemetryWire(**fields)
    return wire.to_bytes()


def test_telemetry_parse_surfaces_all_fields(controller):
    controller._update_telemetry_from_reply(_telemetry_bytes())
    snap = controller.snapshot()
    assert snap["voltage_fire"] == 24.8
    assert snap["voltage_cpu"] == 5.0
    assert snap["motor_voltage"] == {"x": 24.5, "y": 24.0}
    assert snap["motor_rpm"] == {"x": 1200, "y": -800}
    assert snap["battery_voltage"] == 25.1
    assert snap["battery_percent"] == 50


def _status_bytes(cameras_p: int) -> bytes:
    wire = rws_control.RwsReplyWire(
        packet_type=1,
        pad0=0,
        sequence=1,
        flags0=0,
        flags1=0,
        flags2=0,
        flags3=0,
        rotation_p=0,
        elevation_p=0,
        cameras_p=cameras_p,
        distance_mm=1000,
        shots=0,
        rangefinder_seq=0,
        fire_seq=0,
    )
    return wire.to_bytes()


def test_status_parse_surfaces_camera_angle(controller):
    raw = rws_control.encode_angle_rad_to_packet_s32(math.radians(20))
    controller._update_status_from_reply(_status_bytes(raw))
    snap = controller.snapshot()
    assert snap["camera_angle_deg"] == 20.0


def test_turret_distance_field_uses_status_reply(controller):
    # _status_bytes hard-codes distance_mm=1000 -> 1.0 m from the turret reply.
    controller._update_status_from_reply(_status_bytes(0))
    snap = controller.snapshot()
    assert snap["distance_turret_m"] == 1.0
