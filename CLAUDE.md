# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Working rules (mandatory)

- **Reply to the user in Ukrainian.** All chat responses in this repo must be written in Ukrainian.
- **Write code comments in English.** All comments and docstrings in source code must be in English.
- **Keep this file current.** Whenever the project changes in a way that makes anything below
  inaccurate (new service, changed port/IP, protocol change, resolved/added gap), update `CLAUDE.md`
  (and the relevant `docs/`) as part of the same change.

## Project overview

`project_rage` is a control stack for a remotely operated **water-shooting turret** (RWS — Remote
Weapon Station). The mature, working control path is a standalone keyboard controller
(`test_rws_control.py` + `rws_control.py`) that streams 40-byte UDP command packets to the turret at
20 Hz. A **Flask + Gunicorn web cockpit** (`services/web/`) offers a browser control path: full-screen
WHEP video plus WASD/F/Space keys, with a background thread streaming the same 40-byte RWS UDP commands
directly to the turret at 20 Hz (reusing `rws_control.py`, bypassing `rws_bridge`). `rws_bridge` remains
a separate standalone driver; `video_gateway` serves the camera video. The cockpit also has an
**AI mode** (key `I`): browser-side YOLO detection (ONNX Runtime Web) over the live video, plus an
**auto-track** (key `T`) that drives the turret with a proportional visual servo to centre the nearest
target on the crosshair. Auto-track only aims — it never fires.

