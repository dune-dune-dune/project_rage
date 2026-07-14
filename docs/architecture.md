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

**Deploy targets.** *Local laptop (macOS)* — Docker Desktop has no host networking, so only
`video_gateway` runs in Docker and the cockpit runs natively (`run-native.sh`). *Production (Jetson, Linux)*
— **everything runs in Docker**: the CI job [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)
connects over WireGuard, SSHes in, `git reset --hard`, and runs `docker compose` with
`COMPOSE_FILE=docker-compose.yml:docker-compose.jetson.yml`. The
[`docker-compose.jetson.yml`](../services/web/docker-compose.jetson.yml) override adds the Benewake
TF03-180 serial rangefinder (`devices: /dev/ttyUSB0` + `RANGEFINDER_ENABLED=true`) — the only piece that
exists in production but not locally.

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
Browser (WASD momentary / 1-3 speed level / 4=SLOW toggle / 5=camera-mode toggle /
         F=safety toggle / Space=hold-fire / Shift=hold-rangefind)
  → on change + ~150 ms heartbeat → POST /api/input
        {up,down,left,right,safety,fire,fire_mode,speed_level,slow,camera_mode,rangefinder}
      (a WebSocket /api/ws path exists with the same payload/handler but is OFF by default — USE_WS in cockpit.js)
  → Flask updates lock-guarded intent + deadman timestamp
  → background sender thread @ 20 Hz → build_generated_command_packet()
      (manual velocity soft-started via a per-axis ramp: 0→full over ramp_ms, no start jerk)
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
- **Position-hold (start-jerk fix).** The one-time jerk at movement start came from the *position*
  channel, not velocity. The cockpit used to keep the `ROT_P`/`ELE_P` valid bits **off** at idle and
  flip them **on** while jumping the target `0 → ±π` on the first move packet — that rising edge +
  far-target jump kicks the turret once before `VEL_PRIO` settles. It now mirrors the reference (whose
  `flags2` is a constant `0x3f` across idle and motion): `_axis_position` keeps the P valid bits **on
  continuously**, commands the turret's **current angle** (parsed from the 32-byte status reply,
  `_update_angles_from_reply`) when idle so it holds without drift, and leads that angle by
  `_POSITION_LEAD_RAD` (90°, clamped to ±π) in the travel direction while moving — a modest step, never a
  `±π` jump. Until the turret reports an angle it falls back to the old `±π`/off scheme.
- **Turret telemetry.** `_ingest_reply` dispatches inbound replies by length: the 32-byte status reply
  (`_update_status_from_reply`) yields the angles above plus `distance_mm`; the 36-byte telemetry reply
  (`_update_telemetry_from_reply`) yields battery %, battery voltage, per-axis motor temperature and
  current. `snapshot()` exposes them (`angle_rot_deg`/`angle_ele_deg`, `distance_m`, `battery_percent`,
  `battery_voltage`, `motor_temp{x,y}`, `motor_current{x,y}`) and the HUD renders them as badges. Scales
  follow docs/protocol.md (voltage ×0.01, battery raw/0xFFFF); temperature (raw °C) and current (assumed
  ×0.01 A) scales are undocumented — adjust in `_update_telemetry_from_reply` if real readings look off.
- **Serial rangefinder (Benewake TF03-180).** On the Jetson a USB LiDAR is wired to `/dev/ttyUSB0`. A
  dedicated `TurretController` daemon thread (`_run_lidar_loop`, started only when `RANGEFINDER_ENABLED`)
  opens the serial port (`pyserial`, imported lazily; retries on open/read errors), syncs on the `0x59 0x59`
  header and parses the standard 9-byte TF03 UART frame (`parse_tf03_frame`: header + little-endian distance
  in cm + strength + temperature + 1-byte checksum). It runs independently of the 20 Hz command loop so a
  blocking read never stalls control. `snapshot()` then serves `distance_m` from this reading **while fresh**
  (`_LIDAR_STALE_SECONDS = 1 s`, else `null` → HUD shows `—`). When `RANGEFINDER_ENABLED` is false (local),
  `distance_m` falls back to the turret's own status-reply `distance_mm`. The frontend is unchanged — the
  crosshair panel's `#cp-dist` already renders `distance_m`. The device is passed into the container by
  [`docker-compose.jetson.yml`](../services/web/docker-compose.jetson.yml) (`devices: /dev/ttyUSB0`).
