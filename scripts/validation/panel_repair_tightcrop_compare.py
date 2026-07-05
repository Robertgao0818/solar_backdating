#!/usr/bin/env python3
"""ISSUE-04 panel repair: banked (60 m crop) vs tight-crop (12 m / 256 px) compare.

Tests the "under-resolution hypothesis" from
``docs/replan_v2/ISSUE-04-reliability-panel-repair.md``: the
``done_ambiguous_gemini_failed`` stratum (8 units spanning chips
``c0009952`` and ``c0010157``) was scored in the banked full-stack no-search
run (``fullstack_noscan_20260630``) at a 60 m crop / 128 px floor. We
re-scored the SAME frozen vintage stacks for those units at a tight 12 m
crop / 256 px floor. If the tight-crop rescore dates cleanly (higher
mode-hit, fewer undated flips, fewer abstains) where the banked run did not,
the stratum's noise is an imaging-resolution artifact rather than scorer
give-up -- with direct implications for the decoder emission model and the
student chip-rendering spec. If tight-crop is no better, the ambiguity is
intrinsic to the scene (occlusion, roof clutter, etc.), not resolution.

Per-unit and aggregate comparison of the two ``long_all.csv`` runs, reusing
the frozen estimator logic (``derive_install`` / ``_mode_hit``) from
``fullstack_noscan_analyze.py`` so numbers are directly comparable to the
banked full-stack table.

Outputs (under --out-dir):
  per_unit_compare.csv  one row per unit, banked/tight metrics + deltas
  summary.json          per-source means, deltas, counts, dates_changed
  table.md               compact markdown comparison tables
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.fullstack_noscan_analyze import derive_install, _mode_hit  # noqa: E402

DELTA_METRICS = [
    "interval_mode_hit",
    "sustained_mode_hit",
    "year_mode_hit",
    "sustained_year_mode_hit",
    "fpd_undated_flip_rate",
    "sustained_undated_flip_rate",
    "abstain_rate",
]

UNIT_METRICS = [
    "n_reps",
    "interval_mode_hit",
    "sustained_mode_hit",
    "year_mode_hit",
    "sustained_year_mode_hit",
    "fpd_undated_flip_rate",
    "sustained_undated_flip_rate",
    "modal_fpd",
    "modal_fsd",
    "n_frame_rows",
    "abstain_rate",
    "error_rate",
    "mean_pv_score",
    "mean_sequence_confidence",
]


def load_long(path: Path, chip_substrings: list[str]):
    """Load long_all.csv restricted to units whose chip_id matches one of
    chip_substrings.

    Returns:
      profiles: dict[unit] -> dict[rep] -> dict[capture_date] -> pv_present
                (a date appears once per rep; a later row for the same
                (rep, date) overwrites an earlier one, per spec)
      rows:     dict[unit] -> list of raw row dicts (frame-level stats)
    """
    profiles: dict[tuple, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    rows: dict[tuple, list[dict]] = defaultdict(list)
    if not path.exists():
        return profiles, rows
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if not any(s in r["chip_id"] for s in chip_substrings):
                continue
            unit = (r["chip_id"], r["anchor_id"], r["target_label"])
            profiles[unit][r["rep"]][r["capture_date"]] = r["pv_present"]
            rows[unit].append(r)
    return profiles, rows


def summarize_unit(profiles: dict, rows: dict, unit: tuple) -> dict:
    """Per-source metrics for one unit. Blank ("") where undefined (e.g. the
    unit has no rows yet in a still-running source)."""
    reps = profiles.get(unit, {})
    frame_rows = rows.get(unit, [])
    n_reps = len(reps)

    out: dict = {m: "" for m in UNIT_METRICS}
    out["n_reps"] = n_reps
    out["n_frame_rows"] = len(frame_rows)

    if n_reps:
        fpd_list, fsd_list, year_list, syear_list, undated_list, sundated_list = [], [], [], [], [], []
        for _rep, datemap in sorted(reps.items()):
            profile = sorted(datemap.items())
            fpd, fsd, und = derive_install(profile)
            fpd_list.append("UNDATED" if und else f"FPD|{fpd}")
            fsd_list.append("UNDATED" if fsd == "" else f"FSD|{fsd}")
            year_list.append("" if und or len(fpd) < 4 else fpd[:4])
            syear_list.append("" if fsd == "" else fsd[:4])
            undated_list.append(1 if und else 0)
            sundated_list.append(1 if fsd == "" else 0)

        modal_fpd, iv_hit = _mode_hit(fpd_list)
        modal_fsd, fsd_hit = _mode_hit(fsd_list)
        _, yr_hit = _mode_hit(year_list)
        _, syr_hit = _mode_hit(syear_list)

        out["interval_mode_hit"] = round(iv_hit, 3)
        out["sustained_mode_hit"] = round(fsd_hit, 3)
        out["year_mode_hit"] = round(yr_hit, 3)
        out["sustained_year_mode_hit"] = round(syr_hit, 3)
        out["fpd_undated_flip_rate"] = round(sum(undated_list) / len(undated_list), 3)
        out["sustained_undated_flip_rate"] = round(sum(sundated_list) / len(sundated_list), 3)
        out["modal_fpd"] = modal_fpd
        out["modal_fsd"] = modal_fsd

    if frame_rows:
        n_frame = len(frame_rows)
        n_abstain = sum(1 for r in frame_rows if r["pv_present"] == "" and r["error"] == "")
        n_error = sum(1 for r in frame_rows if r["error"] != "")
        pv_scores = [float(r["pv_score"]) for r in frame_rows if r["pv_score"] != ""]
        seq_confs = [
            float(r["sequence_confidence"]) for r in frame_rows if r["sequence_confidence"] != ""
        ]
        out["abstain_rate"] = round(n_abstain / n_frame, 3)
        out["error_rate"] = round(n_error / n_frame, 3)
        if pv_scores:
            out["mean_pv_score"] = round(sum(pv_scores) / len(pv_scores), 4)
        if seq_confs:
            out["mean_sequence_confidence"] = round(sum(seq_confs) / len(seq_confs), 4)

    return out


def _fmt(v) -> str:
    return "" if v == "" else str(v)


def build_compare_row(unit: tuple, banked: dict, tight: dict) -> dict:
    chip_id, _anchor_id, target_label = unit
    row = {"unit": str(unit), "chip_id": chip_id, "target_label": target_label}
    for m in UNIT_METRICS:
        row[f"{m}_banked"] = _fmt(banked[m])
        row[f"{m}_tight"] = _fmt(tight[m])
    for m in DELTA_METRICS:
        b, t = banked[m], tight[m]
        row[f"{m}_delta"] = round(t - b, 3) if (b != "" and t != "") else ""
    return row


def _mean(values: list) -> float | str:
    nums = [float(v) for v in values if v != ""]
    return round(sum(nums) / len(nums), 3) if nums else ""


def source_summary(per_unit_rows: list[dict], suffix: str) -> dict:
    n_reps_vals = [int(r[f"n_reps_{suffix}"]) for r in per_unit_rows if r[f"n_reps_{suffix}"] != ""]
    n_frame_vals = [int(r[f"n_frame_rows_{suffix}"]) for r in per_unit_rows if r[f"n_frame_rows_{suffix}"] != ""]
    out: dict = {
        "n_units_with_data": sum(1 for v in n_reps_vals if v > 0),
        "n_reps_total": sum(n_reps_vals),
        "n_frame_rows_total": sum(n_frame_vals),
    }
    for m in ["interval_mode_hit", "sustained_mode_hit", "year_mode_hit",
              "sustained_year_mode_hit", "fpd_undated_flip_rate",
              "sustained_undated_flip_rate", "abstain_rate",
              "error_rate", "mean_pv_score", "mean_sequence_confidence"]:
        out[f"mean_{m}"] = _mean([r[f"{m}_{suffix}"] for r in per_unit_rows])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--long-banked", type=Path,
        default=Path("/home/gaosh/zasolar_data/geid_temporal/fullstack_noscan_20260630/long_all.csv"),
    )
    ap.add_argument(
        "--long-tight", type=Path,
        default=Path("/home/gaosh/zasolar_data/geid_temporal/panel_repair_20260703/tightcrop_failed/long_all.csv"),
    )
    ap.add_argument("--chips", default="c0009952,c0010157")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    chip_substrings = [c.strip() for c in a.chips.split(",") if c.strip()]

    banked_profiles, banked_rows = load_long(a.long_banked, chip_substrings)
    tight_profiles, tight_rows = load_long(a.long_tight, chip_substrings)

    all_units = sorted(set(banked_profiles) | set(tight_profiles))
    if not all_units:
        print(f"no units matched chips={chip_substrings} in either source", file=sys.stderr)
        return 1

    per_unit = []
    for unit in all_units:
        banked_m = summarize_unit(banked_profiles, banked_rows, unit)
        tight_m = summarize_unit(tight_profiles, tight_rows, unit)
        per_unit.append(build_compare_row(unit, banked_m, tight_m))

    fieldnames = list(per_unit[0].keys())
    with open(a.out_dir / "per_unit_compare.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(per_unit)

    banked_summary = source_summary(per_unit, "banked")
    tight_summary = source_summary(per_unit, "tight")
    deltas = {}
    for m in DELTA_METRICS:
        b, t = banked_summary.get(f"mean_{m}", ""), tight_summary.get(f"mean_{m}", "")
        deltas[f"mean_{m}_delta"] = round(t - b, 3) if (b != "" and t != "") else ""

    dates_changed = []
    for r in per_unit:
        fb, ft = r["modal_fsd_banked"], r["modal_fsd_tight"]
        if fb == "" and ft == "":
            continue
        if fb != ft:
            dates_changed.append({
                "unit": r["unit"],
                "modal_fsd_banked": fb,
                "modal_fsd_tight": ft,
                "modal_fpd_banked": r["modal_fpd_banked"],
                "modal_fpd_tight": r["modal_fpd_tight"],
            })

    summary = {
        "experiment": "ISSUE-04 panel repair: banked 60m/128px vs tight-crop 12m/256px rescore",
        "chips": chip_substrings,
        "n_units": len(per_unit),
        "banked": banked_summary,
        "tight": tight_summary,
        "deltas": deltas,
        "dates_changed": dates_changed,
    }
    (a.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # markdown
    def unit_suffix(r: dict) -> str:
        return f"{r['chip_id'][-9:]}/{r['target_label']}"

    L = [
        "# ISSUE-04 panel repair: banked vs tight-crop compare", "",
        f"- chips: {', '.join(chip_substrings)}   units: {len(per_unit)}",
        f"- banked: n_units_with_data={banked_summary['n_units_with_data']} "
        f"n_reps_total={banked_summary['n_reps_total']} n_frame_rows_total={banked_summary['n_frame_rows_total']}",
        f"- tight:  n_units_with_data={tight_summary['n_units_with_data']} "
        f"n_reps_total={tight_summary['n_reps_total']} n_frame_rows_total={tight_summary['n_frame_rows_total']}",
        "",
        "## Sustained-date estimator (FSD)", "",
        "| unit | sustained hit b/t | year hit b/t | undated-flip b/t | abstain b/t | modal FSD banked | modal FSD tight |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in per_unit:
        L.append(
            f"| {unit_suffix(r)} | {_fmt(r['sustained_mode_hit_banked'])}/{_fmt(r['sustained_mode_hit_tight'])} "
            f"| {_fmt(r['sustained_year_mode_hit_banked'])}/{_fmt(r['sustained_year_mode_hit_tight'])} "
            f"| {_fmt(r['sustained_undated_flip_rate_banked'])}/{_fmt(r['sustained_undated_flip_rate_tight'])} "
            f"| {_fmt(r['abstain_rate_banked'])}/{_fmt(r['abstain_rate_tight'])} "
            f"| {_fmt(r['modal_fsd_banked'])} | {_fmt(r['modal_fsd_tight'])} |"
        )
    L.append(
        f"| **mean** | {tight_summary.get('mean_sustained_mode_hit','')} (tight) vs "
        f"{banked_summary.get('mean_sustained_mode_hit','')} (banked) "
        f"| {tight_summary.get('mean_sustained_year_mode_hit','')} vs "
        f"{banked_summary.get('mean_sustained_year_mode_hit','')} "
        f"| {tight_summary.get('mean_sustained_undated_flip_rate','')} vs "
        f"{banked_summary.get('mean_sustained_undated_flip_rate','')} "
        f"| {tight_summary.get('mean_abstain_rate','')} vs {banked_summary.get('mean_abstain_rate','')} | | |"
    )

    L += [
        "", "## First-present-date estimator (FPD)", "",
        "| unit | interval hit b/t | undated-flip b/t | modal FPD banked | modal FPD tight |",
        "|---|---|---|---|---|",
    ]
    for r in per_unit:
        L.append(
            f"| {unit_suffix(r)} | {_fmt(r['interval_mode_hit_banked'])}/{_fmt(r['interval_mode_hit_tight'])} "
            f"| {_fmt(r['fpd_undated_flip_rate_banked'])}/{_fmt(r['fpd_undated_flip_rate_tight'])} "
            f"| {_fmt(r['modal_fpd_banked'])} | {_fmt(r['modal_fpd_tight'])} |"
        )
    L.append(
        f"| **mean** | {tight_summary.get('mean_interval_mode_hit','')} (tight) vs "
        f"{banked_summary.get('mean_interval_mode_hit','')} (banked) "
        f"| {tight_summary.get('mean_fpd_undated_flip_rate','')} vs "
        f"{banked_summary.get('mean_fpd_undated_flip_rate','')} | | |"
    )

    L += ["", "## Deltas (tight minus banked, mean over units)", ""]
    for m in DELTA_METRICS:
        L.append(f"- {m}: {deltas.get(f'mean_{m}_delta', '')}")

    if dates_changed:
        L += ["", "## Units whose modal sustained/first-present date changed", ""]
        for d in dates_changed:
            L.append(
                f"- {d['unit']}: FSD {d['modal_fsd_banked']!r} -> {d['modal_fsd_tight']!r}, "
                f"FPD {d['modal_fpd_banked']!r} -> {d['modal_fpd_tight']!r}"
            )

    (a.out_dir / "table.md").write_text("\n".join(L) + "\n")

    print(json.dumps(summary, indent=2))
    print(f"\nwrote {a.out_dir}/per_unit_compare.csv + summary.json + table.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
