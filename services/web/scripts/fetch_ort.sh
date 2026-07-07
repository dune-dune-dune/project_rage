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

# The loader plus the WASM backends. wasmPaths in ai.js points at this dir, and
# single-threaded SIMD (no COOP/COEP needed) loads ort-wasm-simd.wasm.
FILES=(
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