- **Velocity soft-start.** A secondary nicety (not the jerk fix): manual velocity is slew-rate limited,
  ramping toward the target by `accel_per_tick` each tick over `[control] ramp_ms` (default 250 ms). The
  reference ramps velocity too (see the `idle_*_idle` captures). `ramp_ms=0` disables it. Auto-track
  bypasses the ramp but keeps its state in sync so an aim→manual handoff does not step. State resets to 0
  on the deadman neutral packet.
- **Control transport.** Operator intent is sent via `POST /api/input` (the reliable default). A
  WebSocket **`/api/ws`** (flask-sock, same Flask app / port / single worker) is implemented but **OFF by
  default** in the client (`USE_WS=false` in `cockpit.js`): a half-open WS can report `readyState===OPEN`
  while silently dropping frames, black-holing the heartbeat → deadman flap → ENABLE-drop clunk each
  cycle. It needs real-hardware validation before enabling. Both paths call the same `apply_input`, so
  the 20 Hz loop/deadman are transport-agnostic. The PIN gate is app-wide (`before_app_request`), so it
  guards `/api/ws` too.
- **Deadman.** If no browser input arrives for `deadman_ms` (default 400 ms), the sender forces neutral.
- **Dry-run.** `RWS_DRY_RUN=true` (default) never opens the socket; packets are built and logged only.
- **Crosshair.** An adjustable aiming crosshair (⚙ panel) is persisted to SQLite via
  `GET`/`POST /api/crosshair` for reuse by later tooling.
- **Auto-track aim override.** When the browser auto-tracker is active it POSTs a normalised aim velocity
  to `/api/track` (`{active, rot, ele}`, each in [-1, 1]). `apply_track` stores it under the same lock and
  refreshes the deadman. In `_build_packet`, an active+fresh aim **replaces** the WASD-derived motion with
  the **exact same packet recipe as a held manual key** — proportional `rotation_v`/`elevation_v` plus a
  full-scale ±π position target and the `*_P` valid bits (per axis sign). This matters: a velocity-only
  packet (no position target/P bits) did **not** move the real turret, whereas the manual recipe does, so
  auto-track commands it identically, only with a proportional velocity. It never touches `arm`/`fire` —
  **auto-track aims, it never fires.** A separate `aim_timeout_ms` (default 500 ms) zeroes the aim if the
  browser stops sending.

## AI detection & auto-track path

Detection and target selection run **entirely in the browser** ([`app/static/ai.js`](../services/web/app/static/ai.js)
+ [`ai-worker.js`](../services/web/app/static/ai-worker.js), ONNX Runtime Web) because that is where the
frames, the active camera, the digital zoom and the crosshair offset all live; the Flask process (which owns
the safety-critical 20 Hz sender thread) never decodes video and gains no torch/GPU dependency.

**Two detection modes on the `I` key** (cycle OFF → YOLO → CUSTOM → OFF): **YOLO** runs the ONNX model in
the worker; **CUSTOM** is a model-free pixel **motion** detector on the main thread — consecutive downscaled
frames are diffed, pixels whose colour changes by more than the ⚙ `motion_thresh` % are marked moving,
dilated and clustered into blobs (connected components), and blobs whose longer side exceeds `min_size`
source-frame px are emitted as targets. CUSTOM does **ego-motion compensation**: the camera's global pan/tilt
between frames (≈ an image translation) is estimated by 1D-correlating luminance projection profiles, and the
previous frame is aligned by that shift before diffing — so when the turret slews the moving *background*
cancels and only objects moving independently of the camera survive (a whole-frame "motion" guard drops
frames where compensation fails). Both modes feed the same overlay draw + auto-track servo, so `T` tracks a
motion blob exactly as it tracks a YOLO box.

