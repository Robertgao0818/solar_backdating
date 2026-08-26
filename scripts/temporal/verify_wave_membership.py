"""Membership audit for a merged wave.

Checks every expected (anchor_id, capture_date) key from
membership_expected.json against the REWRITTEN lanes_merged manifests:
a matching ok/skipped_existing row whose `path` is a non-empty file on
this host. Writes membership_audit.json; exits 1 when anything is missing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OK_STATUSES = {"ok", "skipped_existing"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--membership", type=Path, required=True)
    parser.add_argument("--manifests-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    membership = json.loads(args.membership.read_text(encoding="utf-8"))
    expected = set(membership["keys"])

    found: set[str] = set()
    for manifest in sorted(args.manifests_root.rglob("*_manifest.csv")):
        with manifest.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                anchor = row.get("anchor_id", "")
                date = row.get("capture_date", "")
                key = f"{anchor}|{date}"
                if key in found or key not in expected:
                    continue
                if row.get("status", "").split(":", 1)[0].strip() not in OK_STATUSES:
                    continue
                chip = Path(row.get("path", ""))
                try:
                    if chip.is_file() and chip.stat().st_size > 0:
                        found.add(key)
                except OSError:
                    continue

    missing = sorted(expected - found)
    audit = {
        "schema_version": "ct_citywide_membership_audit_v1",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected": len(expected),
        "found": len(found),
        "missing_count": len(missing),
        "missing_keys_sample": missing[:50],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in audit.items() if k != "missing_keys_sample"}, indent=2))
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
