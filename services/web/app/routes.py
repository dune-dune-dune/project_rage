"""HTTP routes for the cockpit: the page, health, input intake and status."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

bp = Blueprint("cockpit", __name__)


def _controller():
    return current_app.config["TURRET"]


def _crosshair():
    return current_app.config["CROSSHAIR"]


@bp.get("/")
def index():
    settings = current_app.config["SETTINGS"]
    return render_template(
        "index.html",
        cameras=settings.cameras,
        dry_run=settings.dry_run,
        crosshair=_crosshair().load(),
        fire_mode=_controller().snapshot()["fire_mode"],
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