⚠️ **Safety-critical.** Commands go to a real turret by default (`192.168.88.56:7780`). Use
`--dry-run` for any test without hardware. Never send `arm`/`fire` unless the task explicitly requires
live firing on authorized hardware. Note: the code has **no software "must be armed to fire"
interlock** — `fire='F'` is transmitted whenever Space is held, independent of `safetyARM`/`enable`;
`safetyARM` only gates the `arm='A'` byte. There are also **no software angle/sector limits**, and
inbound turret replies are **not checksum-verified**. Full list:
[docs/architecture.md → Safety caveats](docs/architecture.md#safety--control-correctness-caveats).

## Project structure

```
project_rage/
├── rws_control.py            # Core protocol library: 40-byte command build, reply parse, checksum
├── test_rws_control.py       # Interactive TTY keyboard controller (the working control path)
├── compose.yaml              # Docker Compose — defines ONLY video_gateway
├── README.md                 # Ukrainian quick-start + control key map
├── CLAUDE.md                 # This file
├── docs/
│   ├── architecture.md       # Components, data flows, ports/IPs/env, safety model, Known gaps
│   └── protocol.md           # Full RWS UDP protocol reference (packets 40/32/36 bytes)
├── services/
│   ├── rws_bridge/           # Async turret driver: WebSocket control + 20 Hz RWS loop + lease watchdog
│   │   └── src/{main,server,bridge,rws,protocol,config}.py
│   ├── web/                  # Flask + Gunicorn cockpit (browser → RWS UDP → turret)
│   │   ├── app/{__init__,config,turret,routes,ws,db,store,wsgi}.py  # factory, settings, control, routes, /api/ws, SQLite, stores
│   │   ├── app/migrations/*.sql  # schema + seed, applied once at startup (see its README)
│   │   ├── app/templates/{index,login}.html + app/static/{cockpit.js,ai.js,ai-worker.js,heartbeat-worker.js,map.js,compass.js,cockpit.css}  # video + HUD + YOLO (worker) + bg-tab heartbeat worker + map/gauges + compass + PIN login
│   │   ├── app/static/vendor/  # onnxruntime-web (fetch_ort.sh) + leaflet/ (fetch_leaflet.sh)
│   │   ├── scripts/{export_onnx.py,fetch_ort.sh,fetch_leaflet.sh}  # one-off: best.pt→best.onnx, fetch ORT web, vendor Leaflet
│   │   ├── tests/ + conftest.py + pytest.ini  # pytest suite (speed, ramp, timing, turret, auth, routes, ws, db, network)
│   │   ├── requirements-dev.txt  # pytest (dev-only; runtime stays torch/pytest-free)
│   │   ├── data/cockpit.db   # SQLite: crosshair, AI, map, video/network profiles (gitignored)
│   │   ├── data/model/best.pt (+ best.onnx, classes.json)  # YOLO weights (gitignored runtime data)
│   │   ├── settings.toml     # control tuning (rates, ramp_ms, axes, fire, speed_levels, [track] AI servo) — NOT secrets
│   │   ├── .env.example       # turret-network/deploy env template incl. COCKPIT_PIN/SECRET_KEY (user creates .env)
│   │   ├── Dockerfile + docker-compose.yml   # cockpit (:8000, host net) + video_gateway
│   │   ├── docker-compose.jetson.yml # prod override: /dev/ttyUSB0 rangefinder passthrough + RANGEFINDER_ENABLED
│   └── video_gateway/mediamtx.yml   # MediaMTX: RTSP cameras → WebRTC/WHEP
└── research/
    └── reverse_protocol/
        ├── unit_protocol.md  # PRIMARY protocol spec (Ukrainian) — source of truth
        └── old/read_only/    # Reference vendor code + salt.bin + pcap captures
```

## Core protocol cheat-sheet

Full detail: [docs/protocol.md](docs/protocol.md). Source spec:
[research/reverse_protocol/unit_protocol.md](research/reverse_protocol/unit_protocol.md).

- **Transport:** UDP, big-endian. Controller `192.168.88.33:7770` → turret `192.168.88.56:7780`, 20 Hz (50 ms).
- **Packets (identified by length):** command = **40 B**, status reply = **32 B**, telemetry reply = **36 B**.
- **Framing:** 4-byte header (`packet_type`, `pad0`, `sequence` uint16) + body + 4-byte checksum.
- **Checksum:** `SHA256(packet_without_last_4_bytes ‖ salt32)[:4]`, `salt32` = 32 bytes from
  `research/.../salt.bin` (`262bd7b6…fa277`), embedded as `DEFAULT_EMBEDDED_SALT` / `RWS_SALT`. This
  salt is the shared key authenticating every command.
- **Command flags1:** `ENABLE=0x01`, `SLOW=0x02`, `RELOAD=0x04`, `FORCE_HOME=0x08`.
- **Command flags2:** `ROT_V=0x01`, `ELE_V=0x02`, `ROT_P=0x04`, `ELE_P=0x08`, `VEL_PRIO=0x30` (both bits).
- **Magic bytes:** `arm='A'(0x41)` armed / zero disarmed; `fire='F'(0x46)` fire / zero idle.
- **Fire durations:** short=161, medium=605, manual=0.
- **Encodings:** velocity int16 = ±1.0 × `0x7FFF`; angle int32 = radians on ±π; voltage ×0.01;
  battery uint16/`0xFFFF`; distance uint32 mm.

## How to run and control

```bash
# Working keyboard controller (Python 3.10+, POSIX TTY). Real turret by default.
python3 test_rws_control.py
# Safe test without hardware:
python3 test_rws_control.py --dry-run --verbose --packet-limit 5

# Web cockpit (Flask + Gunicorn). Copy services/web/.env.example → services/web/.env first.
# Linux (full stack in Docker):
cd services/web && docker compose up --build          # cockpit :8000 + video_gateway :8889
# Jetson / production (everything in Docker + TF03 rangefinder passthrough):
cd services/web && COMPOSE_FILE=docker-compose.yml:docker-compose.jetson.yml \
  docker compose up -d --build                        # adds /dev/ttyUSB0 + RANGEFINDER_ENABLED
#   (this is exactly what .github/workflows/deploy.yml runs over WireGuard+SSH.)
# macOS/Windows (Docker Desktop has no host networking → cockpit cannot bind the RWS
# source IP in a container). Run video in Docker, cockpit natively on the host:
cd services/web && docker compose up video_gateway    # video only
cd services/web && ./run-native.sh                    # cockpit natively (auto venv on py3.11+)
#   - run-native.sh / gunicorn.conf.py auto-load services/web/.env (python-dotenv).
#   - the sender retries the socket bind until RWS_SRC_IP is configured on the host.

# Other services
docker compose up video_gateway                                       # video (root compose)
#   MediaMTX advertises both ICE hosts (LAN + VPN) by default; override with MEDIAMTX_HOSTS=a,b
python3 services/rws_bridge/src/main.py                                # bridge, WS :8765
```

**Cockpit keys:** `WASD` = momentary move (hold to move; **always available, not gated by safety**),
`1`/`2`/`3` = rotation-speed level (velocity multiplier from `[control] speed_levels` = `[50, 100, 1]`:
`1` = 50 %, `2` = 100 %, `3` = **1 % fine aim**; the boot default is the *fastest* level, i.e. the highest
percent — **not** the last entry, so the fine level can sit at the end of the list and still land on key
`3`; auto-track is unaffected), `4` = toggle **SLOW/precise** mode (`FLAGS1_SLOW`; hardware
slow-motion, gates nothing), `5` = toggle **camera-drive mode** (while on, W/S steer the physical
camera axis `cameras_p` at `[camera] rate_deg_s`, clamped to `min_deg`..`max_deg`, and the turret
elevation holds; aim-only), `F` = safety toggle (**gates firing only**),
`Space` = hold to fire, `Shift` = hold to **range-find** (edge-paced `rangefinder_seq`, spacing
`[control] rangefinder_measure_interval_ms`; aim-only), `M` = cycle fire mode
(short/medium/manual), `Q`/`E` = digital zoom in/out, `TAB` = cycle camera, `I` = cycle AI mode
(**OFF → AI ON (YOLO) → AI CUSTOM → OFF**), `T` = toggle auto-track (any AI mode; aim-only, never fires).
AI CUSTOM is a model-free pixel **motion** detector (frame differencing): pixels whose colour changes by
more than the ⚙ threshold are clustered into blobs, and blobs exceeding the min-object-size are flagged as
a drone. The ⚙ button (top-left) opens a dropdown of settings panels: **мапа** (`/api/map-settings`),
**приціл** (H/V offset, `/api/crosshair`), **ШІ модель** (confidence threshold default 70 %, min object size
in px, Custom motion threshold %, `/api/ai-settings`), **мережа** (see below) and **алерти** (placeholder).
All of them persist to SQLite (`services/web/data/cockpit.db`), not to JSON files.
A **full-width instrument bar** (`#telemetry-bar`, a solid dark panel pinned to the
bottom edge, styled in `cockpit.css`) shows groups, each an SVG icon + label + value:
battery (%+V, `#battery`, whole item pulses red under 15%), motor temps (`#motemp` X/Y),
motor currents (`#mocur` X/Y), speed level (`#speed-bar`), **fire-circuit voltage**
(`#volt-fire`, «U ПОСТРІЛУ», from `voltage_fire`; pulses red under `FIRE_VOLTAGE_MIN`=20 V =
system not ready to fire), **CPU voltage** (`#volt-cpu`), **per-motor voltage** (`#mo-volt` X/Y,
from `voltage_x/y`), **motor RPM** (`#mo-rpm` X/Y, from `rpm_x/y`), **rangefinder distance**
(`#dist-bar`, «ДАЛЕКОМІР», from `distance_m` — the same source as the crosshair `#cp-dist`),
**camera-axis position** (`#cam-pos`, from the status reply `cameras_p` → `camera_angle_deg`), and a
**«Статус підключення»** group with two coloured dots — turret
(`#dot-turret`, green/red from `s.link`/`dry_run`/`bind_error`) and video (`#dot-video`, green/red from
the active camera's `RTCPeerConnection.connectionState`). Each dot's hover tooltip (`title`) shows
«Статус турелі/відео: онлайн/офлайн» (`setDot`/`paintVideo` in `cockpit.js`, `.ok`/`.bad` classes);
telemetry values dim (`.stale`) until their reply arrives. The bar is an opaque overlay over the video's
bottom edge (the video stays full-size behind it, so crosshair/AI aim geometry is untouched); `#hud` sits
just above it. Azimuth/elevation are no longer shown in the bar but still cached (`lastAzDeg`/`lastElDeg`)
for the map widgets. A small **crosshair status panel** (`#cross-panel`, a child of `#crosshair` so it
tracks the reticle offset) sits at the crosshair's lower-right and shows the rangefinder distance
(`#cp-dist`, from `/api/status.distance_m`; on the Jetson this is the serial **Benewake TF03-180**
LiDAR — see below — otherwise the turret's own status-reply distance) plus the camera lens type
(`#cp-camtype`: CAM 95 → Ширококутна, CAM 96 → Вузькокутна via
`cameraKind`) and digital zoom (`#cp-zoom`). Below the camera line a `#cp-state` row shows boxed
indicators in order **safety · AI · track · slow · camera · fire-mode**: a **safety padlock** (`#cp-safety`: closed+green
outline when safe, open+red outline when armed — synced to `s.safety_off` in `pollStatus`, which also
recolours the reticle green/red via `crosshairEl.style.color`); an **AI square** (`#cp-ai`: grey box +
hand icon = manual/OFF, green «AI»/«AI+» = YOLO/custom); a **track square** (`#cp-track`: grey «T» off,
green on); a **slow square** (`#cp-slow`: «S», green when SLOW mode on, from `s.slow`); a **camera
square** (`#cp-cammode`: «C», green when camera-drive mode on, from `s.camera_mode`); and a **fire-mode box** (`#cp-fire` wrapping `#cp-firemode`, `data-mode` set in `paintKeys`:
`•` short / `•••` medium / `▬` manual). AI/track mirror the `#ai`/`#track` badges and are updated by
`AI.setBadges()` in `ai.js` (`.off` class toggles grey↔green; `#cp-ai`/`#cp-track`/`#cp-fire` are
`<div>`s, so `classList` works — unlike the `<svg>` `#cp-safety`, whose class must be set via
`setAttribute`). The `#hud` overlay (bottom-left) keeps the state badges
(`SAFE`/`FIRE`/`SPD`/`AI`/`TRACK`), the WASD/Space keys and the key-legend hint.

**Map cluster (top-right):** `map.js` renders a `#map-widgets` block — a Leaflet map (`#map-square`,
vendored `static/vendor/leaflet/`, **online OSM tiles**) centred on a saved origin, drawing the turret's
azimuth **sector** (radius polygon) plus a live azimuth needle; below it two square SVG gauges show the
azimuth range and the elevation range with live needles. The map's own ⚙ (`#map-settings-btn`) flips the
map to a settings form (only lat, lon, `north_correction`; Save → `POST /api/map-settings`, persisted in
`cockpit.db`). Live angles come from `window.cockpit.azDeg`/`.elDeg` (cached by
`pollStatus`, which calls `window.mapWidgets.update()` at 5 Hz). Bearing mapping:
`bearing = angle_rot_deg + north_correction`.

**Compass (top-centre):** `compass.js` renders `#compass` — a horizontal scrolling
degree tape (`#compass-tape`, SVG) with a boxed current-bearing readout above it
(`#compass-val`). It shows the SAME compass bearing as the azimuth gauge/map needle
(`norm360(azimuth + north_correction)`, 0…360, cardinals «Пн/Сх/Пд/Зх» at
0/90/180/270). It is driven by `map.js`'s `update()` (which owns the bearing math)
via `window.compass.update(bearing)`, so it refreshes at the same 5 Hz. The azimuth/elevation ranges (`az_min`/`az_max` = −72…72,
`ele_min`/`ele_max` = −8…30) are **fixed constants** in the store (used to draw the sector + gauges, not
user-editable). The AI/crosshair ⚙ button + `#settings-panel` are shifted left (`right: 292px`) so the
map owns the corner.

**Settings storage (SQLite):** every operator-tunable setting lives in `services/web/data/cockpit.db`
(stdlib `sqlite3` — no server, no extra container, no new dependency; the file sits in the existing
`./data` bind mount, so it survives rebuilds and the deploy's `git reset --hard`). Schema is **SQL-file
migrations**: `services/web/app/migrations/*.sql` are applied once, in filename order, at startup
(`app/db.py:run_migrations` from `create_app`), recorded in `schema_migrations`, and skipped forever after
— a new setting = a new `000N_*.sql` file, nothing else. Applied files are **append-only** (the engine
tracks names, not checksums — see `app/migrations/README.md`). The pre-SQLite `data/*.json` files are
imported once on first boot and renamed to `*.json.migrated` (`app/db.py:import_legacy_json`).

**Network settings (⚙ → «Налаштування мережі»):** picks which video gateway the *browser* pulls WHEP from
— **локальне** (turret LAN, `192.168.88.33`, paths `cam95_h264`/`cam96_h264`) or **віддалено** (WireGuard
VPN, `10.20.100.1`, paths `cam95_main`/`cam96_main`). Both the host and the MediaMTX stream paths are
editable per profile (`NetworkStore` in `store.py`, `GET/POST /api/network-settings`); labels and the WHEP
port are server-side constants, and an invalid host/path is rejected (keeps the previous value) because the
value is interpolated into a URL the browser then POSTs its SDP to. `routes.index()` builds
`window.__CAMERAS__` from the DB **per request**; saving reloads the page (the `<video>` elements and their
`RTCPeerConnection`s are built once at load). Recovery hatch: **`GET /?video=local`** forces the local
profile for one page load without saving, so a typo'd gateway cannot lock the operator out of the cockpit.
`MTX_WEBRTCADDITIONALHOSTS` advertises **both** IPs (compose default `192.168.88.33,10.20.100.1`,
override `MEDIAMTX_HOSTS`), so switching profiles needs no container restart. ⚠️ `cam*_main` is raw
**H265/1080p** — outside Safari WebRTC cannot decode it (connection goes green, picture stays black); that
is exactly why the paths are UI-editable, switch them to `cam*_h264` if the remote view is black.

`ENABLE` stays on for the whole live
session so the motors HOLD position (drops only on the deadman neutral packet); fire needs safety
disengaged (ARMED).

**Login (PIN):** if `COCKPIT_PIN` (7 digits) is set in `.env`, a `before_request` gate protects the
whole cockpit — everything except `/healthz`, `/login` and static assets redirects unauthenticated
page requests to `/login` and returns `401` for `/api`/`/assets`. `GET/POST /login` renders/validates a
minimal PIN page (`app/templates/login.html`; constant-time `hmac.compare_digest`), `GET /logout` clears
the session. Sessions are signed with `SECRET_KEY` from `.env` (set a stable value so they survive
restarts; otherwise an ephemeral key is used and a warning is logged). **Empty `COCKPIT_PIN` disables the
gate** (open access) — a warning is logged.

**Camera switching (instant):** the cockpit pre-connects a persistent `RTCPeerConnection` + its own
`<video>` for **every** camera at load; `TAB` only flips which pre-decoded stream is visible (no
renegotiation, no ffmpeg cold start, no ICE wait). STUN is dropped (LAN — host candidates are local).
`video_gateway`'s H264 transcodes stay warm via `runOnDemandCloseAfter: 60s`. `window.cockpit.videoEl` is
a getter returning the active camera's element so `ai.js` always reads the visible video.

**AI mode + auto-track** (`app/static/ai.js` + `ai-worker.js`): detection runs in the browser via ONNX
Runtime Web **in a Web Worker** (off the main thread, so inference never starves the input/heartbeat
timers that keep the 20 Hz stream + 400 ms deadman alive — manual control stays smooth while AI is on).
It reads the same `<video>` the operator sees — so it always runs on the ACTIVE camera and honours zoom.
`T` locks the target nearest the crosshair and POSTs a normalised aim velocity to `/api/track` at ~frame
rate; the `TurretController` overrides its velocity axes with that proportional command (visual servo —
no FOV calibration needed, no absolute angle). The aim point is the crosshair position **including its
programmatic offset**, computed by inverting the `object-fit: cover` + zoom mapping. First convert
`best.pt → best.onnx` and vendor ORT once: `python scripts/export_onnx.py` + `bash scripts/fetch_ort.sh`
(deps in `requirements-export.txt`, dev-only — the cockpit runtime stays torch-free). Vendor Leaflet for
the map widget once too: `bash scripts/fetch_leaflet.sh` (map *tiles* still need internet in the browser).

**TTY controller keys** (`test_rws_control.py`, [README.md](README.md)): `WASD` latch axes, arrows
momentary move, `1`/`2`/`4`/`5` = enable/slow/reload/forceHome, `Backspace` = safetyARM, `7`/`8`/`9` =
fire mode, `Space` = fire, `[`/`]` = speed, `V` = stop, `Q` = quit. Ukrainian layouts are also mapped.

## Architecture (short)

See [docs/architecture.md](docs/architecture.md) for full flows, port/env tables, and the safety
model. In brief:

- **Working:** `test_rws_control.py` → RWS UDP → turret. Video: cameras `.95`/`.96` (RTSP) → MediaMTX
  `video_gateway` → browser WHEP (`:8889`).
- **rws_bridge** is a self-contained driver: starts in **safe mode** (neutral packets), single-owner
  ownership with a 4 s lease, edge-triggered fire, 12-byte `control_state` input protocol over
  WebSocket (`:8765`).
- **web cockpit** (`services/web/`): Flask serves the page; a single `TurretController` background
  thread streams RWS UDP at 20 Hz. Movement is always available; the F safety gates **firing only**
  (software fire interlock: `fire='F'` only when safety disengaged). 400 ms deadman, single Gunicorn
  worker (sole UDP/sequence owner). Drives the turret directly, not via `rws_bridge`. A 7-digit-PIN
  login gate (`COCKPIT_PIN` in `.env`) protects all routes except `/healthz`/`/login`/static.
  Rotation speed is switchable at runtime with keys `1`/`2`/`3` (`speed_level` on `/api/input`, levels from
  `[control] speed_levels`; the boot default is the highest percent, `Settings.default_speed_index`). The one-time **jerk at movement start** was traced to the position channel:
  the cockpit used to toggle the `ROT_P`/`ELE_P` valid bits off→on and jump the target 0→±π on the first
  move packet. It now mirrors the reference — **P valid bits stay on continuously**, holding the turret's
  *current* angle (read from status replies) when idle and leading it by a modest amount
  (`_POSITION_LEAD_RAD`, 90°) when moving (`turret.py:_axis_position`); until the turret reports an angle
  it falls back to the old ±π scheme. A separate **velocity soft-start** ramp (`[control] ramp_ms`,
  default 250 ms) smooths the 0→full velocity step (auto-track bypasses it) — a nicety, not the jerk fix.
  **Background-tab position hold:** the 150 ms control heartbeat runs in a dedicated Web Worker
  (`static/heartbeat-worker.js`), whose timers are **not** throttled when the tab is backgrounded. A
  plain main-thread `setInterval` is clamped to ≥1 s in a hidden tab, which starved the 400 ms deadman
  and dropped `ENABLE` — the turret then de-energised and sagged off its aim point when the operator
  switched tabs. The worker keeps feeding the deadman so the turret **holds position** while hidden
  (motion/fire are zeroed on `blur`/`visibilitychange`, so it is a pure hold); if the browser really
  closes/crashes the worker dies with the page and the deadman still neutralises as before. The worker
  URL is `?v=`-stamped from `asset_version` (workers cache aggressively). Falls back to a
  (bg-throttled) main-thread interval if the worker cannot be created.
  **Control input transport:** the browser sends intent via `POST /api/input` (reliable default). A
  WebSocket path **`/api/ws`** (flask-sock) exists server-side but is **OFF by default** on the client
  (`USE_WS=false` in `cockpit.js`) pending real-hardware validation — a half-open WS can report
  `readyState===OPEN` while dropping frames, black-holing the heartbeat and tripping the deadman. HTTP
  routes: `/`, `/healthz`, `/login` (GET/POST), `/logout`, `/api/input`, `/api/status`,
  `/api/crosshair` (GET/POST), `/api/track` (POST auto-aim velocity),
  `/api/ai-settings` (GET/POST conf + min size), `/api/map-settings` (GET/POST map origin lat/lon +
  north_correction), `/api/network-settings` (GET/POST video profiles + active mode),
  `/assets/model.onnx`, `/assets/classes.json`;
  WebSocket route: `/api/ws` (control input). The PIN gate is registered app-wide
  (`before_app_request`) so it also protects `/api/ws`.
- **AI auto-track** runs client-side (`ai.js`, ONNX Runtime Web); the server only receives the resulting
  aim velocity via `/api/track` and applies it as a velocity override (aim-only — never touches `arm`/`fire`).
  A dedicated aim timeout (`[track].aim_timeout_ms`, default 500 ms) zeroes the aim if the browser stalls.
- **Serial rangefinder (Benewake TF03-180):** on the Jetson a USB LiDAR streams distance over serial. A
  dedicated `TurretController` reader thread (`turret.py:_run_lidar_loop`, `pyserial` imported lazily) parses
  the standard 9-byte TF03 UART frames (`parse_tf03_frame`) and caches the distance; `snapshot()` then serves
  it as `distance_m` (only while fresh — `_LIDAR_STALE_SECONDS = 1 s`, else `null → "—"`). It is gated by
  `RANGEFINDER_ENABLED` (env, default off) with `RANGEFINDER_PORT` (default `/dev/ttyUSB0`) and
  `RANGEFINDER_BAUD` (default 115200); when disabled (local), `distance_m` falls back to the turret status
  reply. The device is passed into the container by `docker-compose.jetson.yml` — a separate serial reader,
  independent of the 20 Hz command loop, so a blocking read never stalls control.

## Known gaps (do not assume these work)

Details in [docs/architecture.md#known-gaps](docs/architecture.md#known-gaps).

1. The web cockpit **bypasses `rws_bridge`** — it drives RWS UDP directly and does not use the bridge's
   ownership/lease/replay protection or telemetry. Run only one control path at a time against a turret.
2. Live control needs `network_mode: host` (Linux only) to bind `RWS_SRC_IP:RWS_SRC_PORT`. On Docker
   Desktop (macOS/Windows) only `RWS_DRY_RUN=true` works (the socket is never opened).
3. `GUNICORN_WORKERS` **must stay 1** (hardcoded in the Dockerfile `CMD`). More workers = multiple UDP
   senders with independent sequence counters = corrupt command stream.
4. Web cockpit video needs a reachable `video_gateway` + cameras at the address of the **active profile**
   (⚙ → «Налаштування мережі», stored in `cockpit.db`). A saved-but-unreachable gateway is recoverable via
   `GET /?video=local`. The `remote` profile defaults to `cam*_main` = **H265**, which only Safari decodes
   over WebRTC — elsewhere the connection succeeds and the picture stays black; switch the paths to
   `cam*_h264` in the same panel.
5. `.claude/settings.local.json` registers hooks `.claude/hooks/guard-bash.sh` / `guard-read.sh`, but
   `.claude/hooks/` does not exist.
6. The rangefinder is bound to the fixed device path `/dev/ttyUSB0` (`docker-compose.jetson.yml`). USB
   enumeration order is not guaranteed across reboots/replugs — if a second USB-serial device appears the
   TF03 may land on `ttyUSB1`. Consider a stable `udev` symlink later; for now confirm the path on the Jetson.

This list is a summary — the full, detailed gaps + safety caveats live in
[docs/architecture.md](docs/architecture.md#known-gaps).

## Conventions & gotchas

- **Python:** the TTY controller uses 3.10+ syntax but `from __future__ import annotations` lets it run
  on 3.9, and it is **POSIX-only** (`termios`/`tty`, `select`). The web cockpit needs **Python 3.11+**
  (stdlib `tomllib`); its target runtime is the `python:3.12-slim` image.
- **Two protocols coexist:** raw RWS UDP (turret wire, used by the TTY controller and the web cockpit)
  vs. the bridge's `control_state`/`observed_state` (client↔`rws_bridge`). Do not confuse them.
- **Root `compose.yaml` has only `video_gateway`** (Compose project `autoantibug`, containers prefixed
  `autoantibug-…`). The web cockpit has its own `services/web/docker-compose.yml` (project `rws_cockpit`).
  `rws_bridge` runs on the host directly.
- **Testing:** the web cockpit has a pytest suite in `services/web/tests/`
  (`cd services/web && pip install -r requirements-dev.txt && python3 -m pytest`). For the turret/TTY
  paths prefer `--dry-run` and `--packet-limit`.
- **Never commit or push unless the user asks.**
