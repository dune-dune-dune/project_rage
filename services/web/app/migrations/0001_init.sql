-- Key/value settings table. One JSON blob per section: 'crosshair', 'ai',
-- 'map', 'network'. Kept schemaless on purpose: every section already has a
-- normalizer + clamps in app/store.py, so the DB only has to persist bytes.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
