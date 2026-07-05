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
| **web backend** | [`services/web/backend/`](../services/web/backend/) | Python (pywebtransport + aiohttp) | Browser control endpoint over WebTransport + `/config.json` HTTP | **Prototype** |
| **web frontend** | [`services/web/frontend/`](../services/web/frontend/) | TypeScript + Vite | Full-screen cockpit: keyboard/gamepad/on-screen input, WHEP video, HUD | **Prototype** |
| **video_gateway** | [`services/video_gateway/`](../services/video_gateway/) | MediaMTX (Docker) | Pulls camera RTSP on demand, republishes as WebRTC/WHEP to the browser | **Working** |

Only `video_gateway` is defined in [`compose.yaml`](../compose.yaml). `rws_bridge` and `web` are run
directly on the host (consistent with `RWS_BIND_IP`/WHEP defaulting to host IP `192.168.88.33`).

---

## Control path

The **standalone keyboard controller** is the complete, working control path today:

```
Keyboard (TTY) → test_rws_control.py → rws_control.py → UDP 40-byte command
              → 192.168.88.33:7770 → turret 192.168.88.56:7780  (20 Hz)
turret → 32-byte status + 36-byte telemetry → controller (matched by sequence)
```

The **intended web control path** (see Known gaps for what is not yet wired):

```
Browser input (keyboard WASD / gamepad / on-screen keys)
  → merged at 120 Hz → {x, y, buttons}
  → WebTransport datagram → web backend (:4433)
  → [GAP: backend does NOT relay] →
  → rws_bridge WebSocket (:8765) control_state (12 bytes)
  → rws_bridge 20 Hz loop → next_rws_command() → 40-byte RWS UDP → turret
turret replies → rws_bridge → observed_state (24 bytes) → back to browser HUD
```

## Video path

The MediaMTX gateway itself works; a browser pointed directly at its WHEP endpoint gets video:

```
Turret cameras 192.168.88.95 / .96  (RTSP :554, streams av0_0 / av0_1 / av0_2)
  → MediaMTX video_gateway pulls on demand over UDP
  → WHEP POST http://192.168.88.33:8889/cam95_main/whep
  → WebRTC (media UDP :8189, STUN for ICE) → <video> element
```

**But the web cockpit does not get video by default.** The frontend takes its WHEP URL **only** from
`/config.json`'s `whepUrl`, which the backend populates only when env `WHEP_URL` is set (default `""`).
The `VITE_WHEP_URL` in `.env.development` is **dead code** — its fallback in `src/config/runtime.ts` is
commented out; the frontend only ever reads `VITE_WT_URL`. So out of the box the cockpit shows
`NO SIGNAL` until the operator sets backend `WHEP_URL`. See Known gaps.

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

### web backend ([`main.py`](../services/web/backend/main.py))

| Setting | Env var | Default |
|---|---|---|
| WebTransport host/port | `WT_HOST` / `WT_PORT` | `0.0.0.0` / `4433` |
| HTTP host/port | `HTTP_HOST` / `HTTP_PORT` | `0.0.0.0` / `8080` |
| Public WT URL | `WT_URL_PUB` | `https://localhost:4433/` (must match cert SAN `DNS:localhost` — do **not** use `127.0.0.1`) |
| Video WHEP URL | `WHEP_URL` | (optional) |
| Cert / key | `CERT_FILE` / `KEY_FILE` | `localhost.crt` / `localhost.key` |

WebTransport requires a cert whose SHA-256 hash the browser pins via `serverCertificateHashes`. The
backend generates a self-signed cert (max 14-day validity — a browser API constraint), computes its
DER SHA-256, and hands the base64 hash to the browser through `/config.json`
(`{wtUrl, certHash, whepUrl, debug}`).

### web frontend ([`frontend/`](../services/web/frontend/))

- Vite dev server: `0.0.0.0:5173`, proxies only `/config.json → http://localhost:8080`.
- [`.env.development`](../services/web/frontend/.env.development):
  `VITE_WT_URL=https://127.0.0.1:4433/` (used only as fallback if `/config.json` fails);
  `VITE_WHEP_URL=…` is present but **not read** by the code (dead — the `runtime.ts` fallback is commented out).
