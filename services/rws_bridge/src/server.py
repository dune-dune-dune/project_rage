"""
WebSocket server for internal source ↔ bridge connections.

Each source (web_human or ai_node) opens one WebSocket connection.
Protocol:
  - First text frame must be control_channel_open (JSON)
  - Subsequent binary frames: control_state or presence (parsed by protocol.py)
  - Subsequent text frames: take_control, release_control, request_control_snapshot,
    control_channel_close (JSON)
  - Bridge sends: control_channel_ready, control_request_result, ownership_state,
    session_revoked, channel_warning as text (JSON), and observed_state as binary
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

import websockets
import websockets.exceptions

from bridge import Bridge, ControllerIdentity
from config import BridgeConfig
from protocol import parse_datagram, ControlState, Presence

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ControllerSession:
    def __init__(self, ws: websockets.WebSocketServerProtocol, bridge: Bridge) -> None:
        self.session_id = str(uuid.uuid4())
        self._ws = ws
        self._bridge = bridge
        self._registered = False
        self._identity: ControllerIdentity | None = None

    @property
    def is_open(self) -> bool:
        return not self._ws.closed

    async def send_bytes(self, data: bytes) -> None:
        if self.is_open:
            await self._ws.send(data)

    async def _send_json(self, msg: dict) -> None:
        if self.is_open:
            await self._ws.send(json.dumps(msg))

    # ── Message handlers ────────────────────────────────────────────────────

    async def _handle_open(self, msg: dict) -> None:
        identity = ControllerIdentity(
            session_id=self.session_id,
            controller_kind=msg.get("controller_kind", "unknown"),
            instance_id=msg.get("instance_id", self.session_id),
            principal_id=str(msg.get("principal_id", "")),
            principal_name=str(msg.get("principal_name", "unknown")),
        )
        proto_ver = msg.get("protocol_version", 0)
        bin_ver   = msg.get("binary_version", 0)
        if proto_ver != 1 or bin_ver != 1:
            await self._send_json({
                "type": "channel_warning",
                "code": "version_mismatch",
                "message": f"expected protocol_version=1 binary_version=1, got {proto_ver}/{bin_ver}",
            })

        self._bridge.ownership.register(identity)
        self._bridge.add_session(self)
        self._identity = identity
        self._registered = True

        snap = self._bridge.ownership.ownership_snapshot(self.session_id)
        await self._send_json({
            "type": "control_channel_ready",
            "session_id": self.session_id,
            **snap,
            "safe_mode": self._bridge.safe_mode,
            "can_take_control": snap["owner_kind"] is None,
        })
        logger.info("Session %s registered as %s / %s",
                    self.session_id, identity.controller_kind, identity.principal_name)

    async def _handle_take_control(self, msg: dict) -> None:
        request_id = msg.get("request_id", "")
        ok, reason = self._bridge.take_control(self.session_id)
        await self._send_json({
            "type": "control_request_result",
            "request_id": request_id,
            "action": "take_control",
            "ok": ok,
            "reason": reason,
        })
        if ok:
            await self._broadcast_ownership("taken")

    async def _handle_release_control(self, msg: dict) -> None:
        request_id = msg.get("request_id", "")
        released = self._bridge.release_control(self.session_id)
        await self._send_json({
            "type": "control_request_result",
            "request_id": request_id,
            "action": "release_control",
            "ok": released,
            "reason": "released" if released else "not_owner",
        })
        if released:
            await self._broadcast_ownership("released")

    async def _handle_snapshot_request(self, msg: dict) -> None:
        request_id = msg.get("request_id", "")
        snap = self._bridge.ownership.ownership_snapshot(self.session_id)
        await self._send_json({
            "type": "control_request_result",
            "request_id": request_id,
            "action": "request_control_snapshot",
            "ok": True,
            "reason": "ok",
            **snap,
            "safe_mode": self._bridge.safe_mode,
        })

    async def _broadcast_ownership(self, reason: str) -> None:
        for session in list(self._bridge._sessions.values()):
            snap = self._bridge.ownership.ownership_snapshot(session.session_id)
            try:
                await session._send_json({
                    "type": "ownership_state",
                    "reason": reason,
                    **snap,
                })
            except Exception:
                pass

    async def _on_binary(self, data: bytes) -> None:
        if not self._registered:
            return
        msg = parse_datagram(data)
        if msg is None:
            await self._send_json({
                "type": "channel_warning",
                "code": "bad_datagram",
                "message": f"unrecognized binary frame len={len(data)}",
            })
            return
        if isinstance(msg, ControlState):
            self._bridge.on_control_state(self.session_id, msg)
        elif isinstance(msg, Presence):
            self._bridge.on_presence(self.session_id, msg)

    async def _on_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            await self._send_json({"type": "channel_warning", "code": "bad_json", "message": "invalid JSON"})
            return

        msg_type = msg.get("type", "")

        if not self._registered:
            if msg_type == "control_channel_open":
                await self._handle_open(msg)
            else:
                await self._send_json({"type": "channel_warning", "code": "not_opened",
                                       "message": "send control_channel_open first"})
            return

        if msg_type == "take_control":
            await self._handle_take_control(msg)
        elif msg_type == "release_control":
            await self._handle_release_control(msg)
        elif msg_type == "request_control_snapshot":
            await self._handle_snapshot_request(msg)
        elif msg_type == "control_channel_close":
            await self._ws.close(1000, "control_channel_close")
        else:
            await self._send_json({"type": "channel_warning", "code": "unknown_type",
                                   "message": f"unknown message type: {msg_type!r}"})

    # ── Main run loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    await self._on_binary(message)
                else:
                    await self._on_text(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            logger.exception("Unexpected error in session %s", self.session_id)
        finally:
            was_owner = self._bridge.remove_session(self.session_id)
            if was_owner:
                asyncio.ensure_future(self._notify_revoked_peers())
            logger.info("Session %s disconnected", self.session_id)

    async def _notify_revoked_peers(self) -> None:
        await self._broadcast_ownership("disconnected")


# ── Server startup ─────────────────────────────────────────────────────────────

async def start_ws_server(cfg: BridgeConfig, bridge: Bridge) -> None:
    async def _handler(ws: websockets.WebSocketServerProtocol) -> None:
        session = ControllerSession(ws, bridge)
        await session.run()

    async with websockets.serve(_handler, cfg.ws_host, cfg.ws_port):
        logger.info("WebSocket server listening on %s:%d", cfg.ws_host, cfg.ws_port)
        await asyncio.Future()  # run until cancelled
