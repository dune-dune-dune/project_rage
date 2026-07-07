# Architecture

`project_rage` is a control stack for a remotely operated **water-shooting turret** (RWS — Remote
Weapon Station). It is split into a standalone keyboard controller (the mature, working path) and a
set of services intended to provide a browser-based cockpit with live video.

For the exact wire formats see [protocol.md](protocol.md).

---

## Components

| Component | Path | Language | Role | Status |
|---|---|---|---|---|
| **RWS core / keyboard controller** | [`rws_control.py`](../rws_control.py), [`test_rws_control.py`](../test_rws_control.py) | Python | Protocol library + interactive TTY controller that streams 40-byte RWS UDP commands directly to the turret at 20 Hz | **Working** |
| **rws_bridge** | [`services/rws_bridge/`](../services/rws_bridge/) | Python (asyncio) | Long-running turret driver: WebSocket control server + 20 Hz RWS command loop + ownership/lease watchdog | **Working (standalone)** |
| **web cockpit** | [`services/web/`](../services/web/) | Python (Flask + Gunicorn) | Browser cockpit: full-screen WHEP video + WASD/F/Space control; a background thread streams 40-byte RWS UDP commands directly to the turret at 20 Hz | **Working** |
| **video_gateway** | [`services/video_gateway/`](../services/video_gateway/) | MediaMTX (Docker) | Pulls camera RTSP on demand, republishes as WebRTC/WHEP to the browser | **Working** |

The root [`compose.yaml`](../compose.yaml) defines only `video_gateway`. The web cockpit has its own
[`services/web/docker-compose.yml`](../services/web/docker-compose.yml) that brings up **both** the
Flask cockpit (host networking, to bind `RWS_SRC_IP:RWS_SRC_PORT` and reach the turret) and a
`video_gateway`. `rws_bridge` still runs directly on the host.

---

## Control path

The **standalone keyboard controller** is the complete, working control path today:

```
Keyboard (TTY) → test_rws_control.py → rws_control.py → UDP 40-byte command
              → 192.168.88.33:7770 → turret 192.168.88.56:7780  (20 Hz)
turret → 32-byte status + 36-byte telemetry → controller (matched by sequence)
```

The **web cockpit control path** (`services/web/`, Flask + Gunicorn) drives the turret directly,
reusing `rws_control.py` — it does **not** go through `rws_bridge`:

```
Browser (WASD momentary / F=safety toggle / Space=hold-fire)
  → on change + ~150 ms heartbeat → POST /api/input {up,down,left,right,safety,fire}
  → Flask updates lock-guarded intent + deadman timestamp
  → background sender thread @ 20 Hz → build_generated_command_packet()
  → RwsControlChannel → 40-byte RWS UDP → turret 192.168.88.56:7780
turret replies (32/36 B) → poll_events (drained; HUD reads /api/status)
```

Key properties of the cockpit's `TurretController` ([`services/web/app/turret.py`](../services/web/app/turret.py)):

- **Single owner.** One Gunicorn worker (`GUNICORN_WORKERS=1`, enforced by `gunicorn.conf.py`) owns
  the UDP channel and the sequence counter. Multiple workers would mean multiple senders → corrupt stream.
- **Movement is not gated by safety.** WASD always drives velocity. `FLAGS1_ENABLE` stays on for the
  whole live session (not just while a key is held) so the motors **hold position** — a released axis
  must not sag or spring back. ENABLE drops only on the deadman neutral packet. The turret can be aimed
  at any time.
- **Safety (F) gates firing only.** `arm='A'` and `fire='F'` are emitted only when `safety_off` is true;
  fire additionally requires `fire_held` (`fire='F'` iff `safety_off and fire_held`) — a web-layer
  interlock the wire protocol itself lacks (see Safety caveats).