**YOLO inference runs in a Web Worker.** The main thread grabs the frame (2D-canvas `drawImage(video)` +
black letterbox + `getImageData` — the *exact* pixel path of the proven main-thread version, so detection
quality is preserved) and transfers the raw RGBA buffer (zero-copy) to the worker, which only builds the
tensor, runs ONNX inference, decodes and NMSes. This split is deliberate: single-threaded WASM inference
blocks its thread for 100–300 ms/frame, and on the *main* thread that would starve the `setInterval` timers
streaming control input (WebSocket `/api/ws`) + the heartbeat, tripping the 400 ms deadman and making manual control jerk or die.
Off-thread, manual control stays smooth while AI is on. Only one frame is in flight at a time (`busy` gate),
which throttles submission to the worker's actual inference rate. The auto-track command is DECOUPLED from
the detection rate: detection only updates a target velocity, and a fixed 10 Hz timer re-POSTs it — so the
turret tracks smoothly and the server aim never times out even when detection runs at only a few Hz.

```
Key I (cycle) → YOLO: main thread drawImage(<video>)→getImageData → transfer px to
  worker: tensor → ONNX YOLOv8 → decode [1,4+nc,N] → filter conf (⚙) → min_size → NMS
  CUSTOM: main thread frame-diff → threshold (⚙ motion %) → dilate → connected
  components → filter min_size → blobs (no model, no worker)
  main thread: draw boxes on #detections (cover + zoom mapping, matches #video)
Key T (AI on) → on each result: pick target nearest the crosshair (then nearest to
  previous lock) → error = target − crosshairFrame (normalised) → deadzone →
  rot = clamp(gain·errX), ele = clamp(−gain·errY) → POST /api/track
  (no target visible → POST active:false, so manual WASD works until one appears)
```

- **Closed-loop visual servo.** No camera FOV/lens calibration exists, so pixels cannot be mapped to an
  absolute turret angle. Tracking instead drives *velocity proportional to the pixel error* and lets the
  camera feedback null it to zero — robust without calibration; `gain`/`deadzone`/`max_velocity`
  (`settings.toml [track]`) tune the feel.
- **Crosshair offset is honoured.** The aim point is the crosshair's viewport position
  `((50+cross.x)%, (50+cross.y)%)`, **not** the screen centre. `ai.js` inverts the `object-fit: cover` +
  `scale(zoom)` transform to express it in the same frame-normalised space as the detections, so the target
  is centred on the *offset* crosshair.
- **Camera-agnostic.** Inference reads the same `<video>` element the operator sees, so `TAB` switching
  cameras (95 ↔ 96) needs no server change; a switch just drops the current target lock.
- **Model conversion (one-off, offline).** `scripts/export_onnx.py` converts `data/model/best.pt` →
  `best.onnx` (+ `classes.json`) with ultralytics (`requirements-export.txt`, dev-only);
  `scripts/fetch_ort.sh` vendors onnxruntime-web into `app/static/vendor/` (served locally, no runtime CDN).
  Detection thresholds (`conf`, `min_size`) persist to SQLite via `/api/ai-settings`.

## Video path

The MediaMTX gateway itself works; a browser pointed directly at its WHEP endpoint gets video:

```
Turret cameras 192.168.88.95 / .96  (RTSP :554, streams av0_0 / av0_1 / av0_2)
  → MediaMTX video_gateway pulls on demand over UDP
  → WHEP POST http://192.168.88.33:8889/cam95_main/whep
  → WebRTC (media UDP :8189, STUN for ICE) → <video> element
```

The Flask cockpit renders the camera list into the page (`window.__CAMERAS__`) from the **active network
profile in SQLite**, rebuilt on every `GET /` (`routes.index()` → `NetworkStore.cameras()`). It is *not* an
env var any more: `WHEP_URL` / `WHEP_BASE` / `VIDEO_GATEWAY_HOST_IP` are gone from the app. If the stream
is unreachable the HUD shows `NO SIGNAL`.

