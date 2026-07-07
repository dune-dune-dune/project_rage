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
from .store import CrosshairStore
from .turret import TurretController


def create_app() -> Flask:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Flask(__name__)
    settings = load_settings()
    controller = TurretController(settings)
    controller.start()
    atexit.register(controller.stop)

    app.config["SETTINGS"] = settings
    app.config["TURRET"] = controller
    app.config["CROSSHAIR"] = CrosshairStore(settings.crosshair_file)
    app.register_blueprint(bp)
    return app
