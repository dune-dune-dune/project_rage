"""HTTP routes for the cockpit: the page, health, input intake and status."""

from __future__ import annotations

import hmac
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
    session,
    url_for,
)

bp = Blueprint("cockpit", __name__)

# Endpoints reachable without a valid session when the PIN gate is enabled.
_PUBLIC_ENDPOINTS = {"cockpit.login", "cockpit.login_post", "cockpit.healthz", "static"}


@bp.before_app_request
def require_auth():
    """Gate every request behind the PIN login when COCKPIT_PIN is configured.

    Registered app-wide (``before_app_request``, not blueprint-scoped) so it also
    covers the flask-sock ``/api/ws`` route, which is attached to the app rather
    than this blueprint — a blueprint ``before_request`` would leave it unguarded.

    No PIN configured -> the gate is disabled (open access). Otherwise an
    authenticated session passes; public endpoints (login page, health, static)
    pass; API/asset requests get a 401; page requests redirect to /login.
    """
    settings = current_app.config["SETTINGS"]
    if not settings.pin:
        return None
    if session.get("authed"):
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if request.path.startswith("/api") or request.path.startswith("/assets"):
        abort(401)
    return redirect(url_for("cockpit.login"))


def _controller():
    return current_app.config["TURRET"]


def _crosshair():
    return current_app.config["CROSSHAIR"]


def _ai_settings():
    return current_app.config["AI_SETTINGS"]


def _map_settings():
    return current_app.config["MAP_SETTINGS"]


def _asset_version() -> int:
    """A cache-busting version stamp = newest mtime of the JS/CSS assets.

    Appended as ?v=… to script/worker URLs so the browser never serves a stale
    cached ai.js / ai-worker.js after a code change (a real footgun otherwise —
    workers are cached aggressively).
    """
    static = current_app.static_folder or ""
    latest = 0
    for name in ("ai.js", "ai-worker.js", "cockpit.js", "cockpit.css", "map.js", "heartbeat-worker.js"):
        try:
            latest = max(latest, int(os.path.getmtime(os.path.join(static, name))))
        except OSError:
            pass
    return latest


def _ai_config(settings) -> dict:
    """Client-side AI config injected into the page as window.__AI__.

    Bundles the served model URL, model input size and the visual-servo tuning
    (gain/deadzone/max velocity) with the operator-tunable detection thresholds
    (conf/min_size) persisted server-side.
    """
    thresholds = _ai_settings().load()
    return {
        "model_url": url_for("cockpit.model_onnx"),
        "classes_url": url_for("cockpit.model_classes"),
        "worker_url": url_for("static", filename="ai-worker.js") + f"?v={_asset_version()}",
        "model_available": os.path.exists(settings.model_file),
        "imgsz": settings.ai_imgsz,
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
    return render_template(
        "index.html",
        cameras=settings.cameras,
        dry_run=settings.dry_run,
        crosshair=_crosshair().load(),
        map_settings=_map_settings().load(),
        fire_mode=_controller().snapshot()["fire_mode"],
        speed=_controller().speed_config(),
        ai=_ai_config(settings),
        asset_version=_asset_version(),
    )


@bp.get("/healthz")
def healthz():
    return jsonify(status="ok")


@bp.get("/login")
def login():
    settings = current_app.config["SETTINGS"]
    if not settings.pin or session.get("authed"):
        return redirect(url_for("cockpit.index"))
    return render_template("login.html", error=None)


@bp.post("/login")
def login_post():
    settings = current_app.config["SETTINGS"]
    if not settings.pin:
        return redirect(url_for("cockpit.index"))
    pin = (request.form.get("pin") or "").strip()
    if hmac.compare_digest(pin, settings.pin):
        session["authed"] = True
        session.permanent = True
        return redirect(url_for("cockpit.index"))
    return render_template("login.html", error="Невірний PIN"), 401


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("cockpit.login"))


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


@bp.get("/assets/model.onnx")
def model_onnx():
    """Serve the exported YOLO weights (data/ is not a Flask static folder)."""
    path = current_app.config["SETTINGS"].model_file
    if not os.path.exists(path):
        abort(404, description="ONNX model not found — run scripts/export_onnx.py")
    return send_file(path, mimetype="application/octet-stream")


@bp.get("/assets/classes.json")
def model_classes():
    """Serve the optional class-names sidecar written by the export script."""
    path = current_app.config["SETTINGS"].classes_file
    if not os.path.exists(path):
        return jsonify({})
    return send_file(path, mimetype="application/json")
