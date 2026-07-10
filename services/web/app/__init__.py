"""Flask application factory for the turret cockpit.

The single :class:`~app.turret.TurretController` is created once and started
here, so exactly one process owns the UDP channel and the 20 Hz send loop. This
is why the service MUST run with a single Gunicorn worker.
"""

from __future__ import annotations

import atexit
import logging
import os

from flask import Flask

from .config import load_settings
from .routes import bp
from .store import AiSettingsStore, CrosshairStore
from .turret import TurretController
from .ws import sock

log = logging.getLogger("cockpit")


def create_app() -> Flask:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Flask(__name__)
    settings = load_settings()

    # Session secret for the PIN login. A stable SECRET_KEY (from .env) keeps
    # sessions valid across restarts; without it we fall back to an ephemeral key
    # so login still works this run but every session drops on restart.
    if settings.secret_key:
        app.secret_key = settings.secret_key
    else:
        app.secret_key = os.urandom(32)
        if settings.pin:
            log.warning("SECRET_KEY not set: login sessions will reset on restart")
    if not settings.pin:
        log.warning("COCKPIT_PIN not set: the cockpit is served WITHOUT authentication")

    controller = TurretController(settings)
    controller.start()
    atexit.register(controller.stop)

    app.config["SETTINGS"] = settings
    app.config["TURRET"] = controller
    app.config["CROSSHAIR"] = CrosshairStore(settings.crosshair_file)
    app.config["AI_SETTINGS"] = AiSettingsStore(settings.ai_settings_file)
    app.register_blueprint(bp)
    sock.init_app(app)  # /api/ws control channel (auth via the same before_request gate)
    return app
