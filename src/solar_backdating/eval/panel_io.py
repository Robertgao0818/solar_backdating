"""Banked-panel loaders that normalise the on-disk schema into the seam triple.

All loaders are pure stdlib (``csv``/``json``/``datetime``); pandas is confined
to the optional end-to-end TVD loaders at the bottom. NEVER modify the banked
data under ``~/zasolar_data/geid_temporal/`` — these functions are read-only.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from solar_backdating.estimators import VintageObservation

# Default chip->stratum join table (this sample generation stores CHIP ids in
# the file's ``anchor_id`` column; see load_strata).
DEFAULT_SAMPLE_ANCHORS = (
    Path.home()
    / "zasolar_data/geid_temporal/mini_reliability_20260624/sample/sample_anchors.csv"
)

# Population inventory weights = population_status_counts / 15859. Identical in
# both ``llm_reliability_20260622`` and ``mini_reliability_20260624``
# ``sample_manifest.json``; verified to reproduce summary.json's weighted
# sustained ~0.7975 and sustained-year ~0.4692 (see tests/estimator).
INVENTORY_WEIGHT_FALLBACK: dict[str, float] = {
    "done_appears": 0.6891985623305379,
    "done_ambiguous_no_recent_anchor": 0.1639447632259285,
    "done_ambiguous_nonmonotonic": 0.12970552998297497,
    "done_installed_during_census": 0.011476133425814994,
    "done_already_present_before_geid_history": 0.004161674758812031,
    "done_ambiguous_gemini_failed": 0.0015133362759316476,
}

Unit = tuple[str, str, str]


def parse_iso_date(raw: str) -> date | None:
    """Parse the first 10 chars as ``%Y-%m-%d``; ``None`` on empty/nan sentinels."""
    s = (raw or "").strip()
    if s in ("", "nan", "None"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _conf(raw: str | None) -> float | None:
    s = (raw or "").strip()
    if s in ("", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_panel(
    long_csv: Path,
) -> tuple[dict[Unit, dict[str, list[VintageObservation]]], dict[Unit, str]]:
    """Load ``long_all.csv`` into ``unit -> rep -> ordered [VintageObservation]``.

    Replicates ``load_long``: within a (unit, rep) the raw ``capture_date`` string
    keys a dict so exact-duplicate dates are deduplicated last-wins before parsing.
    Returns ``(panel, chip_of)`` where ``chip_of[unit] = chip_id``.
    """
    # unit -> rep -> {capture_date_str: (row, csv_line_no)}  (last-wins overwrite)
    raw: dict[Unit, dict[str, dict[str, tuple[dict, int]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    chip_of: dict[Unit, str] = {}
    with Path(long_csv).open(newline="") as fh:
        reader = csv.DictReader(fh)
        for lineno, r in enumerate(reader, start=2):  # header is line 1
            unit = (r["chip_id"], r["anchor_id"], r["target_label"])
            chip_of[unit] = r["chip_id"]
            raw[unit][r["rep"]][r["capture_date"]] = (r, lineno)

    panel: dict[Unit, dict[str, list[VintageObservation]]] = {}
    for unit, reps in raw.items():
        panel[unit] = {}
        for rep, datemap in reps.items():
            obs: list[VintageObservation] = []
            for dstr, (row, lineno) in datemap.items():
                d = parse_iso_date(dstr)
                if d is None:
                    continue
                obs.append(
                    VintageObservation(
                        capture_date=d,
                        pv_present=row["pv_present"],
                        confidence=_conf(row.get("sequence_confidence")),
                        quality_flag=row.get("quality_flag", "usable") or "usable",
                        source_row=lineno,
                    )
                )
            obs.sort(key=lambda o: (o.capture_date, o.source_row or 0))
            panel[unit][rep] = obs
    return panel, chip_of


def load_strata(sample_anchors_csv: Path | None = None) -> dict[str, str]:
    """chip_id -> status_stratum.

    Keyed on the file's ``anchor_id`` column, which (this sample generation)
    actually holds CHIP-level ids, so ``strata.get(chip_of[unit])`` resolves the
    join exactly as the reference does. Falls back to the ``status_stratum``
    column present in either schema.
    """
    path = Path(sample_anchors_csv) if sample_anchors_csv else DEFAULT_SAMPLE_ANCHORS
    out: dict[str, str] = {}
    with Path(path).open(newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["anchor_id"]] = r["status_stratum"]
    return out


def load_inventory_weights(sample_manifest_json: Path | None) -> dict[str, float]:
    """Read ``sample_manifest.json['inventory_weight']``; fallback constant if absent."""
    if sample_manifest_json is None:
        return dict(INVENTORY_WEIGHT_FALLBACK)
    path = Path(sample_manifest_json)
    if not path.exists():
        return dict(INVENTORY_WEIGHT_FALLBACK)
    weights = json.loads(path.read_text()).get("inventory_weight")
    if not weights:
        return dict(INVENTORY_WEIGHT_FALLBACK)
    return {k: float(v) for k, v in weights.items()}


# --------------------------------------------------------------------------- #
# Optional end-to-end (3-rep TVD) loaders. pandas confined here.
# --------------------------------------------------------------------------- #
def _s(v) -> str:
    s = str(v).strip()
    return "" if s in ("nan", "None") else s


def _norm_bound(v) -> str:
    s = str(v).strip()
    if s in ("", "nan", "None"):
        return "0"
    try:
        return str(int(float(s)))
    except ValueError:
        return "0"


def agree_key_raw(row: dict) -> str:
    """Mirror ``llm_endtoend_analyze.agree_key_raw`` on a raw delivery row."""
    if _s(row.get("undated_reason")):
        return "UNDATED"
    if not _s(row.get("date_provider")):
        return "UNDATED"
    if _norm_bound(row.get("date_is_bound")) == "1":
        return f"AP_BOUND<=|{_s(row.get('install_interval_end'))}"
    return f"INTERVAL|{_s(row.get('install_interval_start'))}|{_s(row.get('install_interval_end'))}"


def install_year(row: dict) -> str | None:
    """4-digit year off install_date / install_interval_end / earliest_present_date."""
    for col in ("install_date", "install_interval_end", "earliest_present_date"):
        v = _s(row.get(col))
        if len(v) >= 4 and v[:4].isdigit():
            return v[:4]
    return None


def load_endtoend_reference(reference_csv: Path):
    """Load the 642-row end-to-end reference (carries status_stratum, inv_weight)."""
    import pandas as pd

    ref = pd.read_csv(reference_csv, dtype=str)
    ref["source_feature_id"] = ref["source_feature_id"].astype(int)
    ref["inv_weight"] = ref["inv_weight"].astype(float)
    return ref


def load_endtoend_delivery(delivery_csv: Path):
    """Load a per-rep ``delivery.csv`` as a string-typed DataFrame."""
    import pandas as pd

    d = pd.read_csv(delivery_csv, dtype=str)
    d["source_feature_id"] = d["source_feature_id"].astype(int)
    return d
