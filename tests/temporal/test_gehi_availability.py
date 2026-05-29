#!/usr/bin/env python3
"""Tests for gehi_availability resilience."""

from __future__ import annotations

import subprocess

from scripts.temporal.gehi_availability import fetch_availability_for_anchor


def _anchor():
    return {
        "anchor_id": "a000001",
        "region_key": "jhb",
        "grid_id": "G0816",
        "centroid_lat": -26.2,
        "centroid_lon": 28.0,
        "chip_lat_min": -26.21,
        "chip_lat_max": -26.19,
        "chip_lon_min": 27.99,
        "chip_lon_max": 28.01,
    }


def test_availability_timeout_does_not_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows = fetch_availability_for_anchor(_anchor(), runner=_boom)
    # Degraded gracefully: no exception, empty availability for the hung anchor.
    assert rows == []


def test_availability_broad_exception_does_not_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("gehi exploded")

    rows = fetch_availability_for_anchor(_anchor(), runner=_boom)
    assert rows == []
