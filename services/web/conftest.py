"""Shared pytest fixtures for the cockpit web service.

Fixtures build real Flask apps (dry-run, so no UDP socket is ever opened) via the
production :func:`create_app` factory, plus a bare :class:`TurretController` whose
sender thread is NOT started for pure packet-logic unit tests.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make ``import app`` resolve to services/web/app regardless of pytest's cwd.
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.turret import TurretController  # noqa: E402

@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point COCKPIT_DATA_DIR at a per-test tmp dir.

    Without this every test would migrate and write the real
    services/web/data/cockpit.db — the operator's live crosshair/AI/map settings.
    Tests that need the path themselves can just re-setenv it (monkeypatch wins:
    ``_set_env`` below deliberately does not touch this variable).
    """
    data_dir = tmp_path / "data"
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(data_dir))
    return data_dir


def _set_env(dry_run: bool) -> None:
    os.environ["RWS_DRY_RUN"] = "true" if dry_run else "false"


@pytest.fixture
def app_factory():
    """Return a factory building configured apps; stops their threads on teardown."""
    created = []

    def _factory(*, dry_run: bool = True):
        _set_env(dry_run)
        application = create_app()
        application.config["TESTING"] = True
        created.append(application)
        return application

    yield _factory

    for application in created:
        try:
            application.config["TURRET"].stop()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authed_client(app):
    # The cockpit is open (no login gate); kept as an alias so tests written
    # against the former PIN gate keep working unchanged.
    return app.test_client()


@pytest.fixture
def controller():
    """A dry-run TurretController with its sender thread NOT started."""
    _set_env(dry_run=True)
    return TurretController(load_settings())
