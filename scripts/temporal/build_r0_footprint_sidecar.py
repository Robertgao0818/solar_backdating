#!/usr/bin/env python3
"""R0.1 footprint sidecar — versioned per-anchor footprint geometry (PRD §3.2
amendment, owner-approved 2026-07-19, commit 563a772).

Purpose: close the seam reported in
``docs/dinov3_scorer/DATA-r1-crops-2026-07-19.md`` §4.1 — the R1 nominal ROI is
an equal-area **square** (``roi_edge_m = sqrt(source_area_m2)``) because the
frozen R0 manifest carries only ``source_area_m2``, not the footprint's width/
height, so a long thin panel array collapses to a square. This sidecar is a
**pure additive** table (keyed on ``anchor_id``) carrying the footprint width/
height (and derived aspect / bbox-fill) from the anchors CSV, so a future
``r1_cropgeo_v2`` can build an aspect-aware ROI. It NEVER touches the frozen R0
products (``manifest.parquet`` / ``splits.parquet`` / ``MANIFEST_LOCK.json`` are
read-only; a sha guard asserts they are byte-identical after the run).

Source columns (self-checked against ``anchors_all.csv`` 2026-07-19): the CSV
has **no polygon/WKT and no orientation/angle column** — only ``source_width_m``
/ ``source_height_m`` (axis-aligned bounding-box dims, metres) and
``source_area_m2``. Caveat baked into the lock/memo: because the bbox is
axis-aligned, ``bbox_fill = area/(w·h)`` is well below 1 (median ~0.63, tail to
~0.17) for rotated/irregular footprints, so w·h **overstates** the tight extent
and the bbox aspect is a **lower bound** on the true panel elongation. The tight
oriented polygon lives only in the legacy source gpkgs
(``legacy_source_inventory_path``); pulling it is out of scope here (that is R2's
``projected_target_polygon`` job) and noted in the memo.

Reconciliation (any mismatch aborts non-zero, per task red line):
  * sidecar rows == 41,393 and the ``anchor_id`` set == the manifest's exactly
    (0 missing / 0 extra);
  * the CSV ``source_area_m2`` == the manifest ``source_area_m2`` per anchor
    (they share an origin; a drift means the wrong CSV).

Outputs (data only, never the repo) under ``r0_manifest_v1/``:
  * ``footprint_sidecar_v1.parquet``
  * ``FOOTPRINT_SIDECAR_LOCK.json`` (version, source sha, rows, reconciliation,
    area-consistency + aspect distributions, frozen-file sha guard).

Run from the shared venv (``source scripts/activate_env.sh``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

FOOTPRINT_VERSION = "footprint_v1@2026-07-19"

DEFAULT_ANCHORS_CSV = (
    Path.home()
    / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
    / "anchors_v2/anchors_all.csv"
)
DEFAULT_R0_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1"
)
EXPECTED_ANCHORS = 41393
# Frozen R0 products — read-only; the run must leave these byte-identical.
FROZEN_FILES = ("manifest.parquet", "splits.parquet", "MANIFEST_LOCK.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_sidecar_frame(anchors_csv: Path) -> pd.DataFrame:
    """Extract the per-anchor footprint geometry + derived shape descriptors."""
    a = pd.read_csv(anchors_csv)
    cols = ["anchor_id", "chip_arm", "source_area_m2", "source_width_m", "source_height_m"]
    missing = [c for c in cols if c not in a.columns]
    if missing:
        raise SystemExit(f"[R0.1][FATAL] anchors CSV missing columns: {missing}")
    df = a[cols].copy()
    df["source_area_m2"] = df["source_area_m2"].astype(float)
    df["source_width_m"] = df["source_width_m"].astype(float)
    df["source_height_m"] = df["source_height_m"].astype(float)

    w = df["source_width_m"].to_numpy()
    h = df["source_height_m"].to_numpy()
    area = df["source_area_m2"].to_numpy()
    long_m = np.maximum(w, h)
    short_m = np.minimum(w, h)
    df["footprint_long_m"] = long_m
    df["footprint_short_m"] = short_m
    df["bbox_area_m2"] = w * h
    # area / bbox-area: 1.0 for a perfect axis-aligned rectangle; <1 for rotated
    # or irregular footprints (documents that w·h overstates the tight extent).
    with np.errstate(divide="ignore", invalid="ignore"):
        df["bbox_fill"] = np.where(df["bbox_area_m2"] > 0, area / df["bbox_area_m2"], np.nan)
        df["aspect_ratio"] = np.where(short_m > 0, long_m / short_m, np.nan)
    df["sqrt_area_m"] = np.sqrt(np.clip(area, 0, None))
    # aspect-matched, area-preserving rectangle (the proposed cropgeo_v2 ROI):
    # long=sqrt(A·r), short=sqrt(A/r) with r=aspect_ratio. Uses w/h only for the
    # aspect, not the (inflated) absolute size -- carried so v2 needs no re-derive.
    r = df["aspect_ratio"].to_numpy()
    df["areamatched_long_m"] = np.sqrt(np.clip(area, 0, None) * r)
    df["areamatched_short_m"] = np.sqrt(np.clip(area, 0, None) / r)
    df["footprint_version"] = FOOTPRINT_VERSION
    return df.sort_values("anchor_id").reset_index(drop=True)


def reconcile(df: pd.DataFrame, manifest_path: Path) -> dict:
    """Hard reconciliation vs the frozen manifest (rows, anchor set, area)."""
    m = pd.read_parquet(manifest_path, columns=["anchor_id", "source_area_m2"])
    m = m.drop_duplicates("anchor_id")
    man_ids = set(m["anchor_id"])
    side_ids = set(df["anchor_id"])

    diffs: list[str] = []
    if len(df) != EXPECTED_ANCHORS:
        diffs.append(f"rows: actual={len(df)} expected={EXPECTED_ANCHORS}")
    if df["anchor_id"].nunique() != len(df):
        diffs.append("anchor_id not unique in sidecar")
    missing = man_ids - side_ids
    extra = side_ids - man_ids
    if missing:
        diffs.append(f"manifest anchors missing from sidecar: {len(missing)}")
    if extra:
        diffs.append(f"sidecar anchors not in manifest: {len(extra)}")

    # area origin check: CSV area must equal manifest area per anchor.
    j = df.merge(m, on="anchor_id", suffixes=("_side", "_mf"))
    area_absdiff = (j["source_area_m2_side"] - j["source_area_m2_mf"]).abs()
    area_max = float(area_absdiff.max()) if len(j) else float("nan")
    n_area_mismatch = int((area_absdiff > 1e-6).sum())
    if n_area_mismatch:
        diffs.append(
            f"source_area_m2 CSV vs manifest mismatch in {n_area_mismatch} anchors "
            f"(max abs {area_max})"
        )

    return {
        "rows": len(df),
        "expected_rows": EXPECTED_ANCHORS,
        "anchor_id_unique": bool(df["anchor_id"].nunique() == len(df)),
        "manifest_anchors": len(man_ids),
        "missing_from_sidecar": len(missing),
        "extra_in_sidecar": len(extra),
        "area_vs_manifest_max_abs": area_max,
        "area_vs_manifest_mismatches": n_area_mismatch,
        "passed": not diffs,
        "diffs": diffs,
    }


def _dist(s: pd.Series) -> dict:
    s = s.dropna()
    return {
        "p50": float(s.quantile(0.5)),
        "p90": float(s.quantile(0.9)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
    }


def shape_stats(df: pd.DataFrame) -> dict:
    aspect = df["aspect_ratio"]
    fill = df["bbox_fill"]
    long_over_sqrt = df["footprint_long_m"] / df["sqrt_area_m"]
    n = len(df)
    return {
        "aspect_ratio": {
            **_dist(aspect),
            "frac_gt_1_5": float((aspect > 1.5).mean()),
            "frac_gt_2": float((aspect > 2).mean()),
            "frac_gt_3": float((aspect > 3).mean()),
            "frac_gt_4": float((aspect > 4).mean()),
            "note": "axis-aligned bbox aspect = LOWER bound on true panel elongation",
        },
        "bbox_fill": {
            **_dist(fill),
            "frac_lt_0_6": float((fill < 0.6).mean()),
            "frac_gt_0_95": float((fill > 0.95).mean()),
            "note": "area/(w*h); <1 => w*h overstates tight extent (rotation/irregularity)",
        },
        "long_side_over_sqrt_area": {
            **_dist(long_over_sqrt),
            "frac_gt_1_25": float((long_over_sqrt > 1.25).mean()),
            "frac_gt_1_5": float((long_over_sqrt > 1.5).mean()),
            "frac_gt_2": float((long_over_sqrt > 2).mean()),
            "note": "quantifies R1 sqrt(area)-square under-reach of the long footprint side",
        },
        "by_arm": {
            arm: {
                "n": int((df["chip_arm"] == arm).sum()),
                "aspect_p50": float(df.loc[df["chip_arm"] == arm, "aspect_ratio"].quantile(0.5)),
                "aspect_p90": float(df.loc[df["chip_arm"] == arm, "aspect_ratio"].quantile(0.9)),
                "frac_aspect_gt_2": float((df.loc[df["chip_arm"] == arm, "aspect_ratio"] > 2).mean()),
            }
            for arm in sorted(df["chip_arm"].dropna().unique())
        },
        "n": n,
    }


def build(args: argparse.Namespace) -> int:
    anchors_csv = Path(args.anchors_csv)
    r0_dir = Path(args.r0_dir)
    manifest_path = r0_dir / "manifest.parquet"

    # Freeze guard: record frozen shas BEFORE writing anything.
    frozen_before = {f: sha256_file(r0_dir / f) for f in FROZEN_FILES if (r0_dir / f).exists()}

    print(f"[R0.1] reading anchors CSV {anchors_csv}", flush=True)
    df = build_sidecar_frame(anchors_csv)
    print(f"[R0.1] sidecar frame: {len(df)} anchors", flush=True)

    recon = reconcile(df, manifest_path)
    if not recon["passed"]:
        print("[R0.1][RECONCILE-FAIL] STOP:", flush=True)
        for d in recon["diffs"]:
            print(f"   - {d}", flush=True)
        return 2
    print("[R0.1] reconciliation PASS (rows/anchor-set/area all match manifest)", flush=True)

    stats = shape_stats(df)

    out_parquet = r0_dir / "footprint_sidecar_v1.parquet"
    tmp = out_parquet.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out_parquet)

    lock = {
        "footprint_version": FOOTPRINT_VERSION,
        "generator": "scripts/temporal/build_r0_footprint_sidecar.py",
        "prd": "PRD-run3-native-local-line-2026-07-19.md §3.2 amendment (commit 563a772)",
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "source": {
            "anchors_csv": str(anchors_csv),
            "anchors_csv_sha256": sha256_file(anchors_csv),
        },
        "columns": list(df.columns),
        "reconciliation": recon,
        "shape_stats": stats,
        "frozen_files_readonly": FROZEN_FILES,
        "output": {
            "path": str(out_parquet),
            "rows": int(len(df)),
            "sha256": sha256_file(out_parquet),
            "bytes": out_parquet.stat().st_size,
        },
    }

    # Freeze guard: assert the frozen products are byte-identical post-write.
    frozen_after = {f: sha256_file(r0_dir / f) for f in FROZEN_FILES if (r0_dir / f).exists()}
    touched = [f for f in frozen_before if frozen_before[f] != frozen_after.get(f)]
    lock["frozen_files_unchanged"] = (not touched)
    lock["frozen_files_sha"] = frozen_after
    if touched:
        print(f"[R0.1][FATAL] frozen product(s) changed: {touched}", flush=True)
        return 3

    lock_path = r0_dir / "FOOTPRINT_SIDECAR_LOCK.json"
    with open(lock_path, "w") as fh:
        json.dump(lock, fh, indent=2, sort_keys=True, default=str)

    print(f"[R0.1] wrote {out_parquet} ({len(df)} rows)", flush=True)
    print(f"[R0.1] wrote {lock_path}", flush=True)
    print(
        f"[R0.1] aspect: p50={stats['aspect_ratio']['p50']:.2f} "
        f"p90={stats['aspect_ratio']['p90']:.2f} "
        f"frac>2={stats['aspect_ratio']['frac_gt_2']*100:.1f}%  | "
        f"bbox_fill p50={stats['bbox_fill']['p50']:.2f}",
        flush=True,
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors-csv", default=str(DEFAULT_ANCHORS_CSV))
    p.add_argument("--r0-dir", default=str(DEFAULT_R0_DIR))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return build(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
