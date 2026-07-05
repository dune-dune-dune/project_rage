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
20 Hz. A set of services (`rws_bridge`, `web`, `video_gateway`) is being built toward a browser-based
cockpit with live video, but the browser→turret chain is **not yet connected** (see Known gaps).

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
│   ├── web/                  # Browser cockpit (prototype)
│   │   ├── backend/main.py   # WebTransport (:4433) + aiohttp /config.json (:8080)
│   │   └── frontend/         # TypeScript + Vite (:5173): keyboard/gamepad/on-screen input, WHEP video
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

# Services
VIDEO_GATEWAY_HOST_IP=192.168.88.33 docker compose up video_gateway   # video
python3 services/rws_bridge/src/main.py                                # bridge, WS :8765
python3 services/web/backend/main.py                                   # web backend :4433/:8080
cd services/web/frontend && npm install && npm run dev                 # frontend :5173
```

Control keys are documented in [README.md](README.md). Summary: `WASD` latch axes, arrows momentary
move, `1`/`2`/`4`/`5` = enable/slow/reload/forceHome, `Backspace` = safetyARM, `7`/`8`/`9` = fire mode,
`Space` = fire, `[`/`]` = speed, `V` = stop, `Q` = quit. Ukrainian keyboard layouts are also mapped.

## Architecture (short)

See [docs/architecture.md](docs/architecture.md) for full flows, port/env tables, and the safety
model. In brief:

- **Working:** `test_rws_control.py` → RWS UDP → turret. Video: cameras `.95`/`.96` (RTSP) → MediaMTX
  `video_gateway` → browser WHEP (`:8889`).
- **rws_bridge** is a self-contained driver: starts in **safe mode** (neutral packets), single-owner
  ownership with a 4 s lease, edge-triggered fire, 12-byte `control_state` input protocol over
  WebSocket (`:8765`).
- **web** is a prototype cockpit; the browser→bridge relay is not implemented (Known gaps).

## Known gaps (do not assume these work)

Details in [docs/architecture.md#known-gaps](docs/architecture.md#known-gaps).

1. Web backend decodes browser datagrams into memory but does **not** relay to `rws_bridge`.
   `services/web/backend/config.py` and `.../transport/webtransport.py` are empty 0-byte placeholders.
2. Protocol mismatch with a **dangerous bit collision**: frontend/backend send a **3-byte** joystick
   datagram; `rws_bridge` expects the **12-byte** `control_state`. A naive `buttons → state_flags` copy
   maps browser FIRE(0x01)→ENABLE and HOME(0x10)→FIRE. A relay must translate fields, never copy bytes.
3. Web client performs no ownership/enable handshake (`take_control`/`presence`/enable bit) — a second
   reason the web path can't drive the turret even with a relay.
4. Web cockpit video is off by default (`VITE_WHEP_URL` is dead code; needs backend env `WHEP_URL`).
5. `.claude/settings.local.json` registers hooks `.claude/hooks/guard-bash.sh` / `guard-read.sh`, but
   `.claude/hooks/` does not exist.
6. `services/web/Dockerfile` installs only `pywebtransport` (not `aiohttp`), and the backend needs the
   `openssl` CLI at runtime (absent in `python:3.12-slim`) — cert hashing silently fails.
7. `.env.development` uses `127.0.0.1` for WebTransport while the backend cert is issued for `localhost`;
   the backend also mints a new cert each startup, breaking cached browser sessions on restart.

This list is a summary — the full, detailed gaps + safety caveats live in
[docs/architecture.md](docs/architecture.md#known-gaps).

## Conventions & gotchas

- **Python:** code uses 3.10+ syntax but `from __future__ import annotations` lets it run on 3.9. The
  keyboard controller is **POSIX-only** (`termios`/`tty`, `select`).
- **Two protocols coexist:** raw RWS UDP (turret wire) vs. the relay `control_state`/`observed_state`
  (frontend↔backend↔bridge). Do not confuse them.
- **Only `video_gateway` is in `compose.yaml`** (Compose project name is `autoantibug`, so containers
  are prefixed `autoantibug-…`). `rws_bridge` and `web` run on the host directly.
- **Testing:** prefer `--dry-run` and `--packet-limit`. There is no automated test suite despite the
  `pytest` permission entries in `.claude/settings.local.json`.
- **Never commit or push unless the user asks.**
