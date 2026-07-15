"""Server-side relay for the live targets feed.

The targets server lives on a SEPARATE VM (default ``10.31.0.100:8766``),
reachable only from the Jetson over the ``wg-targets`` WireGuard tunnel — the
operator's browser has NO route to it. So this process (the cockpit, on the
Jetson) is the WebSocket *client*: a background thread connects, subscribes, and
caches the latest ``targets`` frame. The browser then polls ``/api/targets`` on
the cockpit's own origin (see static/targets.js) and never talks to the VM
directly.

This mirrors the rangefinder reader (:mod:`app.turret` ``_run_lidar_loop``): a
dedicated daemon thread, independent of the 20 Hz command loop, so a slow/dead
upstream never stalls control. It reuses ``simple_websocket`` (already a
dependency via flask-sock) as the WS client — no new package.
"""

from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger("cockpit.targets")

# Drop cached targets this many seconds after the last frame, so a dead upstream
# clears the map instead of freezing the last known positions.
_STALE_SECONDS = 5.0
_RECEIVE_TIMEOUT = 5.0
_BACKOFF_START = 2.0
_BACKOFF_MAX = 30.0


class TargetsRelay:
    """Maintains a WS connection to the targets VM and caches the latest frame."""

    def __init__(self, host: str, port: int, enabled: bool) -> None:
        # Trailing slash is required: simple_websocket.Client sends the path
        # verbatim and rejects an empty target ("Illegal target characters"),
        # unlike a browser which defaults an empty path to "/". The reference
        # browser client connected with no path, i.e. the server serves at "/".
        self._url = f"ws://{host}:{port}/" if host else ""
        self._enabled = enabled and bool(host)
        self._lock = threading.Lock()
        self._targets: dict = {}
        self._updated = 0.0  # time.monotonic() of the last accepted frame
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled:
            log.info("targets relay disabled (TARGETS_ENABLED off or no host)")
            return
        self._thread = threading.Thread(
            target=self._run, name="targets-relay", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        """Latest targets dict, or ``{}`` if disabled or the feed is stale."""
        with self._lock:
            if not self._updated:
                return {}
            if time.monotonic() - self._updated > _STALE_SECONDS:
                return {}
            return dict(self._targets)

    # --- internals ---------------------------------------------------------

    def _run(self) -> None:
        import simple_websocket  # dependency of flask-sock

        backoff = _BACKOFF_START
        while not self._stop.is_set():
            try:
                ws = simple_websocket.Client(self._url)
                log.info("targets relay connected: %s", self._url)
                backoff = _BACKOFF_START
                # The server routes on this; a server without routing treats it
                # as a no-op.
                ws.send(json.dumps({"type": "subscribe", "mode": "targets_only"}))
                try:
                    while not self._stop.is_set():
                        raw = ws.receive(timeout=_RECEIVE_TIMEOUT)
                        if raw is None:
                            continue  # timeout tick — re-check the stop flag
                        self._ingest(raw)
                finally:
                    ws.close()
            except Exception as exc:  # ConnectionClosed, ConnectionError, etc.
                if self._stop.is_set():
                    break
                log.warning(
                    "targets relay disconnected (%s); retry in %.0fs", exc, backoff
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 1.5, _BACKOFF_MAX)

    def _ingest(self, raw: object) -> None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict) or data.get("type") != "status":
            return
        targets = data.get("targets")
        if not isinstance(targets, dict):
            return
        with self._lock:
            self._targets = targets
            self._updated = time.monotonic()
