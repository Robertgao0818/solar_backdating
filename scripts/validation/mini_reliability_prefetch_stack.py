#!/usr/bin/env python3
"""One-time full-vintage-stack prefetch for the 12-anchor mini reliability test (arm B L3 freeze).

The end-to-end reproducibility arm (B) must vary ONLY the LLM (L1) and the adaptive
search (L2); the imagery layer (L3, GEHI re-fetch) is frozen. There is no hard
`--offline` flag in the GEHistoricalImagery CLI, but `download_chip_with_zoom_ladder`
is idempotent at the chip-file level: if the chip .tif already exists for
(anchor, capture_date, version, zoom) it returns `skipped_existing` WITHOUT touching
GEHI. So we freeze L3 by:

  1. discovering each anchor's COMPLETE-COVERAGE drivable catalog with the EXACT
     scan config the reps use (`_fetch_real_vintage_catalog`), so the prefetched
     date set == the set the adaptive search is allowed to pick from (full freedom),
  2. downloading every drivable vintage chip into a single shared chips dir,
  3. having every rep point `--chips-dir` at that shared dir (caching ON).

This avoids the failure mode where the cache only holds production's explored subset:
we prefetch the *entire* drivable stack, so any date L2 picks in any rep is a local hit.

No Gemini calls. GEHI `info`/`availability` (catalog) + `download` (tiles) only, all
cached by the CLI after the first pass.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_download import _chip_path_for, download_chip_with_zoom_ladder
from scripts.temporal.run_adaptive_scan import _fetch_real_vintage_catalog
from scripts.temporal.scan_config import AdaptiveScanConfig


def _load_anchors(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--anchors-csv", type=Path, required=True)
    ap.add_argument("--chips-dir", type=Path, required=True, help="shared frozen chips dir for all reps")
    ap.add_argument("--provider", default="TM")
    ap.add_argument("--limit-anchors", type=int, default=None)
    a = ap.parse_args()

    cfg = AdaptiveScanConfig(provider=a.provider)
    anchors = _load_anchors(a.anchors_csv)
    if a.limit_anchors:
        anchors = anchors[: a.limit_anchors]
    a.chips_dir.mkdir(parents=True, exist_ok=True)

    stats = defaultdict(int)
    per_anchor: list[tuple[str, int, int, int]] = []  # (anchor, n_drivable, n_ok, n_fail)
    for anchor in anchors:
        aid = anchor["anchor_id"]
        catalog = _fetch_real_vintage_catalog(anchor, cfg)
        vintages = catalog.vintages  # complete-coverage drivable set, what L2 can pick
        n_ok = n_fail = 0
        for v in vintages:
            out = download_chip_with_zoom_ladder(
                anchor,
                capture_date=v.capture_date,
                version=v.version,
                zoom_ladder=cfg.download_zoom_ladder,
                output_root=a.chips_dir,
                provider=cfg.provider,
            )
            stats[out.status] += 1
            if out.status in ("ok", "skipped_existing"):
                n_ok += 1
            else:
                n_fail += 1
        per_anchor.append((aid, len(vintages), n_ok, n_fail))
        print(f"  {aid[-9:]}: drivable={len(vintages):3d}  ok/cached={n_ok:3d}  fail={n_fail}",
              flush=True)

    print("\n=== prefetch status totals ===")
    for k, n in sorted(stats.items()):
        print(f"  {k}: {n}")
    total_drivable = sum(p[1] for p in per_anchor)
    total_ok = sum(p[2] for p in per_anchor)
    print(f"\ndrivable vintages across {len(anchors)} anchors: {total_drivable}")
    print(f"chips present (ok+skipped) in {a.chips_dir}: {total_ok}")
    print(f"newly downloaded this run: {stats.get('ok', 0)}  "
          f"(already cached: {stats.get('skipped_existing', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
