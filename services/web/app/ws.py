"""WebSocket control-input channel.

A single persistent WebSocket replaces the HTTP ``POST /api/input`` polling as the
primary path for streaming operator intent (WASD/fire/speed) to the turret. It
carries the exact same JSON payload as the POST route and feeds it through the
same :meth:`TurretController.apply_input`, so the 20 Hz sender loop and deadman
are unaffected — only the transport changes.

Served by flask-sock inside the existing Flask app: same port, same PIN/session
auth (the ``before_request`` gate runs on the handshake), same single-process
UDP-owner model. Each connection occupies one Gunicorn gthread thread for its
lifetime; the ``TurretController`` runs in its own daemon thread regardless.
"""

from __future__ import annotations

import json
import logging

from flask import current_app
from flask_sock import Sock

log = logging.getLogger("cockpit.ws")

sock = Sock()


@sock.route("/api/ws")
def control_ws(ws) -> None:
    """Receive control-intent JSON frames and apply them until the socket closes.

    Reuses the POST payload shape ``{up,down,left,right,safety,fire,fire_mode,
    speed_level}``. Malformed frames are ignored so one bad message never drops
    the control link. Auth is already enforced on the handshake by the app's
    ``before_request`` gate.
    """
    controller = current_app.config["TURRET"]
    log.info("control WebSocket connected")
    try:
        while True:
            message = ws.receive()
            if message is None:  # client closed the connection
                break
            try:
                payload = json.loads(message)
            except (ValueError, TypeError):
                continue  # ignore non-JSON frames, keep the link alive
            if isinstance(payload, dict):
                controller.apply_input(payload)
    except Exception:  # noqa: BLE001 - transport errors just end this connection
        log.debug("control WebSocket closed with error", exc_info=True)
    finally:
        log.info("control WebSocket disconnected")
