#!/usr/bin/env python3
"""Prep for the full-stack no-search L1 reliability experiment (2026-06-30).

Builds the GEHI image-artifact manifest from the frozen *complete* vintage stack
(``chips_frozen/``) and renders one target-centered review PNG per
(chip, target, vintage) over each chip's full drivable stack. No adaptive search
and no Gemini calls here -- this just materializes the fixed dense sequence that
``fullstack_noscan_run.py`` will score K times.

Rationale: the variance decomposition (2026-06-24, [[endtoend_reproducibility_test]])
attributed install-date irreproducibility mainly to the L2 adaptive search
amplifying small L1 verdict noise. This experiment removes L2 by construction --
score every vintage on a fixed exhaustive grid -- and measures the residual
(pure-L1) reproducibility, to compare against Arm A (0.875) / Arm B (0.771).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import write_csv_rows  # noqa: E402
from scripts.temporal.score_chip_group_matrix import (  # noqa: E402
    load_artifacts_by_chip,
    load_chip_targets_by_chip,
)
from scripts.temporal.score_target_sequence import (  # noqa: E402
    REVIEW_PNG_FIELDS,
    render_review_png_manifest,
)

FNAME_RE = re.compile(r"_(\d{8})_v([0-9]+|noversion)\.tif$")
ZOOM_RE = re.compile(r"/z(\d+)/")
ARTIFACT_FIELDS = ["chip_id", "capture_date", "version", "path", "actual_zoom", "status"]


def build_artifacts(chips_frozen: Path, chip_ids: set[str]) -> list[dict]:
    rows: list[dict] = []
    for chip_id in sorted(chip_ids):
        cdir = chips_frozen / chip_id
        if not cdir.is_dir():
            print(f"  WARN: no frozen dir for {chip_id}", flush=True)
            continue
        for tif in sorted(cdir.rglob("*.tif")):
            m = FNAME_RE.search(tif.name)
            if not m:
                continue
            ymd, ver = m.group(1), m.group(2)
            cap = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            zoom = 19
            mz = ZOOM_RE.search(str(tif))
            if mz:
                zoom = int(mz.group(1))
            rows.append(
                {
                    "chip_id": chip_id,
                    "capture_date": cap,
                    "version": ver,
                    "path": str(tif),
                    "actual_zoom": zoom,
                    "status": "ok",
                }
            )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chips-frozen", type=Path, required=True)
    ap.add_argument("--chip-targets", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--crop-context-multiplier", type=float, default=3.0)
    ap.add_argument("--min-crop-size-m", type=float, default=24.0)
    ap.add_argument("--min-output-px", type=int, default=128)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    targets_by_chip = load_chip_targets_by_chip(a.chip_targets)
    chip_ids = set(targets_by_chip)
    print(f"chips={len(chip_ids)}  targets={sum(len(v) for v in targets_by_chip.values())}")

    art_rows = build_artifacts(a.chips_frozen, chip_ids)
    art_csv = a.out_dir / "artifacts_fullstack.csv"
    write_csv_rows(art_csv, art_rows, ARTIFACT_FIELDS)
    artifacts_by_chip = load_artifacts_by_chip(art_csv)

    all_rows: list[dict] = []
    for chip_id in sorted(targets_by_chip):
        arts = artifacts_by_chip.get(chip_id, [])
        dates = sorted({art.capture_date for art in arts})
        rows = render_review_png_manifest(
            targets_by_chip={chip_id: targets_by_chip[chip_id]},
            artifacts_by_chip={chip_id: arts},
            dates=dates,
            crop_context_multiplier=a.crop_context_multiplier,
            min_crop_size_m=a.min_crop_size_m,
            min_output_px=a.min_output_px,
        )
        all_rows.extend(rows)
        ok = sum(1 for r in rows if r["render_status"] == "ok")
        print(
            f"  {chip_id[-9:]}: dates={len(dates):3d} targets={len(targets_by_chip[chip_id])} "
            f"render_ok={ok}/{len(rows)}",
            flush=True,
        )

    man_csv = a.out_dir / "manifest_fullstack.csv"
    write_csv_rows(man_csv, all_rows, REVIEW_PNG_FIELDS)
    nok = sum(1 for r in all_rows if r["render_status"] == "ok")
    print(f"\nartifacts -> {art_csv}  ({len(art_rows)} rows)")
    print(f"manifest  -> {man_csv}  ({len(all_rows)} rows, {nok} render_ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
