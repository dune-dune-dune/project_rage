"""ModelStore: the AI model library (rows in `models` + the active-model key).

The store is the only thing standing between an uploaded filename and the
filesystem, and it owns the two invariants that keep AI mode from breaking:
there is always a model to fall back to, and the active one is always ready.
"""

from __future__ import annotations

from app.db import SettingsDb
from app.store import (
    SOURCE_ONNX,
    SOURCE_PT,
    STATUS_CONVERTING,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_READY,
    ModelStore,
)


def _store(tmp_path, default_imgsz: int = 640) -> ModelStore:
    db = SettingsDb(str(tmp_path / "cockpit.db"))
    db.migrate()
    return ModelStore(db, str(tmp_path / "models"), default_imgsz)


def _ready(store: ModelStore, name: str = "нова", source: str = SOURCE_PT) -> dict:
    model = store.create(name, source)
    return store.set_status(model["id"], STATUS_READY, imgsz=640, classes={"0": "drone"}, size_bytes=10)


def test_create_registers_a_pending_model_and_its_directory(tmp_path):
    store = _store(tmp_path)
    model = store.create("drone-v3", SOURCE_PT)

    assert model["status"] == STATUS_PENDING
    assert model["source"] == SOURCE_PT
    assert model["imgsz"] == 640  # the default until the exporter reports the real one
    assert store.dir_for(model["id"]).is_dir()
    assert store.list() == [model]


def test_empty_library_has_no_active_model(tmp_path):
    assert _store(tmp_path).active() is None


def test_a_pending_model_can_never_become_active(tmp_path):
    store = _store(tmp_path)
    model = store.create("ще конвертується", SOURCE_PT)

    assert store.set_active(model["id"]) is None
    assert store.active() is None  # nothing ready -> nothing to serve


def test_set_status_ready_records_the_exporter_result(tmp_path):
    store = _store(tmp_path)
    model = store.create("drone-v3", SOURCE_PT)

    updated = store.set_status(
        model["id"], STATUS_READY, imgsz=960, classes={"0": "bird", "1": "drone"}, size_bytes=4096
    )
    assert updated["status"] == STATUS_READY
    assert updated["imgsz"] == 960
    assert updated["classes"] == {"0": "bird", "1": "drone"}
    assert updated["size_bytes"] == 4096
    assert updated["error"] == ""


def test_active_falls_back_to_the_builtin_when_the_stored_id_is_gone(tmp_path):
    store = _store(tmp_path)
    builtin = store.create("Базова модель", SOURCE_ONNX, builtin=True)
    store.set_status(builtin["id"], STATUS_READY)
    other = _ready(store, "drone-v3")
    store.set_active(other["id"])

    # Simulate the active model vanishing from under the setting.
    assert store.delete(builtin["id"]) == (False, "Базову модель видалити не можна")
    store.set_active(builtin["id"])
    ok, _ = store.delete(other["id"])
    assert ok

    assert store.active()["id"] == builtin["id"]


def test_active_ignores_a_stored_id_that_is_not_ready(tmp_path):
    store = _store(tmp_path)
    good = _ready(store, "готова")
    store.set_active(good["id"])
    broken = store.create("зламана", SOURCE_PT)
    store.set_status(broken["id"], STATUS_ERROR, error="конвертер помер")

    assert store.active()["id"] == good["id"]


def test_delete_refuses_the_builtin_and_the_active_model(tmp_path):
    store = _store(tmp_path)
    builtin = store.create("Базова модель", SOURCE_ONNX, builtin=True)
    store.set_status(builtin["id"], STATUS_READY)
    active = _ready(store, "drone-v3")
    store.set_active(active["id"])

    assert store.delete(builtin["id"])[0] is False
    assert store.delete(active["id"])[0] is False
    assert len(store.list()) == 2


def test_delete_removes_the_row_and_the_files(tmp_path):
    store = _store(tmp_path)
    keeper = _ready(store, "лишається")
    store.set_active(keeper["id"])
    doomed = _ready(store, "зайва")
    directory = store.dir_for(doomed["id"])

    ok, reason = store.delete(doomed["id"])
    assert ok and reason == ""
    assert store.get(doomed["id"]) is None
    assert not directory.exists()


def test_rename_keeps_the_old_name_when_the_new_one_is_empty(tmp_path):
    store = _store(tmp_path)
    model = _ready(store, "оригінал")

    assert store.rename(model["id"], "  перейменована  ")["name"] == "перейменована"
    assert store.rename(model["id"], "   ")["name"] == "перейменована"  # blank -> unchanged


def test_the_name_is_sanitised_but_keeps_cyrillic(tmp_path):
    store = _store(tmp_path)
    # The name is rendered into the panel; the id (not the name) is what reaches
    # the filesystem, so this only has to be safe as text.
    model = store.create("дрон\x00 <v3>", SOURCE_PT)
    assert model["name"] == "дрон v3"

    long_name = store.create("я" * 100, SOURCE_PT)
    assert len(long_name["name"]) == 48

    unnamed = store.create("   ", SOURCE_PT)
    assert unnamed["name"] == "модель"  # never blank in the list


def test_unknown_or_traversing_ids_resolve_to_nothing(tmp_path):
    store = _store(tmp_path)
    # The id is the only request-controlled part of a filesystem path.
    assert store.dir_for("../../etc") is None
    assert store.file_for("../../etc", "model.onnx") is None
    assert store.get("../../etc") is None
    # And only the two known filenames are servable.
    model = _ready(store)
    assert store.file_for(model["id"], "source.pt") is None
    assert store.file_for(model["id"], "model.onnx") is not None


def test_fail_interrupted_resets_conversions_a_restart_killed(tmp_path):
    store = _store(tmp_path)
    stuck = store.create("перервана", SOURCE_PT)
    store.set_status(stuck["id"], STATUS_CONVERTING)
    done = _ready(store, "ціла")

    assert store.fail_interrupted() == 1
    assert store.get(stuck["id"])["status"] == STATUS_ERROR
    assert store.get(stuck["id"])["error"]
    assert store.get(done["id"])["status"] == STATUS_READY  # untouched
