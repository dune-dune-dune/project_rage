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
from .db import SettingsDb, import_legacy_json
from .model_jobs import ModelJobs
from .routes import bp
from .store import (
    AiSettingsStore,
    CrosshairStore,
    MapSettingsStore,
    ModelStore,
    NetworkStore,
    import_builtin_model,
)
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

    # Settings storage: apply the SQL migrations, then import any pre-SQLite JSON
    # files. Both must run before the stores, which read a table that does not
    # exist until the migrations have run.
    db = SettingsDb(settings.db_file)
    db.migrate()
    import_legacy_json(db, settings)

    controller = TurretController(settings)
    controller.start()
    atexit.register(controller.stop)

    models = ModelStore(db, settings.models_dir, settings.ai_imgsz)
    import_builtin_model(models, settings)
    # A restart (every deploy does one) kills any conversion in flight: the job
    # thread dies with the process and the sidecar's work is orphaned. Without
    # this the row would sit at "converting" forever.
    interrupted = models.fail_interrupted()
    if interrupted:
        log.warning("%d model conversion(s) were interrupted by a restart", interrupted)

    # Uploaded weights are large; Flask buffers the whole multipart body, so cap it.
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
    app.config["SETTINGS"] = settings
    app.config["TURRET"] = controller
    app.config["DB"] = db
    app.config["CROSSHAIR"] = CrosshairStore(db)
    app.config["AI_SETTINGS"] = AiSettingsStore(db)
    app.config["MAP_SETTINGS"] = MapSettingsStore(db)
    app.config["NETWORK"] = NetworkStore(db)
    app.config["MODELS"] = models
    app.config["MODEL_JOBS"] = ModelJobs(models, settings.exporter_url, settings.exporter_data_dir)
    app.register_blueprint(bp)
    sock.init_app(app)  # /api/ws control channel (auth via the same before_request gate)
    return app
