"""Background conversion of uploaded YOLO weights (.pt -> ONNX).

The conversion itself needs ultralytics + torch, which this process deliberately
does NOT have: one Gunicorn worker also runs the 20 Hz turret loop (400 ms
deadman), and a CPU-pegged torch export inside it risks the arbiter killing the
worker — taking turret control down with it. So the export lives in the
``exporter`` sidecar container (services/exporter) and this module only:

  1. runs one daemon thread per job (the same pattern as TurretController),
  2. POSTs the model directory to the exporter and waits on the socket (idle,
     no CPU),
  3. writes the outcome back into the model row.

Uploading a ready-made .onnx skips all of this — see routes.api_models_upload.
That is the escape hatch when the exporter is down or was never built.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from .store import STATUS_CONVERTING, STATUS_ERROR, STATUS_READY, ModelStore

log = logging.getLogger("cockpit.models")

# A YOLO export on a loaded Jetson CPU is minutes, not seconds. The socket sits
# idle meanwhile, so a long ceiling costs nothing here; it only bounds a hung
# exporter. The exporter's own Gunicorn timeout is the real limit.
_CONVERT_TIMEOUT_SECONDS = 1200
_HEALTH_TIMEOUT_SECONDS = 2


class ModelJobs:
    """Runs .pt -> ONNX conversions against the exporter sidecar."""

    def __init__(self, store: ModelStore, exporter_url: str, container_data_dir: str) -> None:
        self._store = store
        self._url = exporter_url
        # The exporter sees the same ./data bind mount under a path of its own, so
        # the cockpit cannot just hand it a local path.
        self._container_data_dir = container_data_dir.rstrip("/")

    def exporter_online(self) -> bool:
        """Whether the sidecar answers — drives the panel's converter indicator."""
        try:
            with urllib.request.urlopen(f"{self._url}/healthz", timeout=_HEALTH_TIMEOUT_SECONDS):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def submit(self, model_id: str, source_name: str, imgsz: int) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(model_id, source_name, imgsz),
            name=f"model-export-{model_id}",
            daemon=True,
        )
        thread.start()

    def _run(self, model_id: str, source_name: str, imgsz: int) -> None:
        self._store.set_status(model_id, STATUS_CONVERTING)
        payload = json.dumps({
            "dir": f"{self._container_data_dir}/models/{model_id}",
            "source": source_name,
            "imgsz": imgsz,
        }).encode()
        request = urllib.request.Request(
            f"{self._url}/convert",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_CONVERT_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read().decode())
        except urllib.error.HTTPError as err:
            self._fail(model_id, _http_error_message(err))
            return
        except (urllib.error.URLError, OSError):
            self._fail(model_id, "Конвертер недоступний — завантажте готовий .onnx")
            return
        except ValueError:
            self._fail(model_id, "Конвертер повернув некоректну відповідь")
            return

        if not result.get("ok"):
            self._fail(model_id, str(result.get("error") or "Конвертація не вдалася"))
            return
        self._store.set_status(
            model_id,
            STATUS_READY,
            imgsz=result.get("imgsz"),
            classes=result.get("classes"),
            size_bytes=result.get("size_bytes"),
        )
        log.info("model %s converted", model_id)

    def _fail(self, model_id: str, message: str) -> None:
        log.warning("model %s conversion failed: %s", model_id, message)
        self._store.set_status(model_id, STATUS_ERROR, error=message)


def _http_error_message(err: urllib.error.HTTPError) -> str:
    """The exporter's own error text, when it sent one."""
    try:
        body = json.loads(err.read().decode())
        message = body.get("error")
        if isinstance(message, str) and message:
            return message
    except (ValueError, OSError):
        pass
    return f"Конвертер повернув помилку HTTP {err.code}"
