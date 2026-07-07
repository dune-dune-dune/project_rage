"""Persisted crosshair position.

The crosshair offset (horizontal / vertical, as a percentage of the viewport
from centre) is stored in a small JSON file so it survives restarts and can be
consumed by other tooling later. Thread-safe: the cockpit is multi-threaded.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

# Offset range in percent of the viewport, measured from centre.
_LIMIT = 50.0
_DEFAULT = {"x": 0.0, "y": 0.0}


def _clamp(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(-_LIMIT, min(_LIMIT, number))


class CrosshairStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                return dict(_DEFAULT)
            return {"x": _clamp(raw.get("x")), "y": _clamp(raw.get("y"))}

    def save(self, x: object, y: object) -> dict:
        data = {"x": _clamp(x), "y": _clamp(y)}
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data))
        return data
