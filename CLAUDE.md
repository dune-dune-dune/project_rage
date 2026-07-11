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
│   │   ├── app/{__init__,config,turret,routes,ws,store,wsgi}.py  # factory, settings, control, routes, /api/ws, JSON stores
│   │   ├── app/templates/{index,login}.html + app/static/{cockpit.js,ai.js,ai-worker.js,cockpit.css}  # video + HUD + YOLO (worker) + PIN login
│   │   ├── app/static/vendor/  # onnxruntime-web (vendored by scripts/fetch_ort.sh) — NOT committed
│   │   ├── scripts/{export_onnx.py,fetch_ort.sh}  # one-off: best.pt→best.onnx, fetch ORT web
│   │   ├── tests/ + conftest.py + pytest.ini  # pytest suite (speed, ramp, timing, turret, auth, routes, ws)
│   │   ├── requirements-dev.txt  # pytest (dev-only; runtime stays torch/pytest-free)
│   │   ├── data/model/best.pt (+ best.onnx, classes.json)  # YOLO weights (gitignored runtime data)
│   │   ├── settings.toml     # control tuning (rates, ramp_ms, axes, fire, speed_levels, [track] AI servo) — NOT secrets
│   │   ├── .env.example       # network/deploy env template incl. COCKPIT_PIN/SECRET_KEY (user creates .env)
│   │   ├── Dockerfile + docker-compose.yml   # cockpit (:8000, host net) + video_gateway
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
# macOS/Windows (Docker Desktop has no host networking → cockpit cannot bind the RWS
# source IP in a container). Run video in Docker, cockpit natively on the host:
cd services/web && docker compose up video_gateway    # video only
cd services/web && ./run-native.sh                    # cockpit natively (auto venv on py3.11+)
#   - run-native.sh / gunicorn.conf.py auto-load services/web/.env (python-dotenv).
#   - the sender retries the socket bind until RWS_SRC_IP is configured on the host.

# Other services
VIDEO_GATEWAY_HOST_IP=192.168.88.33 docker compose up video_gateway   # video (root compose)
python3 services/rws_bridge/src/main.py                                # bridge, WS :8765
```

**Cockpit keys:** `WASD` = momentary move (hold to move; **always available, not gated by safety**),
`1`/`2` = rotation-speed level (client-side velocity multiplier from `[control] speed_levels`,
default = fastest; auto-track is unaffected), `F` = safety toggle (**gates firing only**),
`Space` = hold to fire, `M` = cycle fire mode
(short/medium/manual), `Q`/`E` = digital zoom in/out, `TAB` = cycle camera, `I` = cycle AI mode
(**OFF → AI ON (YOLO) → AI CUSTOM → OFF**), `T` = toggle auto-track (any AI mode; aim-only, never fires).
AI CUSTOM is a model-free pixel **motion** detector (frame differencing): pixels whose colour changes by
more than the ⚙ threshold are clustered into blobs, and blobs exceeding the min-object-size are flagged as
a drone. The ⚙ button (top-right) opens crosshair position settings (H/V offset,
`services/web/data/crosshair.json` via `/api/crosshair`) and AI settings (confidence threshold default 70%,
min object size in px, and Custom motion threshold %, `services/web/data/ai_settings.json` via
`/api/ai-settings`). A **full-width instrument bar** (`#telemetry-bar`, a solid dark panel pinned to the
bottom edge, styled in `cockpit.css`) shows **turret telemetry** parsed from the reply stream, each metric
as an SVG icon + label + value: azimuth/elevation (`AZ/EL`), battery (%+V, whole item pulses red under
15%), motor temps (`MOT` X/Y), motor currents (`CUR` X/Y) and rangefinder distance (`DIST`); each dims
(`.stale`) until its reply arrives. The bar is an opaque overlay over the video's bottom edge (the video
stays full-size behind it, so crosshair/AI aim geometry is untouched); `#hud` sits just above it. The
`#hud` overlay (bottom-left) keeps the state badges (`SAFE`/`FIRE`/`SPD`/`ZOOM`/`TURRET`/`CAM`/`LINK`/
`AI`/`TRACK`), the WASD/Space keys and the key-legend hint.
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
(deps in `requirements-export.txt`, dev-only — the cockpit runtime stays torch-free).

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
  Rotation speed is switchable at runtime with keys `1`/`2` (`speed_level` on `/api/input`, levels from
  `[control] speed_levels`). The one-time **jerk at movement start** was traced to the position channel:
  the cockpit used to toggle the `ROT_P`/`ELE_P` valid bits off→on and jump the target 0→±π on the first
  move packet. It now mirrors the reference — **P valid bits stay on continuously**, holding the turret's
  *current* angle (read from status replies) when idle and leading it by a modest amount
  (`_POSITION_LEAD_RAD`, 90°) when moving (`turret.py:_axis_position`); until the turret reports an angle
  it falls back to the old ±π scheme. A separate **velocity soft-start** ramp (`[control] ramp_ms`,
  default 250 ms) smooths the 0→full velocity step (auto-track bypasses it) — a nicety, not the jerk fix.
  **Control input transport:** the browser sends intent via `POST /api/input` (reliable default). A
  WebSocket path **`/api/ws`** (flask-sock) exists server-side but is **OFF by default** on the client
  (`USE_WS=false` in `cockpit.js`) pending real-hardware validation — a half-open WS can report
  `readyState===OPEN` while dropping frames, black-holing the heartbeat and tripping the deadman. HTTP
  routes: `/`, `/healthz`, `/login` (GET/POST), `/logout`, `/api/input`, `/api/status`,
  `/api/crosshair` (GET/POST), `/api/track` (POST auto-aim velocity),
  `/api/ai-settings` (GET/POST conf + min size), `/assets/model.onnx`, `/assets/classes.json`;
  WebSocket route: `/api/ws` (control input). The PIN gate is registered app-wide
  (`before_app_request`) so it also protects `/api/ws`.
- **AI auto-track** runs client-side (`ai.js`, ONNX Runtime Web); the server only receives the resulting
  aim velocity via `/api/track` and applies it as a velocity override (aim-only — never touches `arm`/`fire`).
  A dedicated aim timeout (`[track].aim_timeout_ms`, default 500 ms) zeroes the aim if the browser stalls.

## Known gaps (do not assume these work)

Details in [docs/architecture.md#known-gaps](docs/architecture.md#known-gaps).

1. The web cockpit **bypasses `rws_bridge`** — it drives RWS UDP directly and does not use the bridge's
   ownership/lease/replay protection or telemetry. Run only one control path at a time against a turret.
2. Live control needs `network_mode: host` (Linux only) to bind `RWS_SRC_IP:RWS_SRC_PORT`. On Docker
   Desktop (macOS/Windows) only `RWS_DRY_RUN=true` works (the socket is never opened).
3. `GUNICORN_WORKERS` **must stay 1** (hardcoded in the Dockerfile `CMD`). More workers = multiple UDP
   senders with independent sequence counters = corrupt command stream.
4. Web cockpit video is off unless `WHEP_URL` is set in `.env` and `video_gateway` + cameras are reachable.
5. `.claude/settings.local.json` registers hooks `.claude/hooks/guard-bash.sh` / `guard-read.sh`, but
   `.claude/hooks/` does not exist.

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
