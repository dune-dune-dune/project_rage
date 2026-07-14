"""Model exporter: turns uploaded YOLO weights into browser-loadable ONNX.

This is the only place in the stack that owns ultralytics/torch. It lives in its
own container on purpose: the cockpit's single Gunicorn worker also runs the
20 Hz turret loop (400 ms deadman), and a torch export can peg a Jetson CPU for
minutes — long enough for the arbiter to kill that worker and take turret control
down with it. Here, a slow or crashing export costs nothing but a failed job.

It never talks to the turret and has no state of its own: the cockpit writes an
uploaded checkpoint into the shared ``./data`` bind mount and POSTs the directory;
this service writes ``model.onnx`` + ``classes.json`` back into the same directory
and reports the input size and class names it found.

    POST /convert  {"dir": "/data/models/<id>", "source": "source.pt", "imgsz": 640}
                -> {"ok": true, "imgsz": 640, "classes": {...}, "size_bytes": 1234}
    GET  /healthz  -> {"status": "ok"}   (the cockpit's converter indicator)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from flask import Flask, jsonify, request

log = logging.getLogger("exporter")

# The shared bind mount. Every path the cockpit sends must resolve inside it — the
# request comes from an authenticated cockpit, but a path it can steer is still a
# path this process would happily read and overwrite.
DATA_ROOT = Path(os.environ.get("EXPORTER_DATA_DIR", "/data")).resolve()

DEFAULT_IMGSZ = 640
OPSET = 12

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.post("/convert")
def convert():
    payload = request.get_json(silent=True) or {}
    try:
        directory = _safe_dir(payload.get("dir"))
    except ValueError as err:
        return jsonify(ok=False, error=str(err)), 400

    source_name = str(payload.get("source") or "source.pt")
    if source_name not in ("source.pt", "source.onnx"):
        return jsonify(ok=False, error="Невідоме джерело"), 400
    source = directory / source_name
    if not source.exists():
        return jsonify(ok=False, error="Файл ваг не знайдено"), 400

    imgsz = _clamp_imgsz(payload.get("imgsz"))

    try:
        from ultralytics import YOLO
    except ModuleNotFoundError:
        return jsonify(ok=False, error="Конвертер зібрано без ultralytics"), 500

    try:
        model = YOLO(str(source))
        # Prefer the size the checkpoint was TRAINED at over the caller's default:
        # the cockpit has no way to know it, and exporting a 960-px model at 640
        # would work but quietly cost accuracy. The size we return is what the
        # browser letterboxes to, so the two can never drift apart.
        imgsz = _checkpoint_imgsz(model) or imgsz
        exported = Path(model.export(format="onnx", imgsz=imgsz, opset=OPSET, dynamic=False, simplify=True))
    except Exception as err:  # ultralytics raises a wide variety of errors
        log.exception("export failed for %s", source)
        return jsonify(ok=False, error=f"Експорт не вдався: {err}"), 500

    # Ultralytics writes the .onnx next to the .pt, under the checkpoint's name.
    target = directory / "model.onnx"
    if exported.resolve() != target.resolve():
        shutil.move(str(exported), target)

    names = getattr(model, "names", None) or {}
    classes = {str(index): str(name) for index, name in dict(names).items()}
    (directory / "classes.json").write_text(json.dumps(classes, ensure_ascii=False, indent=2))

    log.info("exported %s -> %s (imgsz=%d, %d classes)", source.name, target, imgsz, len(classes))
    return jsonify(ok=True, imgsz=imgsz, classes=classes, size_bytes=target.stat().st_size)


def _safe_dir(raw: object) -> Path:
    if not isinstance(raw, str) or not raw:
        return _reject("Не вказано каталог моделі")
    directory = Path(raw).resolve()
    if not directory.is_relative_to(DATA_ROOT):
        return _reject("Каталог поза межами сховища моделей")
    if not directory.is_dir():
        return _reject("Каталог моделі не існує")
    return directory


def _reject(message: str):
    raise ValueError(message)


def _checkpoint_imgsz(model) -> int | None:
    """The input size the checkpoint was trained at, if it records one.

    Ultralytics keeps the training args on the inner torch module. The attribute
    is a namespace in some versions and a dict in others, and `imgsz` may be a
    scalar or a [w, h] pair — so read it defensively and give up quietly rather
    than fail an otherwise fine export.
    """
    try:
        args = model.model.args
        raw = args["imgsz"] if isinstance(args, dict) else getattr(args, "imgsz", None)
    except (AttributeError, KeyError, TypeError):
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if 32 <= value <= 4096 and value % 32 == 0 else None


def _clamp_imgsz(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_IMGSZ
    # YOLO strides are multiples of 32; anything else silently reshapes the model.
    if value < 32 or value > 4096 or value % 32:
        return DEFAULT_IMGSZ
    return value
