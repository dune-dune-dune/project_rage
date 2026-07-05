"""
Web backend:
  - pywebtransport 0.18.1 server on :4433  — WebTransport hot-path
  - aiohttp HTTP server on :8080            — serves /config.json, future REST
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

from aiohttp import web
from pywebtransport import ServerApp, ServerConfig, WebTransportSession
from pywebtransport.events import Event, EventType
from pywebtransport.utils import generate_self_signed_cert


logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG", "").lower() in ("1", "true") else logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Certs (dev: auto-generate; prod: mount via Docker secret/env) ──────────────

CERT_FILE = os.getenv("CERT_FILE", "localhost.crt")
KEY_FILE  = os.getenv("KEY_FILE",  "localhost.key")

_CERT_HASH_B64: str = ""

try:
    # max 14 days — required for browser serverCertificateHashes API
    generate_self_signed_cert(hostname="localhost", validity_days=14)
    _der = subprocess.run(
        ["openssl", "x509", "-in", CERT_FILE, "-outform", "DER"],
        capture_output=True, check=True,
    ).stdout
    _CERT_HASH_B64 = base64.b64encode(hashlib.sha256(_der).digest()).decode()
    logger.info("Certificate generated (14d), SHA-256=%s…", _CERT_HASH_B64[:12])
except Exception as e:
    logger.warning("Could not generate certs: %s", e)


# ── Environment config ─────────────────────────────────────────────────────────

WT_HOST    = os.getenv("WT_HOST",    "0.0.0.0")
WT_PORT    = int(os.getenv("WT_PORT",    "4433"))
HTTP_HOST  = os.getenv("HTTP_HOST",  "0.0.0.0")
HTTP_PORT  = int(os.getenv("HTTP_PORT",  "8080"))
# URL that the browser uses to reach the WebTransport endpoint.
# Must match the cert SAN (DNS:localhost) — do NOT use 127.0.0.1 here.
WT_URL_PUB = os.getenv("WT_URL_PUB", "https://localhost:4433/")
# Optional: MediaMTX WHEP endpoint for video
WHEP_URL   = os.getenv("WHEP_URL", "")


# ── Shared state (prototype; will be replaced by rws_bridge relay) ─────────────

@dataclass
class JoystickState:
    t: int
    x: float
    y: float
    buttons: int


class SharedState:
    def __init__(self) -> None:
        self.latest: Optional[JoystickState] = None
        self._lock = asyncio.Lock()

    async def update(self, s: JoystickState) -> None:
        async with self._lock:
            self.latest = s

    async def get(self) -> Optional[JoystickState]:
        async with self._lock:
            return self.latest


state = SharedState()


# ── Datagram decoder (prototype 3-byte format) ─────────────────────────────────

def decode_datagram(data: bytes) -> Optional[JoystickState]:
    if len(data) < 3:
        logger.debug("Short datagram ignored len=%d", len(data))
        return None
    try:
        return JoystickState(
            t=int(time.time() * 1000),
            x=(data[1] - 128) / 128.0,
            y=(data[2] - 128) / 128.0,
            buttons=data[0],
        )
    except Exception as e:
        logger.error("decode_datagram failed: %s", e)
        return None


# ── pywebtransport app ─────────────────────────────────────────────────────────

logger.info("app_server create")
wt_app = ServerApp(config=ServerConfig(certfile=CERT_FILE, keyfile=KEY_FILE))
logger.info("app create")


@wt_app.route(path="/")
async def handle_session(session: WebTransportSession) -> None:
    await session.accept()
    logger.info("New WebTransport session session_id=%s", session.session_id)

    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_datagram(event: Event) -> None:
        raw = event.data.get("data") if isinstance(event.data, dict) else None
        if raw:
            queue.put_nowait(raw)

    session.events.on(event_type=EventType.DATAGRAM_RECEIVED, handler=on_datagram)

    try:
        while not session.is_closed:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            js = decode_datagram(data)
            if js:
                await state.update(js)
                logger.debug("[WT] x=%.2f y=%.2f btn=%d", js.x, js.y, js.buttons)
    except asyncio.CancelledError:
        pass
    finally:
        session.events.off(event_type=EventType.DATAGRAM_RECEIVED, handler=on_datagram)
        logger.info("Session ended session_id=%s", session.session_id)


# ── aiohttp HTTP server ────────────────────────────────────────────────────────

http_app = web.Application()


async def handle_config(request: web.Request) -> web.Response:
    config: dict = {"wtUrl": WT_URL_PUB}
    if _CERT_HASH_B64:
        config["certHash"] = _CERT_HASH_B64
    if WHEP_URL:
        config["whepUrl"] = WHEP_URL
    if os.getenv("DEBUG", "").lower() in ("1", "true"):
        config["debug"] = True
    return web.Response(
        content_type="application/json",
        text=json.dumps(config),
        headers={"Cache-Control": "no-store"},
    )


http_app.router.add_get("/config.json", handle_config)


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    runner = web.AppRunner(http_app)
    await runner.setup()
    http_site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await http_site.start()
    logger.info("HTTP server listening on %s:%d", HTTP_HOST, HTTP_PORT)

    async with wt_app:
        logger.info("WebTransport server starting on %s:%d", WT_HOST, WT_PORT)
        await wt_app.serve(host=WT_HOST, port=WT_PORT)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
