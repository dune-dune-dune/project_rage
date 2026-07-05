from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeConfig:
    # RWS UDP
    bind_ip: str
    bind_port: int
    dst_ip: str
    dst_port: int
    send_period_ms: float
    stale_timeout_ms: int
    # Ownership
    lease_timeout_ms: int
    # Internal WebSocket server
    ws_host: str
    ws_port: int
    # RWS checksum salt (32-byte hex)
    salt: bytes


def load() -> BridgeConfig:
    return BridgeConfig(
        bind_ip=os.environ.get("RWS_BIND_IP", "192.168.88.33"),
        bind_port=int(os.environ.get("RWS_BIND_PORT", "7770")),
        dst_ip=os.environ.get("RWS_DST_IP", "192.168.88.56"),
        dst_port=int(os.environ.get("RWS_DST_PORT", "7780")),
        send_period_ms=float(os.environ.get("RWS_SEND_PERIOD_MS", "50.0")),
        stale_timeout_ms=int(os.environ.get("RWS_STALE_TIMEOUT_MS", "5000")),
        lease_timeout_ms=int(os.environ.get("LEASE_TIMEOUT_MS", "4000")),
        ws_host=os.environ.get("WS_HOST", "0.0.0.0"),
        ws_port=int(os.environ.get("WS_PORT", "8765")),
        salt=bytes.fromhex(
            os.environ.get(
                "RWS_SALT",
                "262bd7b673f1371fd274f96f2e819032498f304b4021d3fc87d5db723f8fa277",
            )
        ),
    )
