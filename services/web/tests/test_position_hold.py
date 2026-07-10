"""Position target follows the reference model: P-valid bits stay on continuously.

The one-time jerk at movement start was traced to the ROT_P/ELE_P valid bit
toggling off->on while the target jumped 0 -> +/-pi on the first move packet. The
reference keeps the bits valid and holds the *current* angle when idle, leading it
by a modest amount when moving. These tests pin that behaviour.
"""

from __future__ import annotations

import math
import time

import rws_control

from app.turret import _POSITION_LEAD_RAD


def _status_reply(rot_rad: float, ele_rad: float) -> bytes:
    """Build a 32-byte status reply carrying both angles as valid."""
    r = rws_control.RwsReplyWire()
    r.packet_type = 1
    r.flags1 = (
        rws_control.RWS_STATUS_FLAGS1_ROTATION_P_VALID
        | rws_control.RWS_STATUS_FLAGS1_ELEVATION_P_VALID
    )
    r.rotation_p = rws_control.encode_angle_rad_to_packet_s32(rot_rad)
    r.elevation_p = rws_control.encode_angle_rad_to_packet_s32(ele_rad)
    return bytes(r)


def _telemetry_reply(*, battery_raw, voltage_bat, temp_x, temp_y, amp_x, amp_y) -> bytes:
    """Build a 36-byte telemetry reply carrying battery/motor health fields."""
    t = rws_control.RwsTelemetryWire()
    t.battery_percent = battery_raw
    t.voltage_bat = voltage_bat
    t.temperature_x = temp_x
    t.temperature_y = temp_y
    t.amperage_x = amp_x
    t.amperage_y = amp_y
    return bytes(t)


def _packet(controller, payload):
    controller.apply_input(payload)
    now = time.monotonic()
    return controller._build_packet(controller._read_intent(now), (False, 0.0, 0.0), now)


def test_without_telemetry_idle_leaves_p_bit_off(controller):
    # Fallback (no reply yet): idle keeps the position-valid bit off, as before.
    pkt = _packet(controller, {})
    assert not (pkt.flags2 & rws_control.FLAGS2_ROTATION_P)


def test_reply_populates_current_angles(controller):
    controller._ingest_reply(_status_reply(0.5, -0.3))
    snap = controller.snapshot()
    assert snap["angle_rot_deg"] == round(math.degrees(0.5), 1)
    assert snap["angle_ele_deg"] == round(math.degrees(-0.3), 1)


def test_idle_holds_current_angle_with_p_valid(controller):
    controller._ingest_reply(_status_reply(0.5, -0.3))
    pkt = _packet(controller, {})
    # P bits stay valid even when idle...
    assert pkt.flags2 & rws_control.FLAGS2_ROTATION_P
    assert pkt.flags2 & rws_control.FLAGS2_ELEVATION_P
    # ...and the target equals the current angle, so the turret holds (no drift).
    assert abs(rws_control.decode_packet_angle_s32_to_rad(pkt.rotation_p) - 0.5) < 1e-3


def test_moving_leads_current_angle_not_full_pi(controller):
    controller._ingest_reply(_status_reply(0.5, -0.3))
    pkt = _packet(controller, {"right": True})
    target = rws_control.decode_packet_angle_s32_to_rad(pkt.rotation_p)
    assert abs(target - (0.5 + _POSITION_LEAD_RAD)) < 1e-3
    assert target < math.pi  # a modest lead, never the full +/-pi jump


def test_lead_clamped_to_pi_near_the_limit(controller):
    controller._ingest_reply(_status_reply(3.0, 0.0))  # already near +pi
    pkt = _packet(controller, {"right": True})
    target = rws_control.decode_packet_angle_s32_to_rad(pkt.rotation_p)
    assert target <= math.pi + 1e-6  # clamped, no wrap to the far side


def test_status_reply_exposes_distance(controller):
    r = rws_control.RwsReplyWire()
    r.distance_mm = 12450
    controller._ingest_reply(bytes(r))
    assert controller.snapshot()["distance_m"] == 12.45


def test_telemetry_reply_exposes_health(controller):
    controller._ingest_reply(_telemetry_reply(
        battery_raw=0xFFFF, voltage_bat=2520, temp_x=42, temp_y=45, amp_x=120, amp_y=130,
    ))
    snap = controller.snapshot()
    assert snap["battery_percent"] == 100
    assert snap["battery_voltage"] == 25.2
    assert snap["motor_temp"] == {"x": 42, "y": 45}
    assert snap["motor_current"] == {"x": 1.2, "y": 1.3}