**Local vs remote profile.** The cockpit always reaches the turret over the LAN; only the *browser* moves
between the turret LAN and the WireGuard VPN, and it fetches WHEP straight from MediaMTX. So the gateway
address is a two-profile setting (⚙ → «Налаштування мережі», `GET`/`POST /api/network-settings`):

| profile | gateway host | default stream paths |
| --- | --- | --- |
| `local` | `192.168.88.33` | `cam95_h264` / `cam96_h264` |
| `remote` | `10.20.100.1` (VPN) | `cam95_main` / `cam96_main` |

Host and paths are editable per profile; labels (`CAM 95`/`CAM 96` — `cockpit.js:cameraKind()` derives the
lens type from them) and the WHEP port (8889) are server-side constants. An invalid host/path is rejected
and the previous value kept — the value is interpolated into a URL the browser POSTs its SDP to, so it is
validated against `^[A-Za-z0-9.\-]{1,253}$` / `^[A-Za-z0-9_\-]{1,64}$`. Saving reloads the page, because the
`<video>` elements and their `RTCPeerConnection`s are built once at load. **Recovery:** `GET /?video=local`
forces the local profile for one page load without saving, so a typo'd gateway cannot lock the operator out.

`MTX_WEBRTCADDITIONALHOSTS` advertises **both** hosts (compose default `192.168.88.33,10.20.100.1`,
overridable with `MEDIAMTX_HOSTS`), so switching profiles needs no container restart — at the cost of the
browser timing out the two dead ICE candidates on connect.

**Codec note.** All camera streams are **H265/HEVC**, which only Safari (and Chrome on HEVC-capable
hardware) can play over WebRTC. For cross-browser video the gateway exposes **H264-transcoded** paths
via ffmpeg `runOnDemand` (requires the `bluenviron/mediamtx:1.18.2-ffmpeg` image):

- `cam95_h264` / `cam96_h264` — **default, low-latency**: transcode the 640×480 sub-stream (`av0_1`).
  The SD stream always encodes faster than real time, so latency does **not** accumulate (1080p
  software transcode can dip below real time and grow glass-to-glass latency to seconds). Tuned with
  `-fflags nobuffer -flags low_delay`, x264 `zerolatency`, and a 0.5 s keyframe interval.
- `cam95_h264_hd` / `cam96_h264_hd` — 1080p `av0_0`, heavier; use only with CPU headroom.

⚠️ The `remote` profile defaults to `cam*_main`, i.e. the **raw H265 1080p** pull. Outside Safari the
WebRTC connection comes up but nothing decodes (video dot green, picture black) — switch the paths to
`cam*_h264` in the same panel if that happens.

