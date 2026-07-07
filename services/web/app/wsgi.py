"""Gunicorn entry point: ``gunicorn app.wsgi:app``."""

from __future__ import annotations

from . import create_app

app = create_app()
