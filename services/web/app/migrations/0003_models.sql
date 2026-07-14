-- AI model library: the operator can upload new YOLO weights and switch between
-- them at runtime. One row per model; the files themselves live in
-- data/models/<id>/ (model.onnx + classes.json + the uploaded source).
--
-- status: pending | converting | ready | error  (see app/store.py:ModelStore)
-- builtin: 1 for the model imported from the pre-existing data/model/best.onnx.
--          It can never be deleted, so there is always something to fall back to.
-- classes: JSON object {"0": "bird", ...} — the class-name sidecar, inlined here
--          so the model list can show it without touching the filesystem.
CREATE TABLE IF NOT EXISTS models (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'pt',
    imgsz       INTEGER NOT NULL DEFAULT 640,
    classes     TEXT NOT NULL DEFAULT '{}',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    builtin     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
