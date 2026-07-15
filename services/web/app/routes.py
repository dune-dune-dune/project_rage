"""HTTP routes for the cockpit: the page, health, input intake and status."""

from __future__ import annotations

import json
import os

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .store import CLASSES_FILENAME, MODEL_FILENAME, SOURCE_ONNX, SOURCE_PT, STATUS_READY

bp = Blueprint("cockpit", __name__)


def _controller():
    return current_app.config["TURRET"]


def _crosshair():
    return current_app.config["CROSSHAIR"]


def _ai_settings():
    return current_app.config["AI_SETTINGS"]


def _map_settings():
    return current_app.config["MAP_SETTINGS"]


def _network():
    return current_app.config["NETWORK"]


def _models():
    return current_app.config["MODELS"]


def _jobs():
    return current_app.config["MODEL_JOBS"]


def _asset_version() -> int:
    """A cache-busting version stamp = newest mtime of the JS/CSS assets.

    Appended as ?v=… to script/worker URLs so the browser never serves a stale
    cached ai.js / ai-worker.js after a code change (a real footgun otherwise —
    workers are cached aggressively).
    """
    static = current_app.static_folder or ""
    latest = 0
    for name in (
        "ai.js", "ai-worker.js", "cockpit.js", "cockpit.css",
        "map.js", "compass.js", "heartbeat-worker.js", "models.js",
        "ws-client.js", "targets.js",
    ):
        try:
            latest = max(latest, int(os.path.getmtime(os.path.join(static, name))))
        except OSError:
            pass
    return latest


def _model_payload(model: dict) -> dict:
    """One model row as the browser sees it: metadata + the URLs of its files."""
    return {
        **model,
        "url": url_for("cockpit.model_asset", model_id=model["id"], filename=MODEL_FILENAME),
        "classes_url": url_for("cockpit.model_asset", model_id=model["id"], filename=CLASSES_FILENAME),
    }


def _ai_config(settings) -> dict:
    """Client-side AI config injected into the page as window.__AI__.

    Bundles the ACTIVE model (its URLs and input size) and the visual-servo tuning
    (gain/deadzone/max velocity) with the operator-tunable detection thresholds
    (conf/min_size) persisted server-side. The model is looked up per request, so
    activating another one and reloading is enough — but the panel also hot-swaps
    it without a reload (see models.js -> AI.setModel).
    """
    thresholds = _ai_settings().load()
    active = _models().active()
    return {
        "model": _model_payload(active) if active else None,
        # Kept flat as well: ai.js reads these directly, and they are what a model
        # switch replaces at runtime.
        "model_url": url_for("cockpit.model_asset", model_id=active["id"], filename=MODEL_FILENAME) if active else None,
        "classes_url": (
            url_for("cockpit.model_asset", model_id=active["id"], filename=CLASSES_FILENAME) if active else None
        ),
        "model_available": active is not None,
        "imgsz": active["imgsz"] if active else settings.ai_imgsz,
        "worker_url": url_for("static", filename="ai-worker.js") + f"?v={_asset_version()}",
        "gain": settings.track_gain,
        "deadzone": settings.track_deadzone,
        "max_velocity": settings.track_max_velocity,
        "conf": thresholds["conf"],
        "min_size": thresholds["min_size"],
        "motion_thresh": thresholds["motion_thresh"],
        "max_shift": thresholds["max_shift"],
    }


@bp.get("/")
def index():
    settings = current_app.config["SETTINGS"]
    # ?video=local|remote overrides the saved profile for this page load only.
    # Recovery hatch: saving an unreachable gateway host reloads the cockpit into
    # a config whose video is dead, and the settings panel lives inside it.
    override = request.args.get("video")
    return render_template(
        "index.html",
        cameras=_network().cameras(override),
        network=_network().load(),
        stream_options=_network().stream_options(),
        dry_run=settings.dry_run,
        crosshair=_crosshair().load(),
        map_settings=_map_settings().load(),
        fire_mode=_controller().snapshot()["fire_mode"],
        speed=_controller().speed_config(),
        ai=_ai_config(settings),
        targets_ws_host=settings.targets_ws_host,
        targets_ws_port=settings.targets_ws_port,
        asset_version=_asset_version(),
    )


@bp.get("/healthz")
def healthz():
    return jsonify(status="ok")


@bp.post("/api/input")
def api_input():
    payload = request.get_json(silent=True) or {}
    _controller().apply_input(payload)
    return ("", 204)


@bp.get("/api/status")
def api_status():
    return jsonify(_controller().snapshot())


@bp.get("/api/crosshair")
def api_crosshair_get():
    return jsonify(_crosshair().load())


@bp.post("/api/crosshair")
def api_crosshair_set():
    payload = request.get_json(silent=True) or {}
    return jsonify(_crosshair().save(payload.get("x", 0), payload.get("y", 0)))


@bp.get("/api/map-settings")
def api_map_settings_get():
    return jsonify(_map_settings().load())


@bp.post("/api/map-settings")
def api_map_settings_set():
    payload = request.get_json(silent=True) or {}
    return jsonify(_map_settings().save(payload))


@bp.get("/api/network-settings")
def api_network_settings_get():
    return jsonify(_network().load())