- **Fire mode (M).** `short`/`medium`/`manual` selects `fire_duration` (161/605/0), cycled at runtime.
- **Deadman.** If no browser input arrives for `deadman_ms` (default 400 ms), the sender forces neutral.
- **Dry-run.** `RWS_DRY_RUN=true` (default) never opens the socket; packets are built and logged only.
- **Crosshair.** An adjustable aiming crosshair (⚙ panel) is persisted to `data/crosshair.json` via
  `GET`/`POST /api/crosshair` for reuse by later tooling.

## Video path

The MediaMTX gateway itself works; a browser pointed directly at its WHEP endpoint gets video:

```
Turret cameras 192.168.88.95 / .96  (RTSP :554, streams av0_0 / av0_1 / av0_2)
  → MediaMTX video_gateway pulls on demand over UDP
  → WHEP POST http://192.168.88.33:8889/cam95_main/whep
  → WebRTC (media UDP :8189, STUN for ICE) → <video> element
```

The Flask cockpit renders its WHEP URL into the page from the `WHEP_URL` env var (server-side, via
`index.html`). If `WHEP_URL` is empty the HUD shows `NO VIDEO URL`; if the stream is unreachable it
shows `NO SIGNAL`. Set `WHEP_URL=http://<gateway>:8889/<cam>/whep` in `.env` to enable video.

**Codec note.** All camera streams are **H265/HEVC**, which only Safari (and Chrome on HEVC-capable
hardware) can play over WebRTC. For cross-browser video the gateway exposes **H264-transcoded** paths
via ffmpeg `runOnDemand` (requires the `bluenviron/mediamtx:1.18.2-ffmpeg` image):

- `cam95_h264` / `cam96_h264` — **default, low-latency**: transcode the 640×480 sub-stream (`av0_1`).
  The SD stream always encodes faster than real time, so latency does **not** accumulate (1080p
  software transcode can dip below real time and grow glass-to-glass latency to seconds). Tuned with
  `-fflags nobuffer -flags low_delay`, x264 `zerolatency`, and a 0.5 s keyframe interval.
- `cam95_h264_hd` / `cam96_h264_hd` — 1080p `av0_0`, heavier; use only with CPU headroom.

The cockpit's **TAB** key cycles the `[video].streams` list from
[`settings.toml`](../services/web/settings.toml); the WHEP base URL is derived from `WHEP_URL`
(or `WHEP_BASE` / `VIDEO_GATEWAY_HOST_IP`). RTSP pulls use **TCP** because UDP RTP times out through
Docker Desktop's NAT on macOS/Windows.

---

## Ports, IPs, and environment variables

### RWS core (`rws_control.py` defaults)

| Setting | Value |
|---|---|
| Source (controller) | `192.168.88.33:7770` |
| Destination (turret) | `192.168.88.56:7780` |
| Send period | 50 ms (20 Hz) |
| Stale timeout | 5000 ms |

CLI overrides: `--bind-ip`, `--bind-port`, `--dst-ip`, `--dst-port`, `--salt-file`, `--interval-ms`,
`--dry-run`, etc.

### rws_bridge ([`config.py`](../services/rws_bridge/src/config.py))

| Setting | Env var | Default |
|---|---|---|
| RWS bind IP | `RWS_BIND_IP` | `192.168.88.33` |
| RWS bind port | `RWS_BIND_PORT` | `7770` |
| RWS dst IP (turret) | `RWS_DST_IP` | `192.168.88.56` |
| RWS dst port | `RWS_DST_PORT` | `7780` |
| Send period | `RWS_SEND_PERIOD_MS` | `50.0` (20 Hz) |
| Stale timeout | `RWS_STALE_TIMEOUT_MS` | `5000` |
| Ownership lease timeout | `LEASE_TIMEOUT_MS` | `4000` |
| WebSocket host | `WS_HOST` | `0.0.0.0` |
| WebSocket port | `WS_PORT` | `8765` |
| Checksum salt (32-byte hex) | `RWS_SALT` | `262bd7b6…fa277` |

