"""CLI orchestrator for the CoJ true-date audit pilot (ISSUE-08).

Stages (each resumable / independently re-runnable):

  sample  -- derive strata from install_intervals.csv + join anchor bboxes
             from chip_groups_as_anchors.csv / census2023_cohort.csv; seeded
             sample -> sample.csv (one row per anchor) + anchor_years.csv
             (one row per (anchor, year) fetch/score unit; both 2019 and
             2023 are fetched for every sampled anchor).
  fetch   -- hit the CoJ ArcGIS ImageServers for each (anchor, year); writes
             chips/{year}/{anchor_id}.tif + appends to fetch_stats.jsonl.
             Skips anchors whose chip file already exists and is non-empty.
  score   -- run the census Mask R-CNN detector over every fetched chip
             (+ optional solar_cls classifier corroboration subprocess) ->
             scored.csv.
  join    -- apply the margin rule + conservative-bounds contradiction logic
             -> audit_bits.csv + human_queue.csv (low-margin rows).
  gates   -- compute the three self-gates + fetch-reliability roll-up ->
             gates_report.json.
  all     -- run every stage in order.

This module is deliberately I/O-heavy (real network, real GPU, real CSVs) —
all the logic it calls (`coj_audit_join`, `coj_arcgis_fetch`, `score_coj_chips`)
is unit-tested in isolation; this file just wires them together.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit.coj_arcgis_fetch import (  # noqa: E402
    DEFAULT_POLITENESS_SLEEP_S,
    DEFAULT_USER_AGENT,
    fetch_and_save_chip,
)
from scripts.audit.coj_audit_join import (  # noqa: E402
    build_audit_bit,
    compute_gates,
    sample_pilot,
)

DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
DEFAULT_OUTPUT_ROOT = DATA_ROOT / "coj_audit_pilot_20260703"

DEFAULT_INTERVALS_CSV = (
    DATA_ROOT / "jhb_full382_fpcut_scan_2026-06-02" / "install_intervals.csv"
)
DEFAULT_CHIPGROUPS_CSV = (
    DATA_ROOT
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_groups_as_anchors.csv"
)
DEFAULT_CENSUS2023_CSV = (
    DATA_ROOT
    / "jhb_full382_fpcut_scan_2026-06-02"
    / "census2023"
    / "census2023_cohort.csv"
)

YEARS = (2019, 2023)
BBOX_COLS = ("chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max")


# ---------------------------------------------------------------------------
# Stage: sample
# ---------------------------------------------------------------------------


def stage_sample(
    output_root: Path,
    *,
    intervals_csv: Path = DEFAULT_INTERVALS_CSV,
    chipgroups_csv: Path = DEFAULT_CHIPGROUPS_CSV,
    census2023_csv: Path = DEFAULT_CENSUS2023_CSV,
    seed: int = 20260703,
    n_s1: int = 30,
    n_s2: int = 20,
    n_s4: int = 15,
) -> Path:
    intervals = pd.read_csv(intervals_csv)
    chipgroups = pd.read_csv(chipgroups_csv)
    census2023 = pd.read_csv(census2023_csv)

    intervals_with_bbox = intervals.merge(
        chipgroups[["anchor_id", *BBOX_COLS, "region_key", "grid_id"]],
        on="anchor_id", how="left", suffixes=("", "_cg"),
    )
    intervals_rows = intervals_with_bbox.to_dict("records")

    # Ambiguous stratum: sample from census2023_cohort, restricted to
    # id_kind=="c" (chip-group rows) so install_interval_start/status are
    # populated after the join back to install_intervals (id_kind=="t"
    # target-level rows have no chip-group-level interval and are excluded
    # from this pilot's stratum 4 for that reason).
    census2023_c = census2023[census2023["id_kind"] == "c"].merge(
        intervals[["anchor_id", "status", "install_interval_start"]],
        on="anchor_id", how="left",
    )
    ambiguous_rows = census2023_c.to_dict("records")

    sample = sample_pilot(
        intervals_rows, ambiguous_rows, seed=seed, n_s1=n_s1, n_s2=n_s2, n_s4=n_s4
    )

    output_root.mkdir(parents=True, exist_ok=True)
    sample_csv = output_root / "sample.csv"
    fieldnames = sorted({k for r in sample for k in r.keys()})
    with open(sample_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(sample)

    # Long format: one row per (anchor, year).
    anchor_years_csv = output_root / "anchor_years.csv"
    long_rows = []
    for row in sample:
        for year in YEARS:
            long_rows.append(
                {
                    "anchor_id": row["anchor_id"],
                    "stratum": row["stratum"],
                    "year": year,
                    "status": row.get("status"),
                    "install_interval_start": row.get("install_interval_start"),
                    "install_interval_end": row.get("install_interval_end"),
                    "confidence": row.get("confidence"),
                    "chip_lon_min": row.get("chip_lon_min"),
                    "chip_lat_min": row.get("chip_lat_min"),
                    "chip_lon_max": row.get("chip_lon_max"),
                    "chip_lat_max": row.get("chip_lat_max"),
                }
            )
    with open(anchor_years_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in long_rows for k in r.keys()}))
        w.writeheader()
        w.writerows(long_rows)

    print(
        f"[sample] {len(sample)} anchors "
        f"({sum(1 for r in sample if r['stratum'] == 's1_known_present_pre2019')} s1, "
        f"{sum(1 for r in sample if r['stratum'] == 's2_known_present_2019_2023')} s2, "
        f"{sum(1 for r in sample if r['stratum'] == 's3_known_absent_2023')} s3, "
        f"{sum(1 for r in sample if r['stratum'] == 's4_ambiguous')} s4) "
        f"-> {sample_csv} ({len(long_rows)} (anchor,year) fetch/score units -> {anchor_years_csv})"
    )
    return anchor_years_csv


# ---------------------------------------------------------------------------
# Stage: fetch
# ---------------------------------------------------------------------------


def _requests_http_get(url: str, *, timeout_s: float = 30.0):
    return requests.get(url, timeout=timeout_s, headers={"User-Agent": DEFAULT_USER_AGENT})


def stage_fetch(
    output_root: Path,
    anchor_years_csv: Path,
    *,
    politeness_sleep_s: float = DEFAULT_POLITENESS_SLEEP_S,
    max_attempts: int = 3,
    timeout_s: float = 30.0,
) -> Path:
    with open(anchor_years_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    chips_root = output_root / "chips"
    fetch_stats_path = output_root / "fetch_stats.jsonl"
    fetched_csv = output_root / "anchor_years_fetched.csv"

    out_rows = []
    with open(fetch_stats_path, "a") as stats_f:
        for i, row in enumerate(rows):
            year = int(row["year"])
            anchor_id = row["anchor_id"]
            bbox = tuple(float(row[c]) for c in BBOX_COLS)
            out_path = chips_root / str(year) / f"{anchor_id}.tif"

            outcome = fetch_and_save_chip(
                anchor_id, year, bbox, out_path,
                http_get=lambda u: _requests_http_get(u, timeout_s=timeout_s),
                sleep_fn=time.sleep, max_attempts=max_attempts,
            )
            stats_f.write(json.dumps({"anchor_id": anchor_id, **outcome.to_dict()}) + "\n")
            stats_f.flush()

            new_row = dict(row)
            new_row["chip_path"] = str(out_path) if outcome.outcome in ("ok", "skipped_existing") else ""
            new_row["fetch_outcome"] = outcome.outcome
            out_rows.append(new_row)

            if outcome.outcome != "skipped_existing":
                time.sleep(politeness_sleep_s)

            if (i + 1) % 20 == 0:
                print(f"[fetch] {i + 1}/{len(rows)} done")

    with open(fetched_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in out_rows for k in r.keys()}))
        w.writeheader()
        w.writerows(out_rows)

    n_ok = sum(1 for r in out_rows if r["fetch_outcome"] in ("ok", "skipped_existing"))
    print(f"[fetch] {n_ok}/{len(out_rows)} chips available -> {fetched_csv}")
    return fetched_csv


# ---------------------------------------------------------------------------
# Stage: score
# ---------------------------------------------------------------------------


def stage_score(
    output_root: Path,
    fetched_csv: Path,
    *,
    checkpoint: Path | None = None,
    device: str = "cuda",
    skip_classifier: bool = False,
) -> Path:
    from scripts.audit.score_coj_chips import (
        DEFAULT_CHECKPOINT,
        load_detector,
        run_classifier_manifest,
        load_classifier_probs,
        score_manifest,
    )
    import torch

    with open(fetched_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("chip_path")]

    dev = torch.device(device if (device != "cuda" or torch.cuda.is_available()) else "cpu")
    model = load_detector(checkpoint or DEFAULT_CHECKPOINT, device=str(dev))
    scored = score_manifest(rows, model, dev)

    if not skip_classifier:
        manifest_csv = output_root / "classifier_manifest.csv"
        scores_csv = output_root / "classifier_scores.csv"
        with open(manifest_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["chip_path"])
            w.writeheader()
            for r in scored:
                if r.get("detector_status") == "scored":
                    w.writerow({"chip_path": r["chip_path"]})
        result = run_classifier_manifest(manifest_csv, scores_csv)
        print(f"[score] classifier subprocess ok={result.get('ok')}")
        if result.get("ok"):
            probs = load_classifier_probs(scores_csv)
            for r in scored:
                r["classifier_pv_prob"] = probs.get(r["chip_path"])
        else:
            print(f"[score] classifier subprocess FAILED, demoting to detector-only: {result.get('error') or result.get('stderr', '')[:500]}")
            for r in scored:
                r["classifier_pv_prob"] = None

    scored_csv = output_root / "scored.csv"
    fieldnames = sorted({k for r in scored for k in r.keys()})
    with open(scored_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(scored)
    print(f"[score] {len(scored)} rows -> {scored_csv}")
    return scored_csv


# ---------------------------------------------------------------------------
# Stage: join
# ---------------------------------------------------------------------------


def stage_join(output_root: Path, scored_csv: Path) -> Path:
    with open(scored_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    audit_bits = []
    for row in rows:
        if not row.get("score"):
            continue  # missing_chip / unreadable — no bit to compute
        r = dict(row)
        r["score"] = float(r["score"])
        cp = r.get("classifier_pv_prob")
        r["classifier_pv_prob"] = float(cp) if cp not in (None, "", "None") else None
        audit_bits.append(build_audit_bit(r))

    audit_bits_csv = output_root / "audit_bits.csv"
    human_queue_csv = output_root / "human_queue.csv"
    fieldnames = sorted({k for r in audit_bits for k in r.keys()})
    with open(audit_bits_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(audit_bits)
    with open(human_queue_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows([r for r in audit_bits if r["routed_to_queue"]])

    n_queue = sum(1 for r in audit_bits if r["routed_to_queue"])
    print(f"[join] {len(audit_bits)} bits -> {audit_bits_csv} ({n_queue} routed to {human_queue_csv})")
    return audit_bits_csv


# ---------------------------------------------------------------------------
# Stage: gates
# ---------------------------------------------------------------------------


def _fetch_reliability_summary(fetch_stats_path: Path) -> dict:
    if not fetch_stats_path.exists():
        return {}
    by_layer: dict[str, dict[str, int]] = {}
    latencies = []
    with open(fetch_stats_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            layer = rec.get("layer", "?")
            outcome = rec.get("outcome", "?")
            by_layer.setdefault(layer, {}).setdefault(outcome, 0)
            by_layer[layer][outcome] += 1
            if rec.get("attempts", 0) >= 1 and rec.get("outcome") == "ok":
                latencies.append(rec.get("latency_s", 0.0))
    return {
        "by_layer_outcome_counts": by_layer,
        "n_requests_logged": sum(sum(v.values()) for v in by_layer.values()),
        "mean_latency_s_ok": (sum(latencies) / len(latencies)) if latencies else None,
    }


def stage_gates(output_root: Path, audit_bits_csv: Path) -> Path:
    with open(audit_bits_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["year"] = int(r["year"])
        r["score"] = float(r["score"])
        r["contradiction"] = r["contradiction"] in ("True", "true", "1", True)
        r["routed_to_queue"] = r["routed_to_queue"] in ("True", "true", "1", True)
        r["agrees"] = {"True": True, "False": False, "": None, "None": None}.get(r.get("agrees"), r.get("agrees"))
        r["expected"] = {"True": True, "False": False, "": None, "None": None}.get(r.get("expected"), r.get("expected"))

    gates = compute_gates(rows)

    contradictions_by_stratum: dict[str, int] = {}
    for r in rows:
        if r["contradiction"]:
            contradictions_by_stratum[r["stratum"]] = contradictions_by_stratum.get(r["stratum"], 0) + 1

    report = {
        "n_audit_bits": len(rows),
        "n_high_margin": sum(1 for r in rows if r["bit"] in ("present", "absent")),
        "n_low_margin_routed_to_queue": sum(1 for r in rows if r["routed_to_queue"]),
        "gates": gates,
        "contradictions_by_stratum": contradictions_by_stratum,
        "fetch_reliability": _fetch_reliability_summary(output_root / "fetch_stats.jsonl"),
    }

    gates_report_path = output_root / "gates_report.json"
    with open(gates_report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"[gates] -> {gates_report_path}")
    print(json.dumps(report["gates"]["within_audit_monotonicity"], indent=2))
    print(json.dumps(report["gates"]["clamp_monotonicity"], indent=2))
    return gates_report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["sample", "fetch", "score", "join", "gates", "all"])
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ap.add_argument("--seed", type=int, default=20260703)
    ap.add_argument("--n-s1", type=int, default=30)
    ap.add_argument("--n-s2", type=int, default=20)
    ap.add_argument("--n-s4", type=int, default=15)
    ap.add_argument("--checkpoint", type=Path, default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--skip-classifier", action="store_true")
    ap.add_argument("--politeness-sleep-s", type=float, default=DEFAULT_POLITENESS_SLEEP_S)
    args = ap.parse_args()

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    if args.stage in ("sample", "all"):
        anchor_years_csv = stage_sample(
            output_root, seed=args.seed, n_s1=args.n_s1, n_s2=args.n_s2, n_s4=args.n_s4
        )
    else:
        anchor_years_csv = output_root / "anchor_years.csv"

    if args.stage in ("fetch", "all"):
        fetched_csv = stage_fetch(output_root, anchor_years_csv, politeness_sleep_s=args.politeness_sleep_s)
    else:
        fetched_csv = output_root / "anchor_years_fetched.csv"

    if args.stage in ("score", "all"):
        scored_csv = stage_score(
            output_root, fetched_csv, checkpoint=args.checkpoint, device=args.device,
            skip_classifier=args.skip_classifier,
        )
    else:
        scored_csv = output_root / "scored.csv"

    if args.stage in ("join", "all"):
        audit_bits_csv = stage_join(output_root, scored_csv)
    else:
        audit_bits_csv = output_root / "audit_bits.csv"

    if args.stage in ("gates", "all"):
        stage_gates(output_root, audit_bits_csv)


if __name__ == "__main__":
    main()