- Control loop targets **120 Hz**; merges keyboard > virtual keys > gamepad for axes, ORs buttons.
- Input bit layout (keyboard/gamepad/on-screen all agree): fire `0x01`, slow `0x02`, reload `0x04`,
  arm `0x08`, force_home `0x10`.

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

These are real discrepancies confirmed in the code. Documented so future work does not assume the
web→turret chain is complete.

1. **Web backend does not relay to rws_bridge.** `services/web/backend/main.py` decodes the browser
   datagram into an in-memory `SharedState.latest` and stops there (its own comment: "prototype; will
   be replaced by rws_bridge relay"). `services/web/backend/config.py` and
   `services/web/backend/transport/webtransport.py` are **empty 0-byte placeholders**. So today
   browser → web backend works, but web backend → rws_bridge → turret is **not implemented**.
2. **Protocol mismatch, with a dangerous bit collision.** The frontend/backend use a **3-byte**
   joystick datagram (`buttons, x=(b1-128)/128, y=(b2-128)/128`), while `rws_bridge` expects the
   **12-byte** `control_state`. Worse, the frontend button bits are `fire=0x01, slow=0x02, reload=0x04,
   arm=0x08, force_home=0x10`, while `control_state.state_flags` are `ENABLE=0x01, SLOW=0x02,
   RELOAD=0x04, ARM=0x08, FIRE=0x10`. slow/reload/arm align, but a naive `buttons → state_flags` copy
   maps browser **FIRE(0x01) → ENABLE** and browser **HOME(0x10) → FIRE** — i.e. the Home button would
   fire the weapon. The frontend also has **no enable bit at all**, tempting an implementer to remap
   fire→enable and hit exactly this trap. Any relay must translate fields explicitly, never copy bytes.
3. **Web client performs no ownership/enable handshake.** Even if the backend relayed, the bridge
   requires `control_channel_open` → `take_control` (+ `presence` keepalive) before it accepts control
   frames, and requires `enable` for any motion/fire. The frontend only streams raw joystick datagrams
   — no handshake, no `take_control`, no enable bit — so ownership would never be granted. This is a
   second, independent reason the web path cannot drive the turret today.
4. **Web cockpit video off by default.** The frontend WHEP URL comes only from `/config.json`'s
   `whepUrl`, which the backend sets only when env `WHEP_URL` is provided. `VITE_WHEP_URL` in
   `.env.development` is dead code. Out of the box the cockpit shows `NO SIGNAL`.
5. **Broken Claude Code hooks.** [`.claude/settings.local.json`](../.claude/settings.local.json)
   registers PreToolUse hooks `.claude/hooks/guard-bash.sh` and `.claude/hooks/guard-read.sh`, but the
   `.claude/hooks/` directory does not exist.
6. **web Dockerfile dependency gaps.** [`services/web/Dockerfile`](../services/web/Dockerfile) installs
   only `pywebtransport`, but `backend/main.py` also imports `aiohttp` (declared in the top-level
   `services/web/requirements.txt`, not `backend/requirements.txt`). Additionally, `backend/main.py`
   shells out to the **`openssl` CLI** at startup to compute the cert hash; `openssl` is not in the
   `python:3.12-slim` image, and the failure is swallowed → `certHash` is omitted and the browser
   WebTransport handshake fails.
7. **Cert host mismatch.** `.env.development` uses `https://127.0.0.1:4433/` for WebTransport, while
   the backend issues its cert for `localhost` and explicitly warns against `127.0.0.1`. The frontend
   only falls back to `VITE_WT_URL` if `/config.json` fails. Also, the backend mints a **new
   self-signed cert on every startup**, so a backend restart changes `certHash` and silently breaks the
   browser session until a full reload.
8. **Stale reference stub.** `research/reverse_protocol/old/test_control.py` imports a `main` from a
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
- There is **no software "must be armed to fire" interlock** anywhere. Whether a shot actually leaves
  the barrel depends entirely on how the turret firmware interprets `fire`/`arm`/`fire_seq`.

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

### Input-merge hazards (web frontend)

`core/loop.ts` OR-merges buttons across gamepad | keyboard | virtual keys, and `gamepad.ts`
auto-adopts the first connected pad. A stuck/idle controller asserting button 0 (fire) or 3 (arm)
injects FIRE/ARM that keyboard input cannot mask. Note also that **Space means `fire` in the TTY
controller but `arm` in the browser** — a cross-tool muscle-memory hazard.

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
