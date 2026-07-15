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
from .targets import TargetsRelay
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

    # The cockpit is served openly — there is no login gate. An ephemeral secret
    # key is still set so Flask's session machinery is usable if ever needed.
    app.secret_key = os.urandom(32)

    # Settings storage: apply the SQL migrations, then import any pre-SQLite JSON
    # files. Both must run before the stores, which read a table that does not
    # exist until the migrations have run.
    db = SettingsDb(settings.db_file)
    db.migrate()
    import_legacy_json(db, settings)

    controller = TurretController(settings)
    controller.start()
    atexit.register(controller.stop)

    # Relay the targets feed from the separate VM (over the wg-targets tunnel) so
    # the browser can poll it from the cockpit's own origin. No-op unless enabled.
    targets_relay = TargetsRelay(
        settings.targets_ws_host, settings.targets_ws_port, settings.targets_enabled
    )
    targets_relay.start()
    atexit.register(targets_relay.stop)

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
    app.config["TARGETS_RELAY"] = targets_relay
    app.config["DB"] = db
    app.config["CROSSHAIR"] = CrosshairStore(db)
    app.config["AI_SETTINGS"] = AiSettingsStore(db)
    app.config["MAP_SETTINGS"] = MapSettingsStore(db)
    app.config["NETWORK"] = NetworkStore(db)
    app.config["MODELS"] = models
    app.config["MODEL_JOBS"] = ModelJobs(models, settings.exporter_url, settings.exporter_data_dir)
    app.register_blueprint(bp)
    sock.init_app(app)  # /api/ws control channel
    return app
