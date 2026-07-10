"""Command-stream cadence: the loop must transmit every 50 ms (20 Hz) or faster."""

from __future__ import annotations

import dataclasses

from app.config import load_settings


def test_period_is_50ms_at_20hz():
    settings = load_settings()
    assert settings.send_rate_hz == 20
    assert settings.period_seconds == 0.05  # 50 ms — the hard requirement


def test_period_scales_with_rate():
    faster = dataclasses.replace(load_settings(), send_rate_hz=40)
    assert faster.period_seconds == 0.025  # never slower than 50 ms
