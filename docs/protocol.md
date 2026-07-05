# RWS UDP Protocol Reference

This document describes the binary protocols used to control the water-shooting turret
(RWS — Remote Weapon Station) and to relay control between the web layer and the turret driver.

Two distinct protocols exist in this project:

1. **RWS UDP protocol** — the real wire protocol spoken directly to the turret hardware.
   Implemented in [`rws_control.py`](../rws_control.py) and [`services/rws_bridge/src/rws.py`](../services/rws_bridge/src/rws.py).
2. **External control-channel protocol** — a WebSocket/WebTransport relay protocol between the
   browser, the web backend, and `rws_bridge`. Implemented in
   [`services/rws_bridge/src/protocol.py`](../services/rws_bridge/src/protocol.py).

The authoritative, hand-written reverse-engineering specification (in Ukrainian) is
[`research/reverse_protocol/unit_protocol.md`](../research/reverse_protocol/unit_protocol.md).
Everything below has been cross-checked against that spec and the working code.

---

## 1. RWS UDP protocol (turret wire protocol)

### Transport

- Transport: **UDP**, all multi-byte integers **big-endian** (network byte order).
- Default endpoints (see [`rws_control.py`](../rws_control.py) `DEFAULT_*` constants):
  - Controller (source): `192.168.88.33:7770`
  - Turret (destination): `192.168.88.56:7780`
- Command send rate: **20 Hz** (`PERIOD_MS = 50.0`).
- Link considered stale after **5000 ms** without RX (`DEFAULT_TIMEOUT_MS`).
- Three packet types, distinguished on RX purely by **payload length**:
  - **40 bytes** → command (controller → turret)
  - **32 bytes** → status reply (turret → controller), `RWS_STATUS_PAYLOAD_LEN`
  - **36 bytes** → telemetry reply (turret → controller), `RWS_TELEMETRY_PAYLOAD_LEN`
  - Other lengths (50 = temperature report, 74 = UGV telemetry) are ignored.

### Common framing

Every packet:

- First 4 bytes = header: `packet_type` (1B), `unused/pad0` (1B), `sequence` (2B, uint16, `+1 mod 65536`).
- Last 4 bytes = `checksum`.
- **Checksum** = `SHA256( packet_bytes_without_last_4 ‖ salt32 )[:4]`.
  - `salt32` is the 32-byte shared secret in
    [`research/reverse_protocol/old/read_only/salt.bin`](../research/reverse_protocol/old/read_only/salt.bin).
  - Hex value: `262bd7b673f1371fd274f96f2e819032498f304b4021d3fc87d5db723f8fa277`.
  - Embedded as `DEFAULT_EMBEDDED_SALT` in `test_rws_control.py` and as `RWS_SALT` default in the bridge config.
  - Implemented as `compute_command_checksum(body, salt)` in `rws_control.py`.
  - This salt is effectively the shared key that authenticates every command to the turret.

### Value encodings

| Quantity | Wire type | Encoding | Helper in `rws_control.py` |
|---|---|---|---|
| Velocity (rotation_v / elevation_v) | int16 | normalized `-1.0..+1.0` × `0x7FFF` | `encode_unit_axis_to_packet_s16` / `decode_packet_axis_s16_to_unit` |
| Angle (rotation_p / elevation_p) | int32 | radians on ±π scale: `rad / π × 0x7FFFFFFF` | `encode_angle_rad_to_packet_s32` / `decode_packet_angle_s32_to_rad` |
| Voltage | int16 | `raw × 0.01` volts | — |
| Battery percent | uint16 | `raw / 0xFFFF × 100` | — |
| Distance | uint32 | millimetres | — |

### Command packet — 40 bytes (`packet_type = 0x01`)

Wire struct: `RwsCommandWire` in `rws_control.py`.

| Offset | Size | Type | Field | Meaning |
|---|---|---|---|---|
| 0 | 1 | uint8 | `packet_type` | `0x01` |
| 1 | 1 | uint8 | `pad0` | `0x00` |
| 2 | 2 | uint16 | `sequence` | command counter |
| 4 | 1 | uint8 | `flags1` | enable/slow/reload/forceHome (see below) |
| 5 | 1 | uint8 | `flags2` | validity + velocity-priority (see below) |
| 6 | 1 | uint8 | `flags3` | reserved (0) |
| 7 | 1 | uint8 | `flags4` | reserved (0) |
| 8 | 2 | int16 | `rotation_v` | pan (rotate) velocity, ±1.0 |
| 10 | 2 | int16 | `elevation_v` | tilt (elevate) velocity, ±1.0 |
| 12 | 4 | int32 | `rotation_p` | target pan angle, ±π |
| 16 | 4 | int32 | `elevation_p` | target tilt angle, ±π |
| 20 | 4 | bytes | `arm` | `41 00 00 00` (`'A'`) = armed; `00 00 00 00` = disarmed |
| 24 | 2 | bytes | `fire` | `46 00` (`'F'`) = fire; `00 00` = idle |
| 26 | 2 | uint16 | `fire_duration` | burst length: **short=161, medium=605, manual=0** |
| 28 | 4 | int32 | `cameras_p` | unused (0) |
| 32 | 1 | uint8 | `rangefinder_seq` | rangefinder request counter |
| 33 | 1 | uint8 | `fire_seq` | fire request counter (edge-triggered per burst) |
| 34 | 2 | bytes | `reserved_tail` | `00 00` |
| 36 | 4 | bytes | `checksum` | `SHA256(bytes[0:36] ‖ salt32)[:4]` |

