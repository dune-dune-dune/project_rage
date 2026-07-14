#!/usr/bin/env bash
# Vendor ONNX Runtime Web into app/static/vendor/ so the cockpit runs YOLO
# inference in the browser with NO external CDN at runtime (field networks are
# often offline). Run this once on a machine with internet access; the fetched
# files are then served locally by Flask from /static/vendor/.
#
#   bash services/web/scripts/fetch_ort.sh
#
# VERSION MATTERS. ORT 1.17 called `adapter.requestAdapterInfo()`, which has since
# been REMOVED from the WebGPU spec (it is the `adapter.info` property now), so on
# a current browser its WebGPU backend dies with
#   "no available backend found. ERR: [webgpu] TypeError: r.requestAdapterInfo is not a function"
# and inference silently drops to the (≈20x slower) CPU. Do not pin this back.
set -euo pipefail

ORT_VERSION="${ORT_VERSION:-1.22.0}"
BASE="https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="${HERE}/../app/static/vendor"
mkdir -p "${VENDOR}"

# Two files, both loaded by ai-worker.js (a MODULE worker — since 1.18 the WebGPU
# build is an ES module, not a classic importScripts bundle):
#
#   ort.webgpu.bundle.min.mjs        WebGPU **and** WASM execution providers in one
#                                    module.
#   ort-wasm-simd-threaded.jsep.wasm the WASM binary. It backs BOTH providers —
#                                    WebGPU (JSEP) is built on top of it — so if this
#                                    file cannot load, *neither* backend comes up.
#   ort-wasm-simd-threaded.jsep.mjs  its loader glue. Fetch it even though the bundle
#                                    inlines a copy: some resolution paths still import
#                                    it by name, and a 404 here fails initWasm() and
#                                    thus the whole engine.
#
# The worker resolves all of these RELATIVE TO ITSELF (import.meta.url), so they must
# sit next to ort.webgpu.bundle.min.mjs in this directory. It deliberately does NOT set
# ort.env.wasm.wasmPaths — a string prefix there sends ORT down a path that fetches the
# .mjs by hand and is easy to get subtly wrong.
#
# NOTE: WebGPU needs a SECURE CONTEXT — navigator.gpu does not exist on a plain
# http:// LAN origin, so the worker silently falls back to WASM and says so in the
# ⚙ panel. Serve over HTTPS/localhost, or allow the origin in the browser, to get it.
FILES=(
  "ort.webgpu.bundle.min.mjs"
  "ort-wasm-simd-threaded.jsep.wasm"
  "ort-wasm-simd-threaded.jsep.mjs"
)

for f in "${FILES[@]}"; do
  echo "Fetching ${f} ..."
  curl -fsSL "${BASE}/${f}" -o "${VENDOR}/${f}"
done

# Files from the pre-1.18 layout (classic-script bundle + non-jsep wasm). Nothing
# loads them any more; leaving ~50 MB of dead weight in the image would be worse
# than removing them.
STALE=(
  "ort.min.js"
  "ort.webgpu.min.js"
  "ort-wasm.wasm"
  "ort-wasm-simd.wasm"
  "ort-wasm-threaded.wasm"
  "ort-wasm-simd-threaded.wasm"
  "ort-wasm-simd.jsep.wasm"
)
for f in "${STALE[@]}"; do
  if [ -e "${VENDOR}/${f}" ]; then
    echo "Removing stale ${f} ..."
    rm -f "${VENDOR}/${f}"
  fi
done

echo "Done. onnxruntime-web ${ORT_VERSION} vendored into ${VENDOR}"
