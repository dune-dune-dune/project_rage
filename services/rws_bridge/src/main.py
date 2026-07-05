"""
rws_bridge entry point.

Three concurrent asyncio tasks:
  1. WebSocket server — accepts source connections (web_human, ai_node)
  2. Control loop     — sends 40-byte RWS command every send_period_ms
  3. Watchdog         — checks ownership lease timeout every 200 ms
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import config as cfg_mod
from bridge import Bridge
from rws import RwsDriver
from server import start_ws_server

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def _control_loop(cfg: cfg_mod.BridgeConfig, bridge: Bridge, driver: RwsDriver) -> None:
    period = cfg.send_period_ms / 1000.0
    logger.info("Control loop started at %.0f Hz", 1.0 / period)
    while True:
        start = time.perf_counter()

        # Send RWS command
        payload = bridge.next_rws_command()
        driver.send(payload)

        # Ingest any new RWS replies into the observation snapshot
        now = time.monotonic()
        if driver.reply is not None:
            bridge.obs.update_status(driver.reply, now)
            driver.reply = None
        if driver.telemetry is not None:
            bridge.obs.update_telemetry(driver.telemetry, now)
            driver.telemetry = None

        # Push observed_state to all connected sessions
        await bridge.broadcast_observed_state()

        elapsed = time.perf_counter() - start
        await asyncio.sleep(max(0.0, period - elapsed))


async def _watchdog_loop(bridge: Bridge) -> None:
    import json as _json
    while True:
        await asyncio.sleep(0.2)
        revoked = bridge.check_lease()
        if revoked:
            for session in list(bridge._sessions.values()):
                snap = bridge.ownership.ownership_snapshot(session.session_id)
                try:
                    await session._send_json({
                        "type": "ownership_state",
                        "reason": "lease_timeout",
                        **snap,
                    })
                except Exception:
                    pass


async def main() -> None:
    cfg = cfg_mod.load()
    bridge = Bridge(cfg)

    driver = RwsDriver(
        bind_ip=cfg.bind_ip,
        bind_port=cfg.bind_port,
        dst_ip=cfg.dst_ip,
        dst_port=cfg.dst_port,
    )

    logger.info(
        "Starting rws_bridge — RWS %s:%d → %s:%d, WS %s:%d",
        cfg.bind_ip, cfg.bind_port,
        cfg.dst_ip, cfg.dst_port,
        cfg.ws_host, cfg.ws_port,
    )

    await driver.start()

    await asyncio.gather(
        start_ws_server(cfg, bridge),
        _control_loop(cfg, bridge, driver),
        _watchdog_loop(bridge),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
