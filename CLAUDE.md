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
│   ├── exporter/             # Sidecar: uploaded YOLO .pt → ONNX (the ONLY place torch lives)
│   │   └── Dockerfile + requirements.txt + src/main.py + README.md   # POST /convert, :8901 (loopback)
│   ├── web/                  # Flask + Gunicorn cockpit (browser → RWS UDP → turret)
│   │   ├── app/{__init__,config,turret,routes,ws,db,store,model_jobs,wsgi}.py  # factory, settings, control, routes, /api/ws, SQLite, stores, model conversion jobs
│   │   ├── app/migrations/*.sql  # schema + seed + models table, applied once at startup (see its README)
│   │   ├── app/templates/index.html + app/static/{cockpit.js,ai.js,ai-worker.js,models.js,heartbeat-worker.js,map.js,compass.js,ws-client.js,targets.js,cockpit.css}  # landing grid + video + HUD + YOLO (worker) + AI model library + bg-tab heartbeat worker + map/gauges + compass + targets WS client + target markers
│   │   ├── app/static/vendor/  # onnxruntime-web (fetch_ort.sh) + leaflet/ (fetch_leaflet.sh)
│   │   ├── scripts/{export_onnx.py,fetch_ort.sh,fetch_leaflet.sh}  # offline: best.pt→best.onnx, fetch ORT web, vendor Leaflet
│   │   ├── tests/ + conftest.py + pytest.ini  # pytest suite (speed, ramp, timing, turret, auth, routes, ws, db, network, models)
│   │   ├── requirements-dev.txt  # pytest (dev-only; the cockpit runtime stays torch/pytest-free)
│   │   ├── data/cockpit.db   # SQLite: crosshair, AI, map, video/network profiles, model registry (gitignored)
│   │   ├── data/models/<id>/{source.pt|source.onnx,model.onnx,classes.json}  # the AI model library (gitignored)
│   │   ├── data/model/best.pt (+ best.onnx, classes.json)  # pre-library weights; imported once as the builtin model
│   │   ├── settings.toml     # control tuning (rates, ramp_ms, axes, fire, speed_levels, [track] AI servo) — NOT secrets
│   │   ├── .env.example       # turret-network/deploy env template (user creates .env; no auth — cockpit is open)
│   │   ├── wg-targets.conf.example  # template for data/wg-targets.conf (WireGuard tunnel to the targets VM; user copies + fills keys)
│   │   ├── Dockerfile + docker-compose.yml   # cockpit (:8000, host net) + exporter (:8901) + video_gateway
│   │   ├── docker-compose.jetson.yml # prod override: /dev/ttyUSB0 rangefinder passthrough + RANGEFINDER_ENABLED + wg-targets WireGuard sidecar (targets VM tunnel)
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
cd services/web && docker compose up --build          # cockpit :8000 + exporter :8901 + video_gateway :8889
#   NOTE: the exporter image installs ultralytics + CPU-only torch (~1-1.5 GB) — the first build is SLOW.
#   Skip it with `docker compose up cockpit video_gateway`; only .pt->ONNX conversion is lost
#   (uploading a ready .onnx still works).
# Jetson / production (everything in Docker + TF03 rangefinder passthrough):
cd services/web && COMPOSE_FILE=docker-compose.yml:docker-compose.jetson.yml \
  docker compose up -d --build                        # adds /dev/ttyUSB0 + RANGEFINDER_ENABLED
#   (this is exactly what .github/workflows/deploy.yml runs over WireGuard+SSH.)
# macOS/Windows (Docker Desktop has no host networking → cockpit cannot bind the RWS
# source IP in a container). Run video in Docker, cockpit natively on the host:
cd services/web && docker compose up video_gateway exporter   # video + model converter
cd services/web && ./run-native.sh                    # cockpit natively (auto venv on py3.11+)
#   - run-native.sh / gunicorn.conf.py auto-load services/web/.env (python-dotenv).
#   - the sender retries the socket bind until RWS_SRC_IP is configured on the host.

# Other services
docker compose up video_gateway                                       # video (root compose)
#   MediaMTX advertises both ICE hosts (LAN + VPN) by default; override with MEDIAMTX_HOSTS=a,b
python3 services/rws_bridge/src/main.py                                # bridge, WS :8765
```

**Cockpit keys:** `WASD` = momentary move (hold to move; **always available, not gated by safety**),
`1`/`2`/`3` = rotation-speed level (velocity multiplier from `[control] speed_levels` = `[100, 50, 1]`:
`1` = **100 % (boot default)**, `2` = 50 %, `3` = **1 % fine aim**; the default is an *argmax* over the
list — `Settings.default_speed_index` — not its last entry, so the list can be ordered by key rather than
by speed; auto-track is unaffected). **`1`/`2`/`3` are the only number keys the cockpit binds** — the
former key-4 hardware SLOW toggle (`FLAGS1_SLOW`) and key-5 camera-drive mode (`cameras_p`) were removed;
the command stream never sets either now (`cameras_p` is always 0), and the `FLAGS1_SLOW`/`cameras_p`
definitions survive only in `rws_control.py`, which is the wire protocol shared with the TTY controller.
`F` = safety toggle (**gates firing only**),
`Space` = hold to fire, `Shift` = hold to **range-find** (edge-paced `rangefinder_seq`, spacing
`[control] rangefinder_measure_interval_ms`; aim-only), `M` = cycle fire mode
(short/medium/manual), `Q`/`E` = digital zoom in/out (**on the wide camera the zoom is centred on the
crosshair, not on the screen centre** — see below), `TAB` = cycle camera, `I` = cycle AI mode
(**OFF → AI ON (YOLO) → AI CUSTOM → OFF**), `T` = toggle auto-track (any AI mode; aim-only, never fires).
AI CUSTOM is a model-free pixel **motion** detector (frame differencing): pixels whose colour changes by
more than the ⚙ threshold are clustered into blobs, and blobs exceeding the min-object-size are flagged as
a drone. The ⚙ button (top-left) opens a dropdown of settings panels: **мапа** (`/api/map-settings`),
**приціл** (H/V offset у % від центру, крок **0.01 %** — повзунок + числове поле для точного вводу;
`/api/crosshair`, значення клампиться до ±50 і округлюється до 2 знаків у `store.py`), **ШІ модель**
(бібліотека моделей — див. нижче — плюс confidence threshold default 70 %, min object size in px,
Custom motion threshold %, `/api/ai-settings`), **мережа** (see below) and **алерти** (placeholder).
All of them persist to SQLite (`services/web/data/cockpit.db`), not to JSON files.

**AI model library (⚙ → «Налаштування ШІ моделі»).** The operator uploads new YOLO weights and switches
between models **at runtime** — no SFTP, no container restart. A `.pt` is registered instantly (202) and
converted to ONNX **asynchronously** by the `exporter` sidecar (`services/exporter/`, the only component
with ultralytics/torch: a torch export inside the cockpit could stall the Gunicorn worker that owns the
20 Hz turret loop and get it killed). The panel polls `/api/models` every 2 s until the row settles
(`pending → converting → ready|error`). A ready-made **`.onnx` (+ optional `classes.json`) is accepted
as-is with no exporter at all** — the recovery hatch when the sidecar is down; its input size then falls
back to `[track].imgsz`, while the `.pt` path learns the real one from the checkpoint. Files live in
`data/models/<id>/`; the registry is the `models` table (`0003_models.sql`) and only the *active* model id
is a settings key. `data/model/best.onnx` is imported once as the **builtin** model, which can never be
deleted (nor can the active one) — there is always a fallback. Switching is a **hot swap**
(`AI.setModel()` re-inits the ONNX worker; **no page reload**, unlike the network panel), and the panel
shows readiness explicitly: per-model status, the browser ONNX engine (`не запущено` / `завантаження` /
`працює (WebGPU|WASM) — N к/с, M мс` / `помилка: …`) and whether the exporter answers. This is all
**AI ON (YOLO)** only — **AI CUSTOM is model-free and untouched**.

**Inference backend (WebGPU → WASM).** `ai-worker.js` is a **module worker** (`new Worker(url, {type:
"module"})`) that imports `vendor/ort.webgpu.bundle.min.mjs` — ORT's WebGPU build has been an ES module
since 1.18, so a classic `importScripts` worker cannot load it — and tries **WebGPU** first: YOLO then runs
on the **operator's** GPU (~20–40 ms a
frame) instead of one CPU core (~500 ms ≈ 2 FPS for an `s`-class model, which visibly lags auto-track). It
falls back to single-threaded SIMD WASM otherwise, and the panel reports which backend came up **and why**
(`#ai-state-backend`). ⚠️ **WebGPU requires a secure context**: `navigator.gpu` does not exist on a plain
`http://` LAN origin — the normal way the cockpit is served — so the field default silently ends up on
WASM. `localhost` counts as secure; otherwise allow the origin in the browser
(`chrome://flags/#unsafely-treat-insecure-origin-as-secure`) or serve over HTTPS (which then needs TLS on
MediaMTX too, or the WHEP requests become mixed content). The GPU is the **client's** — the Jetson's GPU is
never involved in detection. ⚠️ **Do not pin ORT back below 1.22** (`scripts/fetch_ort.sh`): 1.17 calls
`adapter.requestAdapterInfo()`, since removed from the WebGPU spec, so its GPU backend throws
(`no available backend found … requestAdapterInfo is not a function`) on any current browser and inference
silently drops to the ~20× slower CPU path. `scripts/export_onnx.py` still does the same conversion
offline.
A **full-width instrument bar** (`#telemetry-bar`, a solid dark panel pinned to the
bottom edge, styled in `cockpit.css`) shows **five** groups, each an SVG icon + label + value:
battery (%+V, `#battery`, whole item pulses red under 15%), motor temps (`#motemp` X/Y),
motor currents (`#mocur` X/Y), speed level (`#speed-bar`), and a
**«Статус підключення»** group with two coloured dots — turret
(`#dot-turret`, green/red from `s.link`/`dry_run`/`bind_error`) and video (`#dot-video`, green/red from
the active camera's `RTCPeerConnection.connectionState`). Each dot's hover tooltip (`title`) shows
«Статус турелі/відео: онлайн/офлайн» (`setDot`/`paintVideo` in `cockpit.js`, `.ok`/`.bad` classes);
telemetry values dim (`.stale`) until their reply arrives. The bar is an opaque overlay over the video's
bottom edge (the video stays full-size behind it, so crosshair/AI aim geometry is untouched); `#hud` sits
just above it. Fields deliberately **not** in the bar (though `/api/status` still serves them all):
azimuth/elevation — cached in `lastAzDeg`/`lastElDeg` for the map widgets; fire-circuit voltage
(`voltage_fire`), CPU voltage (`voltage_cpu`), per-motor voltage (`voltage_x/y`), motor RPM (`rpm_x/y`),
the turret's own rangefinder distance (`distance_turret_m`) and the camera-axis angle
(`camera_angle_deg`) — all removed from the UI; and `distance_m`, which is shown at the crosshair
(`#cp-dist`), not in the bar. A small **crosshair status panel** (`#cross-panel`, a child of `#crosshair` so it
tracks the reticle offset) sits at the crosshair's lower-right and shows the rangefinder distance
(`#cp-dist`, from `/api/status.distance_m`; on the Jetson this is the serial **Benewake TF03-180**
LiDAR — see below — otherwise the turret's own status-reply distance) plus the camera lens type
(`#cp-camtype`: CAM 95 → Ширококутна, CAM 96 → Вузькокутна via
`cameraKind`) and digital zoom (`#cp-zoom`). Below the camera line a `#cp-state` row shows boxed
indicators in order **safety · AI · track · fire-mode**: a **safety padlock** (`#cp-safety`: closed+green
outline when safe, open+red outline when armed — synced to `s.safety_off` in `pollStatus`, which also
recolours the reticle green/red via `crosshairEl.style.color`); an **AI square** (`#cp-ai`: grey box +
hand icon = manual/OFF, green «AI»/«AI+» = YOLO/custom); a **track square** (`#cp-track`: grey «T» off,
green on); and a **fire-mode box** (`#cp-fire` wrapping `#cp-firemode`, `data-mode` set in `paintKeys`:
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
`bearing = angle_rot_deg + north_correction`. `map.js` exposes the Leaflet instance via a
`window.mapWidgets.map` getter so `targets.js` can drop markers on it.

**Live target markers (`ws-client.js` + `targets.js`):** a standalone WebSocket client
(`window.TargetsWS`) opens `ws://<TARGETS_WS_HOST>:<TARGETS_WS_PORT>` (defaults `10.31.0.100:8766`,
injected as `window.__TARGETS_WS_HOST__/__PORT__`; empty host → the page host) with auto-reconnect
(exp. backoff), a staleness heartbeat, and reconnect on tab-visible/`online`. The targets server is a
**separate VM** reached over its **own** WireGuard tunnel (`wg-targets`, distinct from the turret VPN on
the MikroTik) — the `wg-targets` compose sidecar (`docker-compose.jetson.yml`, `network_mode: host` +
`NET_ADMIN`) brings it up from `data/wg-targets.conf` (git-ignored; copy `wg-targets.conf.example`). It
subscribes with `{type:'subscribe', mode:'targets_only'}`; each `{type:'status', targets:{…}}` frame is
drawn by `targets.js:updateTargetMarkers()` as pulsing `L.divIcon` markers on the shared Leaflet map
(FPV vs «Молнія» SVG chosen by `target_type_id`; styles in `cockpit.css`, `.target-marker*`). This is
**display-only** — it never touches control/fire and is independent of the RWS command stream.

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
VPN, `10.20.100.1`, paths `cam95_main`/`cam96_main`). The host is free text per profile; each camera's
stream is a **dropdown = the video-quality picker**, rendered from the server-side catalogue
`NetworkStore.stream_options()`: **SD 640 · H264** (`cam*_h264`), **HD 1080 · H264** (`cam*_h264_hd`,
software x264 transcode on the gateway — if the CPU cannot keep up, latency grows) and **HD 1080 · H265**
(`cam*_main`, Safari only). The catalogue only *offers* paths — `save()` still accepts any valid path, and a
stored path outside the catalogue shows up as an extra `… (власний)` option (`NetworkStore` in `store.py`,
`GET/POST /api/network-settings`); labels and the WHEP
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

**No login gate (open access).** The former 7-digit `COCKPIT_PIN` login was removed
(`login.html`, `/login`, `/logout` and the `before_app_request` auth hook are all gone; `COCKPIT_PIN`
/`SECRET_KEY` are no longer read). ⚠️ The cockpit is served **openly** — put it behind a trusted
LAN / VPN. An ephemeral Flask `secret_key` is still set (`__init__.py`) but nothing uses the session.

**Landing view (turret grid ↔ control).** The entry screen is a **landing view**, not the control page:
the top half is a large turret **map** (the same `#map-widgets` Leaflet cluster + azimuth sector as the
control view's mini-map), the bottom half is an **8-cell grid** of turrets. In this single-turret
deployment only **cell 0** is live — it shows the active camera as a thumbnail (`#stage`) and is
clickable; the other 7 are empty «Немає турелі» placeholders. Clicking cell 0 enters the **control
view**; a **grid button** (`#grid-btn`, top-left, next to ⚙) returns to the landing. These are **two
states of one page** — `body.mode-grid` / `body.mode-control`, toggled by `setView()` in `cockpit.js`
— **not** separate pages: both share the single Leaflet map and the pre-connected camera videos, so the
switch is instant (no reload, no second map/`RTCPeerConnection`). `#stage` (the shared video stack)
lives inside cell 0 and is CSS-repositioned — `position:absolute` filling the cell in the grid,
`position:fixed inset:0` fullscreen in control. On each toggle `setView()` re-runs `applyView()`
(control) and `window.mapWidgets.relayout()` (Leaflet `invalidateSize`, since the map container
resized). Control keys (WASD/Space/etc.) are **ignored in grid mode** and motion/fire is neutralised on
entry, but the heartbeat keeps flowing so the turret **holds its aim** while the operator is on the map.

**Crosshair-centred zoom (wide camera only):** the crosshair offset is a **boresight calibration** — the
frame point the jet actually hits. On the **wide camera (CAM 95)** the reticle is pinned to the geometric
centre of the screen and the *picture is panned* by the offset instead, so `Q`/`E` magnify around the
crosshair and the aim point no longer depends on the zoom. `cockpit.js:viewParams(i)` is the sole owner of
the geometry (`applyView()` writes both the `<video>` `transform: translate(t) scale(z)` and the reticle's
`left`/`top` in **px**, so it must re-run on resize) and publishes it as `window.cockpit.view`
(`{scale, tx, ty, crossX, crossY}`) — `ai.js` reads that instead of re-deriving the mapping. Because
`object-fit: cover` clips to the element box, the pan's only headroom is the box's own scale-up, giving a
**dynamic base overscan** `base = 1/(1 − 2·|offset|/100)` (offset 10 % → показуємо 80 % кадру, 20 % у
резерві; **offset 0 → base 1, кропу немає взагалі**), capped by `BASE_MAX = 3.0`. **CAM 96 (вузькокутна)
навмисно не змінена** — без панорамування, приціл малюється зміщеним, і її точка наведення, як і раніше,
повзе до центру кадру при зумі. Повний вивід формул:
[docs/architecture.md → Crosshair-centred zoom](docs/architecture.md#crosshair-centred-zoom-wide-camera).

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
programmatic offset**, computed by inverting the `object-fit: cover` + zoom mapping. The weights come from
the **model library** (⚙ panel, see above) — upload a `.pt` and the `exporter` container converts it; the
**cockpit runtime itself stays torch-free** (`requirements.txt` = flask/gunicorn/pyserial), which is why
the conversion is a sidecar and not an in-process call. Vendor ORT once: `bash scripts/fetch_ort.sh`; and
Leaflet for the map widget: `bash scripts/fetch_leaflet.sh` (map *tiles* still need internet in the
browser).

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
  worker (sole UDP/sequence owner). Drives the turret directly, not via `rws_bridge`. **No auth gate** —
  the cockpit is served openly (the former PIN login was removed); front it with a trusted network/VPN.
  The entry screen is the **landing view** (turret map + 8-cell grid), a client-side mode of the same
  page as the control view (see «Landing view» above). Rotation speed is switchable at runtime with keys `1`/`2`/`3` (`speed_level` on `/api/input`, levels from
  `[control] speed_levels` = `[100, 50, 1]`; the boot default is the highest percent,
  `Settings.default_speed_index`). The one-time **jerk at movement start** was traced to the position channel:
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
  routes: `/`, `/healthz`, `/api/input`, `/api/status`,
  `/api/crosshair` (GET/POST), `/api/track` (POST auto-aim velocity),
  `/api/ai-settings` (GET/POST conf + min size), `/api/map-settings` (GET/POST map origin lat/lon +
  north_correction), `/api/network-settings` (GET/POST video profiles + active mode),
  `/api/models` (GET list + POST multipart upload of `.pt`/`.onnx`),
  `/api/models/<id>/activate` (POST), `/api/models/<id>/rename` (POST), `/api/models/<id>` (DELETE),
  `/assets/models/<id>/model.onnx` + `/assets/models/<id>/classes.json`,
  `/assets/model.onnx`, `/assets/classes.json` (redirect to the active model);
  WebSocket route: `/api/ws` (control input). All routes are open (no auth gate).
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
7. Converting an uploaded `.pt` needs the **`exporter` container built and running** (~1–1.5 GB image —
   CPU-only torch from `download.pytorch.org/whl/cpu`, not the CUDA wheels; the Jetson builds it itself,
   so the first deploy after this feature is slow — the deploy's SSH step allows 60 min). Without it the panel shows
   «Конвертер: недоступний» and a `.pt` upload ends in `помилка` — a ready-made `.onnx` upload still works.
   A conversion in flight does **not** survive a deploy (`docker compose down` kills it); the row is reset
   to `error` at startup rather than left stuck at `converting`.
8. **AI inference falls back to WASM over plain HTTP.** WebGPU needs a secure context, and the cockpit is
   normally served as `http://<lan-ip>:8000`, so `navigator.gpu` is absent and detection runs on one CPU
   core (~2 FPS for an `s`-class model — auto-track visibly lags). The ⚙ panel states this outright. Fix by
   opening it via `localhost`, allowing the origin
   (`chrome://flags/#unsafely-treat-insecure-origin-as-secure`), or serving over HTTPS — note the last one
   also needs TLS on MediaMTX, or WHEP video becomes blocked mixed content.

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