The bridge runs three asyncio tasks ([`main.py`](../services/rws_bridge/src/main.py)):
1. `start_ws_server` — WebSocket control server for sources.
2. `_control_loop` — every 50 ms sends `bridge.next_rws_command()`, ingests replies, broadcasts `observed_state`.
3. `_watchdog_loop` — every 200 ms enforces the ownership lease.

### web cockpit ([`services/web/`](../services/web/))

Deployment/network/secrets come from `.env` (see [`.env.example`](../services/web/.env.example)):

| Setting | Env var | Default |
|---|---|---|
| RWS source (bind) IP/port | `RWS_SRC_IP` / `RWS_SRC_PORT` | `192.168.88.33` / `7770` |
| RWS dst (turret) IP/port | `RWS_DST_IP` / `RWS_DST_PORT` | `192.168.88.56` / `7780` |
| Dry-run (do not transmit) | `RWS_DRY_RUN` | `true` |
| Checksum salt file (32 B) | `RWS_SALT_FILE` | empty → built-in salt |
| Gunicorn bind | `WEB_BIND` | `0.0.0.0:8000` |
| Gunicorn workers / threads | `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `1` / `8` |
| Log level | `LOG_LEVEL` | `info` |
| Video WHEP URL | `WHEP_URL` | (optional) |
| Video gateway host IP | `VIDEO_GATEWAY_HOST_IP` | (optional) |

Control **tuning** lives separately in [`settings.toml`](../services/web/settings.toml) (read via
stdlib `tomllib`, mounted read-only into the container so it can be edited without a rebuild):
`[control]` send_rate_hz (20), deadman_ms (400), speed_percent (100); `[axes]` rotation/elevation unit
amplitudes; `[fire]` mode + short/medium durations.

HTTP routes ([`routes.py`](../services/web/app/routes.py)): `GET /` (cockpit page), `GET /healthz`,
`POST /api/input` (JSON intent → controller, 204), `GET /api/status` (HUD snapshot).

Gunicorn runs `app.wsgi:app` via [`gunicorn.conf.py`](../services/web/gunicorn.conf.py) (pins
`workers=1`, reads `WEB_BIND`/`GUNICORN_THREADS`, and auto-loads `.env` through python-dotenv). The app
factory ([`__init__.py`](../services/web/app/__init__.py)) constructs the single `TurretController` and
starts its sender thread at import time. Native host runs use
[`run-native.sh`](../services/web/run-native.sh) (creates a Python 3.11+ venv and launches Gunicorn).

### video_gateway ([`mediamtx.yml`](../services/video_gateway/mediamtx.yml), [`compose.yaml`](../compose.yaml))

- Image `bluenviron/mediamtx:1.18.2`. HLS disabled. WebRTC on `:8889` (WHEP signaling), media UDP `:8189`.
- Published ports: `8889:8889`, `8189:8189/udp`.
- **Compose project name is `autoantibug`** (`compose.yaml` line 1), not `project_rage` — so containers/networks
  are prefixed `autoantibug-…` (e.g. `autoantibug-video_gateway-1`). Relevant for `docker ps` / cleanup.
- `MTX_WEBRTCADDITIONALHOSTS` fed from host env `VIDEO_GATEWAY_HOST_IP` (so WebRTC advertises the
  right host IP for ICE — clients are not on the docker network).
- Six on-demand RTSP pulls: `cam95_main/_sub1/_sub2` from `192.168.88.95:554`, `cam96_*` from `192.168.88.96:554`
  (streams `av0_0` / `av0_1` / `av0_2`), UDP transport.

---

## Safety & ownership model (rws_bridge)

- The bridge **starts in `safe_mode = True`**. While in safe mode (or with no owner, or `enable`
  not set), `next_rws_command()` emits a **neutral packet**: `flags1=0`, zero velocities, disarmed,
  fire off. This is the safe default that keeps the turret inert.
- **Single owner**: exactly one control source owns the turret at a time (`OwnershipManager`).
  `take_control` fails with `"occupied"` if someone else owns it.
- **Lease**: the owner must keep sending traffic; after `LEASE_TIMEOUT_MS` (4 s) of silence the lease
  is revoked, `_latest_ctrl` is cleared, and the bridge reverts to safe mode.
- **Replay protection**: incoming control frames must have monotonically increasing sequence numbers.
- **Fire is edge-triggered**: `FireTracker` increments `fire_seq` on each rising edge of fire, so one
  press = one burst.

The standalone `test_rws_control.py` controller has its own safety gate: **`safetyARM`** (toggled with
Backspace) must be on before `arm='A'` is sent, and `turret_enable` (key `1`) must be on for motion.

---

## Known gaps

These are real discrepancies confirmed in the code. The old WebTransport/Vite prototype (and its
browser↔bridge relay gaps) has been **removed** and replaced by the Flask cockpit, which drives the
turret directly. The remaining gaps:

1. **Web cockpit bypasses `rws_bridge` entirely.** The Flask cockpit talks RWS UDP directly via
   `rws_control.py`; it does **not** use the bridge's ownership/lease/replay-protection or its
   `observed_state` telemetry. So the two never run against the same turret at once — pick one control
   path. The cockpit's own protections are the master-safety toggle, the software fire interlock, the
   400 ms deadman, and single-worker ownership (below), not the bridge's lease model.
2. **Live control needs host-level networking.** Binding `RWS_SRC_IP:RWS_SRC_PORT` requires the process
   to run in the host network namespace. In Docker this means `network_mode: host` (Linux/Docker Engine
   only). On **macOS/Windows** Docker Desktop has no host networking, so run the cockpit **natively** via
   [`run-native.sh`](../services/web/run-native.sh) (video_gateway still runs in Docker) — the host must
   own `192.168.88.33`. The `TurretController` retries the bind every second, so control starts as soon
   as the IP is configured, without a restart; until then `/api/status.bind_error` is populated and the
   HUD shows `TURRET BIND ERR`.
3. **Single-worker requirement is a footgun if overridden.** The Dockerfile `CMD` hardcodes
   `--workers 1`. Raising it (or running multiple app instances) creates multiple UDP senders sharing
   one turret with independent sequence counters → corrupt/duplicated command stream. Keep it at one.
4. **Web cockpit video off unless `WHEP_URL` is set.** The cockpit renders `WHEP_URL` into the page;
   empty → HUD shows `NO VIDEO URL`. It also needs the `video_gateway` up and cameras reachable.
5. **Broken Claude Code hooks.** [`.claude/settings.local.json`](../.claude/settings.local.json)
   registers PreToolUse hooks `.claude/hooks/guard-bash.sh` and `.claude/hooks/guard-read.sh`, but the
   `.claude/hooks/` directory does not exist.
6. **Stale reference stub.** `research/reverse_protocol/old/test_control.py` imports a `main` from a
   module `test_rws_control` that does not exist under `old/`. The old CLI's illustrative burst
   durations (100/1000/10000) do not match the real captured values (161/605/0).

---

## Safety & control-correctness caveats

Behaviors in the current code that are non-obvious and matter for safe/correct control. These are
**not** bugs the docs invented — they are how the code actually behaves today. Treat as required
reading before any live operation or before wiring the web path.

### Firing is not gated by ARM (and, in the standalone, not by enable)

- **Standalone** (`test_rws_control.py` `build_cmd_packet`): `fire='F'` + `fire_duration` are written
  whenever Space is held (`is_fire_active`), **independent of `safety_arm_enabled` and `turret_enable`**.
  `safetyARM` only gates the separate `arm='A'` byte. So a fire packet goes on the wire even with ARM
  off and enable off.
- **Bridge** (`bridge.py` `next_rws_command`): when enabled, fire is emitted from the `FireTracker`
  with **no check of `ctrl.arm`**. Fire is gated on `enable`, not on `arm`.
- There is **no software "must be armed to fire" interlock** in the wire protocol. The Flask cockpit
  adds one in its own layer: `fire='F'` is emitted only when the master safety is off
  (`safety_off and fire_held`), and the master safety gates `enable`+`arm` together. This is a
  cockpit-level convenience, not a turret-firmware guarantee.

### No software travel/sector limits

The reference `ControlLayout.py` enforced per-axis angle limits and sector guards
(`rotation_from/to`, `elevation_from/to`, etc.). Neither `rws_control.py` nor the bridge enforces any
angle clamp: the standalone drives `rotP/eleP` to ±π (full swing) and the bridge commands velocity
±1.0 unbounded. Mechanical/firmware limits are the only protection.

### Inbound replies are not authenticated

Both `rws_control.py` (`RwsReplyTracker.record`, `RwsControlChannel.poll_events`) and the bridge
(`rws.py` `_on_datagram`) dispatch replies **purely by length (32/36)** and never verify the 4-byte
checksum — a regression from the reference `udpcomm.py`, which dropped on hash mismatch. Every
operator-facing value (`distance_mm`, `shots`, angles, link "online", `rws_active`, `fire_pulse`) is
accepted unvalidated from any host at the turret IP. See [protocol.md](protocol.md).

### Committed shared secret ⇒ no real command authentication

The 32-byte salt that authenticates every command is checked into the repo three ways (`salt.bin`,
`DEFAULT_EMBEDDED_SALT`, `RWS_SALT` default). Anyone with the repo can forge valid commands. The
checksum is integrity/versioning, not a secret-protected auth.

### Bridge is velocity-only; standalone can command position

The bridge hardcodes `rotation_p=elevation_p=0`, never sets the `*_P` valid bits, and always ORs
`FLAGS2_VEL_PRIO` — so it can only do velocity + a force-home pulse, never seek/hold an absolute angle
or center. The standalone can (rotP/eleP + `center_requested`). Force-home pulse widths also differ:
bridge ≈0.15 s vs standalone 1.0 s (`FORCE_HOME_PULSE_SECONDS`). Consequence: the bridge idle command
sends `flags2 = 0x33` (velPrio always on), whereas the standalone idle sends `0x03`.

### Fire timing has two independent concepts

`fire_seq` increments once per rising edge of fire (one Space tap = one `fire_seq`), but `fire='F'`
is asserted for the whole `axis_hold` window (~10 packets at the 500 ms default), each carrying
`fire_duration` (161/605/0). Whether the unit fires once per `fire_seq` or per `fire_duration` decides
how many rounds leave the barrel — an unresolved control-correctness question. "Manual" mode is not
truly while-held either: it holds `fire='F'` for `axis_hold` after the last key event, with `duration=0`.

### Cross-tool key-mapping differences

The Flask cockpit and the TTY controller use **different keys** for the same functions — a
muscle-memory hazard when switching tools. Cockpit: `F` = master safety (enable+arm), `Space` = fire,
`WASD` = momentary move. TTY (`test_rws_control.py`): `Backspace` = safetyARM, `1` = enable,
`Space` = fire, `WASD` = *latched* axes. In the cockpit `Space` fires (as in the TTY); there is no
gamepad path, so the browser input-merge/stuck-controller hazard of the old prototype no longer exists.

### Wider protocol family & rangefinder

The reference dispatches replies by `packet_type` across a wider registry (GPS=3, compass=4, gyro=5,
temperatures=6, powers=10, followme=11, RWS status=1, RWS telemetry=12); the new code ignores
`packet_type` and keys only on length. The rangefinder request flow is unimplemented
(`rangefinder_seq` is hard-0), yet the bridge copies `distance_mm` into `observed_state`
unconditionally, so observed distance can be stale/garbage with no validity gate.

### TUI counters are not a command-loss signal

`RwsReplyTracker` marks a command "complete" only after both a 32- and a 36-byte reply share its
sequence, but telemetry has its own cadence, so `pending_packets` effectively never drains. Read link
health from `describe_connection` (`last_rx` age), not from "pending".
