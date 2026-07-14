"""SQL migration engine + the one-time import of the pre-SQLite JSON files."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.db import KEY_CROSSHAIR, KEY_MAP, KEY_NETWORK, SettingsDb, import_legacy_json


def _versions(db_path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def test_migrations_apply_on_a_fresh_database(tmp_path):
    path = tmp_path / "cockpit.db"
    applied = SettingsDb(str(path)).migrate()
    # Every shipped file runs, in filename order. Asserted as a prefix + sorted-ness
    # rather than a literal list, so adding a migration does not fail this test.
    assert applied[:2] == ["0001_init.sql", "0002_seed_network.sql"]
    assert applied == sorted(applied)
    assert _versions(path) == applied
    # 0002 seeds the network profiles.
    assert SettingsDb(str(path)).get(KEY_NETWORK)["video_mode"] == "local"


def test_creates_missing_data_directory(tmp_path):
    """data/ is gitignored and never COPYd into the image — connect() must not blow up."""
    db = SettingsDb(str(tmp_path / "nested" / "deeper" / "cockpit.db"))
    assert db.migrate()


def test_applied_migrations_are_skipped_on_the_next_boot(tmp_path):
    path = tmp_path / "cockpit.db"
    first = SettingsDb(str(path)).migrate()
    db = SettingsDb(str(path))
    db.put(KEY_CROSSHAIR, {"x": 7.0, "y": 0.0})

    assert db.migrate() == []  # nothing re-runs
    assert _versions(path) == first
    assert db.get(KEY_CROSSHAIR) == {"x": 7.0, "y": 0.0}  # and nothing is clobbered


def test_a_new_sql_file_runs_while_old_ones_do_not(tmp_path, monkeypatch):
    import app.db as db_module

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_init.sql").write_text(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    path = tmp_path / "cockpit.db"
    db = db_module.SettingsDb(str(path), migrations_dir=migrations)
    assert db.migrate() == ["0001_init.sql"]

    (migrations / "0002_later.sql").write_text(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('later', '{\"ok\": true}');"
    )
    assert db.migrate() == ["0002_later.sql"]  # only the new one
    assert db.get("later") == {"ok": True}
    assert db.migrate() == []


def test_a_failing_migration_rolls_back_and_raises(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_init.sql").write_text(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
        "INSERT INTO settings (key, value) VALUES ('a', '{}');\n"
        "THIS IS NOT SQL;"
    )
    path = tmp_path / "cockpit.db"
    with pytest.raises(sqlite3.Error):
        SettingsDb(str(path), migrations_dir=migrations).migrate()

    # The whole file was rolled back: no bookkeeping row, no half-applied table.
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchall()
    assert rows == []
    assert _versions(path) == []


def test_legacy_json_is_imported_once_and_renamed(tmp_path, settings_stub):
    crosshair = tmp_path / "crosshair.json"
    crosshair.write_text(json.dumps({"x": 13.0, "y": -2.0}))
    map_file = tmp_path / "map_settings.json"
    map_file.write_text(json.dumps({"lat": 46.6, "lon": 32.6, "north_correction": 180}))

    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()
    import_legacy_json(db, settings_stub)

    assert db.get(KEY_CROSSHAIR) == {"x": 13.0, "y": -2.0}
    assert db.get(KEY_MAP)["lat"] == 46.6
    assert not crosshair.exists()
    assert (tmp_path / "crosshair.json.migrated").exists()

    # A second run is a no-op: the renamed files are gone and the rows stand.
    db.put(KEY_CROSSHAIR, {"x": 1.0, "y": 1.0})
    import_legacy_json(db, settings_stub)
    assert db.get(KEY_CROSSHAIR) == {"x": 1.0, "y": 1.0}


def test_import_never_overwrites_an_existing_row(tmp_path, settings_stub):
    (tmp_path / "crosshair.json").write_text(json.dumps({"x": 13.0, "y": 0.0}))
    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()
    db.put(KEY_CROSSHAIR, {"x": -5.0, "y": 0.0})

    import_legacy_json(db, settings_stub)

    assert db.get(KEY_CROSSHAIR) == {"x": -5.0, "y": 0.0}
    assert (tmp_path / "crosshair.json").exists()  # not consumed


@pytest.fixture
def settings_stub(tmp_path):
    """Minimal stand-in for Settings: only the legacy file paths are read."""

    class _Stub:
        crosshair_file = str(tmp_path / "crosshair.json")
        ai_settings_file = str(tmp_path / "ai_settings.json")
        map_settings_file = str(tmp_path / "map_settings.json")

    return _Stub()
