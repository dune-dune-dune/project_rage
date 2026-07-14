"""/api/models/* + /assets/models/* — upload, switch, rename, delete.

The .pt path hands off to the exporter sidecar, which these tests never talk to:
ModelJobs.submit is stubbed, so what is asserted is the cockpit's half of the
contract (the row is registered, the file lands in the model directory, the
response is 202 and the model is NOT servable yet).
"""

from __future__ import annotations

import io

import pytest

from app.store import STATUS_PENDING, STATUS_READY


@pytest.fixture
def client(app_factory, tmp_path, monkeypatch):
    """Authed client whose data dir (DB + model files) is the tmp dir."""
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path))
    c = app_factory().test_client()
    with c.session_transaction() as sess:
        sess["authed"] = True
    return c


@pytest.fixture(autouse=True)
def no_exporter_calls(monkeypatch):
    """Never reach for the sidecar in tests; record what would have been sent."""
    submitted: list[tuple] = []
    monkeypatch.setattr(
        "app.model_jobs.ModelJobs.submit",
        lambda self, model_id, source, imgsz: submitted.append((model_id, source, imgsz)),
    )
    monkeypatch.setattr("app.model_jobs.ModelJobs.exporter_online", lambda self: True)
    return submitted


def _upload(client, filename: str, content: bytes = b"weights", name: str = "drone-v3", **extra):
    data = {"file": (io.BytesIO(content), filename), "name": name}
    data.update(extra)
    return client.post("/api/models", data=data, content_type="multipart/form-data")


def test_uploading_a_pt_registers_it_and_queues_the_conversion(client, no_exporter_calls):
    resp = _upload(client, "best.pt")
    assert resp.status_code == 202

    model = resp.get_json()
    assert model["status"] == STATUS_PENDING
    assert model["source"] == "pt"
    assert model["name"] == "drone-v3"
    # Handed to the exporter, not converted here.
    assert no_exporter_calls == [(model["id"], "source.pt", model["imgsz"])]
    # ...and not servable until it comes back ready.
    assert client.get(model["url"]).status_code == 404


def test_uploading_an_onnx_is_ready_immediately(client, no_exporter_calls):
    resp = _upload(client, "best.onnx", content=b"onnx-bytes", name="ready-model")
    assert resp.status_code == 201

    model = resp.get_json()
    assert model["status"] == STATUS_READY
    assert model["size_bytes"] == len(b"onnx-bytes")
    assert no_exporter_calls == []  # the escape hatch needs no exporter at all

    served = client.get(model["url"])
    assert served.status_code == 200
    assert served.data == b"onnx-bytes"
    assert client.get(model["classes_url"]).get_json() == {}


def test_an_onnx_can_carry_its_class_names(client):
    resp = _upload(
        client,
        "best.onnx",
        classes=(io.BytesIO(b'{"0": "drone"}'), "classes.json"),
    )
    model = resp.get_json()
    assert model["classes"] == {"0": "drone"}
    assert client.get(model["classes_url"]).get_json() == {"0": "drone"}


def test_other_file_types_are_rejected(client):
    assert _upload(client, "weights.bin").status_code == 400
    assert _upload(client, "").status_code == 400


def test_upload_is_capped(client, monkeypatch):
    client.application.config["MAX_CONTENT_LENGTH"] = 32
    resp = _upload(client, "best.onnx", content=b"x" * 1024)
    assert resp.status_code == 413


def test_activate_switches_the_served_model(client):
    first = _upload(client, "a.onnx", content=b"aaa", name="перша").get_json()
    second = _upload(client, "b.onnx", content=b"bbb", name="друга").get_json()

    assert client.post(f"/api/models/{second['id']}/activate").status_code == 200
    listing = client.get("/api/models").get_json()
    assert listing["active"] == second["id"]
    assert {m["name"] for m in listing["models"]} == {"перша", "друга"}

    # The back-compat asset URL follows the active model.
    redirect = client.get("/assets/model.onnx")
    assert redirect.status_code == 302
    assert second["id"] in redirect.headers["Location"]
    assert client.get("/assets/model.onnx", follow_redirects=True).data == b"bbb"
    assert first["id"] not in redirect.headers["Location"]


def test_a_pending_model_cannot_be_activated(client):
    pending = _upload(client, "best.pt").get_json()
    assert client.post(f"/api/models/{pending['id']}/activate").status_code == 400


def test_rename_and_delete(client):
    keeper = _upload(client, "a.onnx", content=b"aaa", name="активна").get_json()
    doomed = _upload(client, "b.onnx", content=b"bbb", name="зайва").get_json()
    client.post(f"/api/models/{keeper['id']}/activate")

    renamed = client.post(f"/api/models/{doomed['id']}/rename", json={"name": "перейменована"})
    assert renamed.get_json()["name"] == "перейменована"

    assert client.delete(f"/api/models/{doomed['id']}").status_code == 204
    assert client.get(doomed["url"]).status_code == 404
    # The active model is protected, with a reason the panel can show.
    refused = client.delete(f"/api/models/{keeper['id']}")
    assert refused.status_code == 400
    assert refused.get_json()["error"]


def test_index_injects_the_active_model(client):
    model = _upload(client, "best.onnx", content=b"aaa").get_json()
    client.post(f"/api/models/{model['id']}/activate")
    html = client.get("/").get_data(as_text=True)
    assert "window.__AI__" in html
    assert model["id"] in html


def test_routes_require_auth(app_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path))
    anon = app_factory().test_client()  # default PIN set → gate active
    assert anon.get("/api/models").status_code == 401
    assert anon.post("/api/models").status_code == 401
    assert anon.get("/assets/models/abc123abc123/model.onnx").status_code == 401
