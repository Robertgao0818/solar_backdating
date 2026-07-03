"""Unit tests for the banked-panel loaders."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from solar_backdating.eval.panel_io import (
    INVENTORY_WEIGHT_FALLBACK,
    load_inventory_weights,
    load_panel,
    load_strata,
    parse_iso_date,
)

DATA = Path(__file__).parent / "data" / "golden_mini_long.csv"
PANEL = Path(
    os.environ.get(
        "FULLSTACK_PANEL_DIR",
        Path.home() / "zasolar_data/geid_temporal/fullstack_noscan_20260630",
    )
)
SAMPLE = (
    Path.home()
    / "zasolar_data/geid_temporal/mini_reliability_20260624/sample/sample_anchors.csv"
)


def test_parse_iso_date():
    assert parse_iso_date("2021-06-30T00:00:00") == date(2021, 6, 30)
    assert parse_iso_date("") is None
    assert parse_iso_date("nan") is None
    assert parse_iso_date("garbage") is None


def test_load_panel_golden_dedup_and_sort():
    panel, chip_of = load_panel(DATA)
    unitA = ("chipA", "anchorA", "T01")
    assert chip_of[unitA] == "chipA"
    obs = panel[unitA]["1"]
    # sorted ascending by capture_date
    dates = [o.capture_date for o in obs]
    assert dates == sorted(dates)
    # chipD abstain rows preserved with pv_present '' and confidence None
    obsD = panel[("chipD", "anchorD", "T01")]["1"]
    abstains = [o for o in obsD if o.pv_present == ""]
    assert abstains and all(o.confidence is None for o in abstains)


def test_load_panel_last_wins_on_duplicate_date(tmp_path):
    csv_path = tmp_path / "dup.csv"
    csv_path.write_text(
        "rep,chip_id,anchor_id,target_label,capture_date,pv_present,quality_flag,"
        "sequence_confidence\n"
        "1,c,a,T01,2020-01-01,0,usable,0.9\n"
        "1,c,a,T01,2020-01-01,1,usable,0.8\n"  # same date -> last wins
    )
    panel, _ = load_panel(csv_path)
    obs = panel[("c", "a", "T01")]["1"]
    assert len(obs) == 1
    assert obs[0].pv_present == "1"  # last row won
    assert obs[0].confidence == 0.8


def test_load_inventory_weights_fallback():
    assert load_inventory_weights(None) == INVENTORY_WEIGHT_FALLBACK
    assert load_inventory_weights(Path("/does/not/exist.json")) == INVENTORY_WEIGHT_FALLBACK


@pytest.mark.skipif(
    not (PANEL / "long_all.csv").exists() or not SAMPLE.exists(),
    reason="banked panel not present",
)
def test_load_panel_banked_shape():
    panel, chip_of = load_panel(PANEL / "long_all.csv")
    assert len(panel) == 28
    # every unit has 10 reps
    assert all(len(reps) == 10 for reps in panel.values())
    strata = load_strata(SAMPLE)
    # chip-keyed join resolves for every unit (collision replicated)
    assert all(chip_of[u] in strata for u in panel)