@bp.post("/api/network-settings")
def api_network_settings_set():
    """Save the video gateway profiles / active mode.

    The client reloads the page afterwards: the camera <video> elements and their
    RTCPeerConnections are built once at load, so the new URLs only take effect
    on a fresh document.
    """
    payload = request.get_json(silent=True) or {}
    return jsonify(_network().save(payload))


@bp.post("/api/track")
def api_track():
    """Auto-track aim override from the browser visual servo (aim only, no fire)."""
    payload = request.get_json(silent=True) or {}
    _controller().apply_track(payload)
    return ("", 204)


@bp.get("/api/ai-settings")
def api_ai_settings_get():
    return jsonify(_ai_settings().load())


@bp.post("/api/ai-settings")
def api_ai_settings_set():
    payload = request.get_json(silent=True) or {}
    return jsonify(_ai_settings().save(
        payload.get("conf"), payload.get("min_size"),
        payload.get("motion_thresh"), payload.get("max_shift"),
    ))


# --- AI model library ---------------------------------------------------------


@bp.get("/api/models")
def api_models_list():
    """The library + which model is active + whether the exporter is reachable.

    The panel polls this while a conversion is running.
    """
    store = _models()
    active = store.active()
    return jsonify({
        "active": active["id"] if active else None,
        "models": [_model_payload(model) for model in store.list()],
        "exporter_online": _jobs().exporter_online(),
    })


@bp.post("/api/models")
def api_models_upload():
    """Upload new weights: multipart ``file`` (+ optional ``classes``) and ``name``.

    A ``.pt`` is registered and handed to the exporter sidecar, which converts it
    asynchronously — the response returns immediately (202) and the panel polls
    for the status. A ready-made ``.onnx`` is accepted as-is and is ready at once;
    that path needs no exporter at all, so it is the recovery hatch when the
    sidecar is down.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error="Файл не додано"), 400
    suffix = os.path.splitext(upload.filename)[1].lower()
    if suffix not in (".pt", ".onnx"):
        return jsonify(error="Підтримуються лише файли .pt та .onnx"), 400

    store = _models()
    source = SOURCE_ONNX if suffix == ".onnx" else SOURCE_PT
    model = store.create(request.form.get("name"), source)
    directory = store.dir_for(model["id"])
    assert directory is not None  # the id was just generated by the store

    upload.save(directory / f"source{suffix}")

    if source == SOURCE_PT:
        _jobs().submit(model["id"], f"source{suffix}", model["imgsz"])
        return jsonify(_model_payload(model)), 202

    # A ready .onnx is the model: no conversion, so it must arrive complete.
    os.replace(directory / "source.onnx", directory / MODEL_FILENAME)
    classes = _uploaded_classes(request.files.get("classes"))
    (directory / CLASSES_FILENAME).write_text(json.dumps(classes, ensure_ascii=False, indent=2))
    updated = store.set_status(
        model["id"],
        STATUS_READY,
        classes=classes,
        size_bytes=(directory / MODEL_FILENAME).stat().st_size,
    )
    return jsonify(_model_payload(updated or model)), 201


def _uploaded_classes(upload) -> dict:
    """The optional classes.json shipped with a ready .onnx.

    Unlike the .pt path (where the exporter reads the names straight out of the
    checkpoint) nothing here can infer them, so an absent or unreadable file just
    means unlabelled boxes — not a failed upload.
    """
    if upload is None or not upload.filename:
        return {}
    try:
        parsed = json.loads(upload.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@bp.post("/api/models/<model_id>/activate")
def api_models_activate(model_id: str):
    model = _models().set_active(model_id)
    if model is None:
        return jsonify(error="Модель недоступна"), 400
    return jsonify(_model_payload(model))


@bp.post("/api/models/<model_id>/rename")
def api_models_rename(model_id: str):
    payload = request.get_json(silent=True) or {}
    model = _models().rename(model_id, payload.get("name"))
    if model is None:
        return jsonify(error="Модель не знайдено"), 404
    return jsonify(_model_payload(model))


@bp.delete("/api/models/<model_id>")
def api_models_delete(model_id: str):
    ok, reason = _models().delete(model_id)
    if not ok:
        return jsonify(error=reason), 400
    return ("", 204)


@bp.get("/assets/models/<model_id>/<filename>")
def model_asset(model_id: str, filename: str):
    """Serve one model's ONNX weights or its class-names sidecar.

    ``data/`` is not a Flask static folder, and the id/filename are the only
    request-controlled parts of the path — ModelStore whitelists both, so neither
    can traverse out of the model directory.
    """
    path = _models().file_for(model_id, filename)
    if path is None:
        abort(404)
    if not path.exists():
        if filename == CLASSES_FILENAME:
            return jsonify({})
        abort(404, description="ONNX model not found")
    mimetype = "application/json" if filename == CLASSES_FILENAME else "application/octet-stream"
    return send_file(path, mimetype=mimetype)


@bp.get("/assets/model.onnx")
def model_onnx():
    """The active model's weights, under the pre-library URL (back-compat)."""
    active = _models().active()
    if active is None:
        abort(404, description="No AI model in the library")
    return redirect(url_for("cockpit.model_asset", model_id=active["id"], filename=MODEL_FILENAME))


@bp.get("/assets/classes.json")
def model_classes():
    """The active model's class names, under the pre-library URL (back-compat)."""
    active = _models().active()
    if active is None:
        return jsonify({})
    return redirect(url_for("cockpit.model_asset", model_id=active["id"], filename=CLASSES_FILENAME))
