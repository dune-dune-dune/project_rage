#!/usr/bin/env bash
# Vendor ONNX Runtime Web into app/static/vendor/ so the cockpit runs YOLO
# inference in the browser with NO external CDN at runtime (field networks are
# often offline). Run this once on a machine with internet access; the fetched
# files are then served locally by Flask from /static/vendor/.
#
#   bash services/web/scripts/fetch_ort.sh
set -euo pipefail

ORT_VERSION="${ORT_VERSION:-1.17.3}"
BASE="https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="${HERE}/../app/static/vendor"
mkdir -p "${VENDOR}"

# The loaders plus the backends. wasmPaths in ai-worker.js points at this dir.
#
#   ort.webgpu.min.js       the JSEP build: WebGPU **and** WASM in one bundle. This
#                           is what ai-worker.js loads first — WebGPU runs YOLO on
#                           the OPERATOR's GPU (tens of FPS instead of ~2 on WASM).
#   ort-wasm-simd.jsep.wasm the WASM binary that bundle needs (single-threaded SIMD).
#   ort.min.js + ort-wasm*  the plain WASM build, kept as the fallback the worker
#                           loads if the JSEP files are absent.
#
# NOTE: WebGPU needs a SECURE CONTEXT — navigator.gpu does not exist on a plain
# http:// LAN origin, so the worker silently falls back to WASM and says so in the
# ⚙ panel. Serve over HTTPS/localhost, or allow the origin in the browser, to get it.
FILES=(
  "ort.webgpu.min.js"
  "ort-wasm-simd.jsep.wasm"
  "ort.min.js"
  "ort-wasm.wasm"
  "ort-wasm-simd.wasm"
  "ort-wasm-threaded.wasm"
  "ort-wasm-simd-threaded.wasm"
)

for f in "${FILES[@]}"; do
  echo "Fetching ${f} ..."
  curl -fsSL "${BASE}/${f}" -o "${VENDOR}/${f}"
done

echo "Done. onnxruntime-web ${ORT_VERSION} vendored into ${VENDOR}"