The cockpit's **TAB** key cycles the active profile's stream list (from the database, no longer from
`settings.toml`). RTSP pulls use **TCP** because UDP RTP times out through Docker Desktop's NAT on
macOS/Windows.

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
| Rangefinder enable (TF03-180) | `RANGEFINDER_ENABLED` | `false` (Jetson: `true` via `docker-compose.jetson.yml`) |
| Rangefinder serial port | `RANGEFINDER_PORT` | `/dev/ttyUSB0` |
| Rangefinder serial baud | `RANGEFINDER_BAUD` | `115200` |
| Gunicorn bind | `WEB_BIND` | `0.0.0.0:8000` |
| Gunicorn workers / threads | `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `1` / `8` |
| Log level | `LOG_LEVEL` | `info` |
| Login PIN (7 digits) | `COCKPIT_PIN` | empty → login disabled (open) |
| Session secret | `SECRET_KEY` | empty → ephemeral key (sessions reset on restart) |
| MediaMTX advertised ICE hosts | `MEDIAMTX_HOSTS` (compose only) | `192.168.88.33,10.20.100.1` |
| Settings data dir | `COCKPIT_DATA_DIR` | `services/web/data` |

The video gateway address is **not** an env var: it lives in the settings database (see *Video path*).
`WHEP_URL` / `WHEP_BASE` / `VIDEO_GATEWAY_HOST_IP` were removed; leftover entries in an existing `.env`
are simply ignored.

### Settings database (SQLite)

Everything the operator can change at runtime — crosshair offset, AI thresholds, map origin, video/network
profiles — is stored in `data/cockpit.db` ([`db.py`](../services/web/app/db.py) +
[`store.py`](../services/web/app/store.py)). SQLite is a library, not a server: no extra container, no new
dependency (stdlib `sqlite3`), and the file sits in the existing `./data` bind mount, so it survives
`docker compose down`, image rebuilds and the deploy's `git reset --hard`.

- **Schema = SQL files.** `app/migrations/*.sql` are applied once, in filename order, at startup
  (`SettingsDb.migrate()` from `create_app`), each inside one transaction together with its
  `schema_migrations` row. Applied versions are skipped on every later boot; a failing migration rolls back
  and aborts startup. Adding a setting = adding `000N_*.sql`. Files are **append-only** (the engine tracks
  names, not checksums) — see `app/migrations/README.md`.
- **Legacy import.** On the first boot the pre-SQLite `data/{crosshair,ai_settings,map_settings}.json` are
  imported (only when the DB has no row for that section) and renamed to `*.json.migrated`, so a lost
  `cockpit.db` cannot silently resurrect stale settings.
- **Concurrency.** One Gunicorn worker (8 gthreads); each call opens its own short-lived connection
  (`busy_timeout=5000`, WAL best-effort). The 20 Hz turret thread never touches the database.

Control **tuning** lives separately in [`settings.toml`](../services/web/settings.toml) (read via
stdlib `tomllib`, mounted read-only into the container so it can be edited without a rebuild):
`[control]` send_rate_hz (20), deadman_ms (400), ramp_ms (250, velocity soft-start; 0 disables),
speed_percent (100), `speed_levels` (percent list
selectable with keys 1..N, default `[100, 50, 1]`); `[axes]` rotation/elevation unit
amplitudes; `[fire]` mode + short/medium durations; `[track]` AI visual-servo `gain` (2.5), `deadzone`
(0.02), `max_velocity` (0.5), `aim_timeout_ms` (500), `imgsz` (640, must match the ONNX export).

**Authentication:** an app-wide `before_app_request` gate (`routes.py`) protects the cockpit when
`COCKPIT_PIN` (7 digits) is set — unauthenticated page requests redirect to `/login`, `/api`+`/assets`
get `401`; `/healthz`, `/login` and static assets are public. It is registered `before_app_request`
(not blueprint-scoped) specifically so it also gates the flask-sock `/api/ws` route, which is attached
to the app rather than the blueprint. `POST /login` compares the PIN with
`hmac.compare_digest` and sets a signed session (secret = `SECRET_KEY`). Empty `COCKPIT_PIN` = open access.

**Rotation-speed levels:** keys `1`/`2`/`3` post `speed_level` on `/api/input`; the controller multiplies
manual-motion velocity by `speed_levels[level-1]/100` (auto-track is unaffected — it has its own
`max_velocity` cap). The default `[100, 50, 1]` maps key `1` → 100 %, key `2` → 50 %, key `3` → a **1 %
fine-aim level** (0.8 × 0.01 → int16 262 — small, but well clear of zero; the parser's floor is 1 %, not
10 %). The boot default is the level with the **highest percent** (`Settings.default_speed_index`), an
argmax rather than the last entry, so the list can be ordered by key instead of by speed.

Picking any level also **clears the key-4 `slow` flag** (`FLAGS1_SLOW`) client-side: both are ways to slow
the turret, and a latched hardware SLOW would keep scaling down the level the operator just selected. The
server does not enforce this — the browser owns the intent and simply stops sending `slow: true`.

**Instant camera switching:** the client pre-connects a persistent `RTCPeerConnection` + its own
`<video>` per camera at load (STUN dropped — LAN candidates are local); `TAB` only flips which
pre-decoded stream is visible. `video_gateway` keeps the H264 transcodes warm (`runOnDemandCloseAfter:
60s`).

WebSocket route ([`ws.py`](../services/web/app/ws.py)): `/api/ws` — an alternative control-input channel
(flask-sock) that feeds the same `apply_input`. **Disabled by default** on the client (`USE_WS=false`)
pending hardware validation; `POST /api/input` is the active path.

HTTP routes ([`routes.py`](../services/web/app/routes.py)): `GET /` (cockpit page), `GET /healthz`,
`GET`/`POST /login`, `GET /logout`,
`POST /api/input` (JSON intent incl. `speed_level`, `slow`, `camera_mode`, `rangefinder` → controller, 204; active control path), `GET /api/status` (HUD snapshot,
incl. `track_active`, `speed_level`, `speed_levels`, `slow`, `camera_mode`, `rangefinder_seq`, and turret telemetry: `angle_rot_deg`/`angle_ele_deg`,
`camera_angle_deg`, `distance_m`, `battery_percent`/`battery_voltage`, `motor_temp`, `motor_current`,
`motor_voltage`, `motor_rpm`, `voltage_fire`, `voltage_cpu`), `GET`/`POST /api/crosshair`,
`GET`/`POST /api/network-settings` (video profiles + active mode),
`POST /api/track` (auto-aim velocity → controller, 204),
`GET`/`POST /api/ai-settings` (conf, min size, Custom motion threshold, ego-motion max shift),
`GET /assets/model.onnx` (exported weights),
`GET /assets/classes.json` (class names).

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
- `MTX_WEBRTCADDITIONALHOSTS` fed from `MEDIAMTX_HOSTS` (default `192.168.88.33,10.20.100.1`) so WebRTC
  advertises the right host IPs for ICE — clients are not on the docker network, and **both** the LAN and
  the VPN address are advertised so the cockpit's local/remote switch needs no restart. Note the Jetson's
  `.env` is never rewritten by the deploy, so in production the compose default is what actually applies.
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
4. **Web cockpit video depends on the active network profile.** The camera URLs come from the settings
   database (⚙ → «Налаштування мережі»), not from `.env`. It needs the `video_gateway` up and the cameras
   reachable *at the active profile's gateway host*. Two traps: (a) a saved-but-wrong host reloads the
   cockpit into a config with dead video — recover with `GET /?video=local`; (b) the `remote` profile
   defaults to `cam*_main`, which is **H265** and decodes only in Safari — elsewhere the connection
   succeeds and the picture stays black. Switch those paths to `cam*_h264` in the same panel.
5. **Broken Claude Code hooks.** [`.claude/settings.local.json`](../.claude/settings.local.json)
   registers PreToolUse hooks `.claude/hooks/guard-bash.sh` and `.claude/hooks/guard-read.sh`, but the
   `.claude/hooks/` directory does not exist.
6. **Stale reference stub.** `research/reverse_protocol/old/test_control.py` imports a `main` from a
   module `test_rws_control` that does not exist under `old/`. The old CLI's illustrative burst
   durations (100/1000/10000) do not match the real captured values (161/605/0).
7. **AI mode needs a one-off build step.** `GET /assets/model.onnx` 404s until
   `scripts/export_onnx.py` converts `data/model/best.pt` → `best.onnx`, and pressing `I` shows
   `AI NO MODEL` / `AI ERROR` until both that and `scripts/fetch_ort.sh` (vendors onnxruntime-web into
   `app/static/vendor/`) have run. `data/` and the vendored ORT files are gitignored/uncommitted, so a
   fresh checkout or container must regenerate them. Browser inference speed depends on the client device
   (single-thread WASM/SIMD); tracking tolerates a few Hz but a weak client will track sluggishly.
8. **Auto-track is uncalibrated and open-loop on direction sign.** With no camera FOV data the servo
   assumes image-right = turret-pan-right and image-down = tilt-down; if a camera is mounted mirrored the
   `gain` sign (or axis) would need flipping. It is aim-only and never fires, but it *does* move a real
   turret — run it with `RWS_DRY_RUN=true` first and keep the deadzone/gain conservative.

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
