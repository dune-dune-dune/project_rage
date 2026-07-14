"""SQLite persistence for operator-tunable settings.

Everything the cockpit lets the operator change at runtime (crosshair offset, AI
thresholds, map origin, video/network profiles) lives in a single SQLite file —
``data/cockpit.db``, inside the ``./data`` bind mount, so it survives container
restarts, image rebuilds and the deploy's ``git reset --hard``.

SQLite is a library, not a server: there is no separate container, no new pip
dependency (stdlib ``sqlite3``), and the 20 Hz turret thread never touches it.

Schema lives in ``app/migrations/*.sql`` and is applied once at startup; see
that directory's README for the rules.

Concurrency: one Gunicorn worker (8 gthreads) + the turret thread. Each call
opens its own short-lived connection, so connections are never shared across
threads; ``busy_timeout`` covers the rare write/write overlap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cockpit.db")

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Milliseconds a connection waits for a competing writer before raising
# "database is locked". Default is 0, which would 500 a settings POST that lands
# while another one is committing.
_BUSY_TIMEOUT_MS = 5000

# The sections persisted as JSON blobs in the `settings` table.
KEY_CROSSHAIR = "crosshair"
KEY_AI = "ai"
KEY_MAP = "map"
KEY_NETWORK = "network"


class SettingsDb:
    """Key -> JSON-blob store over SQLite, with SQL-file migrations."""

    def __init__(self, path: str, migrations_dir: Path | None = None) -> None:
        self._path = Path(path)
        # data/ is gitignored and the image never COPYs it, so on a fresh clone
        # the directory may not exist yet — sqlite3.connect() would raise
        # "unable to open database file" and take down startup.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._migrations_dir = migrations_dir or _MIGRATIONS_DIR
        self._write_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None -> autocommit; transactions are opened explicitly
        # where they matter (see migrate()). Wrap every use in closing(): the
        # sqlite3 connection context manager commits but does NOT close.
        conn = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")  # per-connection, must be re-issued
        return conn

    def migrate(self) -> list[str]:
        """Apply every not-yet-applied ``migrations/*.sql``. Returns their names.

        Raises on a failing migration: a half-applied schema must abort startup
        loudly rather than serve requests against it.
        """
        with self._write_lock, closing(self._connect()) as conn:
            # WAL is best-effort: it is right on Linux/Jetson (plain bind mount)
            # but unreliable on Docker Desktop's VirtioFS. Fall back silently.
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode and str(mode[0]).lower() != "wal":
                log.info("sqlite journal_mode=%s (WAL unavailable on this filesystem)", mode[0])
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version    TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL"
                ")"
            )
            done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            applied: list[str] = []
            for sql_file in sorted(self._migrations_dir.glob("*.sql")):
                version = sql_file.name
                if version in done:
                    continue
                self._apply(conn, version, sql_file.read_text())
                applied.append(version)
                log.info("applied migration %s", version)
            return applied

    @staticmethod
    def _apply(conn: sqlite3.Connection, version: str, sql: str) -> None:
        """Run one migration + its bookkeeping row in a single transaction.

        ``executescript`` commits any pending transaction before it runs, so the
        BEGIN/COMMIT must live *inside* the script we hand it — an outer
        ``conn.execute("BEGIN")`` would just be committed away. Hence migration
        files must not open transactions of their own.
        """
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (version, applied_at) VALUES "
            f"({_quote(version)}, {_quote(stamp)});\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise

    def get(self, key: str) -> dict | None:
        """Return the stored dict for ``key``, or None if absent/corrupt."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except ValueError:
            log.warning("settings row %r holds invalid JSON — falling back to defaults", key)
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, data: dict) -> None:
        with self._write_lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(data)),
            )


def _quote(value: str) -> str:
    """SQL string literal (migration names/timestamps are ours, but be strict)."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def import_legacy_json(db: SettingsDb, settings) -> None:
    """One-time import of the pre-SQLite JSON files into the database.

    Reading files off disk cannot be expressed in a .sql migration, so this runs
    as a separate step right after :meth:`SettingsDb.migrate`. A section is
    imported only when the DB has no row for it yet; the source file is then
    renamed to ``*.json.migrated`` so that a lost cockpit.db cannot silently
    resurrect stale settings on the next boot.
    """
    legacy = (
        (KEY_CROSSHAIR, settings.crosshair_file),
        (KEY_AI, settings.ai_settings_file),
        (KEY_MAP, settings.map_settings_file),
    )
    for key, path in legacy:
        source = Path(path)
        if not source.exists() or db.get(key) is not None:
            continue
        try:
            data = json.loads(source.read_text())
        except (ValueError, OSError):
            log.warning("legacy %s is unreadable — skipping import, defaults apply", source)
            continue
        if not isinstance(data, dict):
            continue
        db.put(key, data)
        try:
            source.rename(source.with_suffix(source.suffix + ".migrated"))
        except OSError:
            log.warning("imported %s but could not rename it", source)
        log.info("imported legacy %s into the database as %r", source.name, key)
