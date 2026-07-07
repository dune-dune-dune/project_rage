#!/usr/bin/env bash
# Run the cockpit natively on the host (required on macOS, where Docker Desktop
# has no host networking so a container cannot bind the RWS source IP).
#
# The host must own RWS_SRC_IP (default 192.168.88.33) before live control works;
# the sender retries the bind automatically until the address appears.
#
# Usage:  cd services/web && cp .env.example .env && ./run-native.sh
set -euo pipefail
cd "$(dirname "$0")"

# tomllib needs Python 3.11+.
PY=""
for c in python3.13 python3.12 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "error: need Python 3.11+ (for stdlib tomllib); none of python3.11/3.12/3.13 found" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating venv with $PY ..."
  "$PY" -m venv .venv
fi
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "warning: no .env found — copy .env.example to .env first. Using defaults (dry-run)." >&2
fi

exec ./.venv/bin/gunicorn -c gunicorn.conf.py app.wsgi:app
