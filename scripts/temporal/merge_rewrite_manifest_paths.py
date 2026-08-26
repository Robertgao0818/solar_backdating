"""Rewrite merged lane manifests so `path` points at the home hot-set tree.

gehi_download writes absolute paths from the downloading host (e.g.
/home/koko/.../wave_01/chips/<anchor>/z19/<file>.tif). QA only checks
`Path(row['path']).is_file()`, so after chips are merged into
$DL_ROOT/chips the manifests must be re-rooted: the last three path
components (<anchor_id>/<zNN>/<file>.tif) are re-based under
--home-chips-root. Failure rows (no usable file) keep their original path.

Rewrites in place; prints a JSON summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

OK_STATUSES = {"ok", "skipped_existing"}


def _status_head(value: str) -> str:
    return value.split(":", 1)[0].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifests-root", type=Path, required=True)
    parser.add_argument("--home-chips-root", type=Path, required=True)
    args = parser.parse_args()

    home_root = args.home_chips_root.resolve()
    manifests = sorted(args.manifests_root.rglob("*_manifest.csv"))
    if not manifests:
        raise SystemExit(f"no manifests under {args.manifests_root}")

    summary = {"manifests": 0, "rows": 0, "rewritten": 0, "kept": 0, "missing_on_disk": 0}
    for path in manifests:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        fields = list(rows[0].keys())
        if "path" not in fields:
            raise SystemExit(f"manifest lacks path column: {path}")
        for row in rows:
            summary["rows"] += 1
            old = (row.get("path") or "").strip()
            if not old or _status_head(row.get("status", "")) not in OK_STATUSES:
                summary["kept"] += 1
                continue
            parts = Path(old).parts
            if len(parts) < 3:
                summary["kept"] += 1
                continue
            new = home_root / parts[-3] / parts[-2] / parts[-1]
            row["path"] = str(new)
            summary["rewritten"] += 1
            if not new.is_file():
                summary["missing_on_disk"] += 1
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        summary["manifests"] += 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["rewritten"] == 0:
        raise SystemExit("nothing rewritten; refusing to treat merge as complete")


if __name__ == "__main__":
    main()