#### `flags1` bits (byte 4)

| Bit | Const | Meaning |
|---|---|---|
| `0x01` | `FLAGS1_ENABLE` | motion enable (turret motors active) |
| `0x02` | `FLAGS1_SLOW` | slow-motion mode |
| `0x04` | `FLAGS1_RELOAD` | reload request |
| `0x08` | `FLAGS1_FORCE_HOME` | pulse "return to home/default position" |

#### `flags2` bits (byte 5)

| Bit | Const | Meaning |
|---|---|---|
| `0x01` | `FLAGS2_ROTATION_V` | `rotation_v` field is valid |
| `0x02` | `FLAGS2_ELEVATION_V` | `elevation_v` field is valid |
| `0x04` | `FLAGS2_ROTATION_P` | `rotation_p` field is valid |
| `0x08` | `FLAGS2_ELEVATION_P` | `elevation_p` field is valid |
| `0x10 \| 0x20` | `FLAGS2_VEL_PRIO` (mask `0x30`) | velocity-priority: obey `*_v` first, treat `*_p` as secondary. Reference senders set **both** bits together. |

> Note: the **standalone** idle command sets `flags2 = 0x03` (rotV+eleV valid, both zero) — confirmed
> via `python3 test_rws_control.py --dry-run --verbose`. The **bridge** always ORs `FLAGS2_VEL_PRIO`,
> so its idle `flags2 = 0x33`.

### Status reply — 32 bytes

Wire struct: `RwsReplyWire` in `rws_control.py`.

| Offset | Size | Type | Field | Meaning |
|---|---|---|---|---|
| 0 | 1 | uint8 | `packet_type` | transport type (raw) |
| 1 | 1 | uint8 | `pad0` | — |
| 2 | 2 | uint16 | `sequence` | echoes the command sequence |
| 4 | 1 | uint8 | `flags0` | unused |
| 5 | 1 | uint8 | `flags1` | `0x04` = `rotation_p` valid; `0x08` = `elevation_p` valid |
| 6 | 1 | uint8 | `flags2` | unused |
| 7 | 1 | uint8 | `flags3` | unused |
| 8 | 4 | int32 | `rotation_p` | current pan angle (±π) |
| 12 | 4 | int32 | `elevation_p` | current tilt angle (±π) |
| 16 | 4 | int32 | `cameras_p` | unused |
| 20 | 4 | uint32 | `distance_mm` | rangefinder distance, millimetres |
| 24 | 2 | uint16 | `shots` | rounds-fired counter |
| 26 | 1 | uint8 | `rangefinder_seq` | echo counter |
| 27 | 1 | uint8 | `fire_seq` | echo counter |
| 28 | 4 | bytes | `checksum` | 4-byte digest |

Validity constants: `RWS_STATUS_FLAGS1_ROTATION_P_VALID = 0x04`, `RWS_STATUS_FLAGS1_ELEVATION_P_VALID = 0x08`.

### Telemetry reply — 36 bytes

Wire struct: `RwsTelemetryWire` in `rws_control.py`.

| Offset | Size | Type | Field | Meaning |
|---|---|---|---|---|
| 0 | 1 | uint8 | `packet_type` | telemetry transport type |
| 1 | 1 | uint8 | `pad0` | — |
| 2 | 2 | uint16 | `sequence` | response counter |
| 4 | 1 | uint8 | `flags0` | `0x01` = RWS node alive; `0x02` = fire pulse active |
| 5 | 1 | uint8 | `flags1` | unused |
| 6 | 1 | uint8 | `flags2` | X-drive: bit0=active, bit1=ready, bit2=alarm, bit3=home-return fault |
| 7 | 1 | uint8 | `flags3` | Y-drive: bit0=active, bit1=ready, bit2=alarm, bit3=home-return fault |
| 8 | 2 | int16 | `rpm_x` | X-drive RPM |
| 10 | 2 | int16 | `voltage_x` | X voltage (×0.01 V) |
| 12 | 2 | int16 | `amperage_x` | X current |
| 14 | 2 | int16 | `temperature_x` | X temperature |
| 16 | 2 | int16 | `rpm_y` | Y-drive RPM |
| 18 | 2 | int16 | `voltage_y` | Y voltage (×0.01 V) |
| 20 | 2 | int16 | `amperage_y` | Y current |
| 22 | 2 | int16 | `temperature_y` | Y temperature |
| 24 | 2 | int16 | `voltage_bat` | battery voltage (×0.01 V) |
| 26 | 2 | int16 | `voltage_fire` | fire-pulse circuit voltage (×0.01 V) |
| 28 | 2 | int16 | `voltage_cpu` | compute-board voltage (×0.01 V) |
| 30 | 2 | uint16 | `battery_percent` | `0..65535 → 0..100%` |
| 32 | 4 | bytes | `checksum` | 4-byte digest |

