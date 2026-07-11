#!/usr/bin/env bash
# Vendor Leaflet into app/static/vendor/leaflet/ so the cockpit map widget loads
# its JS/CSS locally (no external CDN for the library itself). Map *tiles* are
# still fetched from OpenStreetMap at runtime, so the operator's browser needs
# internet for imagery — but the app boots even if only tiles are unreachable.
# Run this once on a machine with internet access.
#
#   bash services/web/scripts/fetch_leaflet.sh
set -euo pipefail

LEAFLET_VERSION="${LEAFLET_VERSION:-1.9.4}"
BASE="https://cdn.jsdelivr.net/npm/leaflet@${LEAFLET_VERSION}/dist"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="${HERE}/../app/static/vendor/leaflet"
mkdir -p "${VENDOR}/images"

# Core loader + stylesheet. leaflet.css references marker/layers PNGs by relative
# path (images/…), so they must sit alongside under images/.
FILES=(
  "leaflet.js"
  "leaflet.css"
)
IMAGES=(
  "marker-icon.png"
  "marker-icon-2x.png"
  "marker-shadow.png"
  "layers.png"
  "layers-2x.png"
)

for f in "${FILES[@]}"; do
  echo "Fetching ${f} ..."
  curl -fsSL "${BASE}/${f}" -o "${VENDOR}/${f}"
done

for f in "${IMAGES[@]}"; do
  echo "Fetching images/${f} ..."
  curl -fsSL "${BASE}/images/${f}" -o "${VENDOR}/images/${f}"
done

echo "Done. Leaflet ${LEAFLET_VERSION} vendored into ${VENDOR}"
