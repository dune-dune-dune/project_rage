# exporter — YOLO `.pt` → ONNX for the cockpit

A one-endpoint HTTP service that the web cockpit calls when the operator uploads
new AI weights from ⚙ → «Налаштування ШІ моделі».

## Why it is a separate container

It is the only component that needs `ultralytics` + `torch` (~2–3 GB). Putting
them in the cockpit image would mean:

* a torch export running inside the process that owns the **20 Hz turret command
  loop** (400 ms deadman). Gunicorn's arbiter kills a worker that stops
  heart-beating; a CPU-pegged export could therefore take turret control down.
* the cockpit image growing from ~200 MB to ~3 GB on the Jetson.

Here, a slow, crashing or OOM-killed export costs nothing but a failed job. The
container is capped at one core with the lowest CPU priority
(`cpus`/`cpu_shares` in `services/web/docker-compose.yml`) so it cannot starve
the control loop either.

## Contract

Both containers bind-mount the same `services/web/data` directory — the cockpit
at `/app/data`, the exporter at `/data`. The cockpit writes the uploaded
checkpoint to `data/models/<id>/source.pt` and POSTs the directory; the exporter
writes `model.onnx` + `classes.json` back into it.

```
POST /convert  {"dir": "/data/models/<id>", "source": "source.pt", "imgsz": 640}
            -> {"ok": true, "imgsz": 640, "classes": {"0": "bird"}, "size_bytes": 39812345}
            -> {"ok": false, "error": "…"}   (4xx/5xx)
GET  /healthz  -> {"status": "ok"}
```

`dir` must resolve inside `EXPORTER_DATA_DIR` (default `/data`) — the request
comes from an authenticated cockpit, but a path it can steer is still a path this
process would read and overwrite.

Published on `127.0.0.1:8901` only: it has no authentication of its own and must
not be reachable from the turret LAN.

## If it is down

Uploading a ready-made `.onnx` from the cockpit needs no exporter at all — the
file is served as-is. That is the recovery hatch (export it yourself with
`services/web/scripts/export_onnx.py`, which is the same code path, offline).