### Sequence / reply matching

`RwsReplyTracker` (in `rws_control.py`) tracks each sent sequence and matches incoming 32/36-byte
replies by sequence using a signed modular distance (`sequence_distance`). A command is "complete"
once both a 32-byte and a 36-byte reply have arrived for its sequence.

> ⚠️ **Inbound replies are NOT checksum-verified.** Both `rws_control.py` and the bridge
> (`services/rws_bridge/src/rws.py`) dispatch replies purely by length (32/36) and never validate the
> trailing 4-byte digest — a regression from the reference `udpcomm.py`, which dropped packets on hash
> mismatch. All reply values are trusted from any host at the turret IP. Likewise the reply
> `packet_type` byte is ignored (the reference dispatched on it: status=1, telemetry=12, plus GPS/
> compass/gyro/temperature/powers/followme types). See
> [architecture.md → Safety caveats](architecture.md#safety--control-correctness-caveats).

---

## 2. External control-channel protocol (relay)

Defined in [`services/rws_bridge/src/protocol.py`](../services/rws_bridge/src/protocol.py).
`VERSION = 1`. Big-endian. Header = `version` (1B) + `message_type` (1B). This is the protocol a
control source (e.g. the web backend acting as a `web_human`) speaks to `rws_bridge` over WebSocket.

### `control_state` — 12 bytes (`message_type = 1`)

Layout after the 2-byte header: `struct ">IBBhh"` = `seq(uint32), state_flags(u8), aux_flags(u8), axis_x(int16), axis_y(int16)`.
`axis_x`/`axis_y` are already speed-scaled by the client.

- `state_flags`: `ENABLE=0x01`, `SLOW=0x02`, `RELOAD=0x04`, `ARM=0x08`, `FIRE=0x10`.
- `aux_flags`: `FORCE_HOME=0x01`, `CENTER=0x02`, `FIRE_MODE=0xC0` (bits 6-7: 0=short, 1=medium, 2=manual).

### `presence` — 6 bytes (`message_type = 2`)

`struct ">I"` = `seq(uint32)`. Keepalive to hold the ownership lease.

### `observed_state` — 24 bytes (`message_type = 3`)

`encode_observed_state()` packs `struct ">BBIBBiiIHBB"` =
`version, msg_type, seq, state_flags, reserved0, rotation_p, elevation_p, distance_mm, shots, x_status_flags, y_status_flags`.

- `state_flags`: `ROT_P_VALID=0x01`, `ELE_P_VALID=0x02`, `RWS_ACTIVE=0x04`, `FIRE_PULSE=0x08`,
  `SAFE_MODE=0x10`, plus a 2-bit link field `OFFLINE=0x00 / STALE=0x20 / ONLINE=0x40` (mask `0x60`).

> **Gap:** the current web frontend/backend use a **3-byte** joystick datagram
> (`buttons, x, y`), not this 12-byte `control_state`. See
> [architecture.md](architecture.md#known-gaps) → Known gaps.

---

## 3. External rangefinder (separate channel)

The reference GUI also talks to a standalone laser rangefinder over a **separate, unauthenticated**
UDP channel (no salt/checksum), documented only in the reference code
[`research/reverse_protocol/old/read_only/external_rangefinder_comm.py`](../research/reverse_protocol/old/read_only/external_rangefinder_comm.py):

- Rangefinder device: `192.168.88.97:20424`, local bind `:20444`.
- Plain ASCII datagrams: `on_<count>` / `off_1`; distance parsed from reply text.
- Not part of the RWS UDP protocol and not used by the current controllers in this repo.

---

## Source of truth

- Primary spec (Ukrainian): [`research/reverse_protocol/unit_protocol.md`](../research/reverse_protocol/unit_protocol.md)
- Reference vendor code: [`research/reverse_protocol/old/read_only/`](../research/reverse_protocol/old/read_only/)
  (`udpcomm.py` = transport + checksum, `control_rws.py` = RWS-only CLI, `control.py` = UGV+RWS CLI,
  `ControlLayout.py` = Kivy operator GUI).
- Packet captures: [`research/reverse_protocol/old/read_only/captures/`](../research/reverse_protocol/old/read_only/captures/)
  (`.pcap`/`.pcapng` of live sessions; `jumps/` isolates the three fire-duration burst modes).
