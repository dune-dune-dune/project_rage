"""Gunicorn config: single source of truth for bind / workers / threads.

Self-contained .env loading so a native run
``gunicorn -c gunicorn.conf.py app.wsgi:app`` picks up the same configuration as
the Docker ``env_file``. Kept free of app imports so it always loads.
"""

from __future__ import annotations

import os
from pathlib import Path

_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env, override=False)
    except ModuleNotFoundError:
        pass

bind = os.environ.get("WEB_BIND", "0.0.0.0:8000")

# MUST stay 1: the single worker is the sole owner of the UDP channel and the
# command sequence counter. Running more corrupts the command stream.
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
loglevel = os.environ.get("LOG_LEVEL", "info")
