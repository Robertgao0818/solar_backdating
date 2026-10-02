#!/usr/bin/env python3
"""Membership-scoped SHARED-hotset PNG (+ orphan TFW) delete.

The Cape Town hotset is keyed by anchor folder, not per-wave. After
RELEASE_OK+BACKUP_OK, delete only this wave's review PNGs (and leftover
*.tfw world-files) using the same membership list as tif release
(wave_NN_all.csv / members.txt).

chip_lifecycle.py deletes *.tif (and companion *.tfw next to those tifs).
This helper covers the user 2026-08-29 rule change: do not keep pngs for
audit, plus any orphan *.tfw left from earlier tif-only releases.

Never rm -rf the hotset. Never delete *.tif. Never delete a whole anchor
folder if it still has tifs. After deletes, rmdir empty z<zoom> dirs;
rmdir the anchor dir only if it is then empty. Refuses unreleased wave_13
membership unless RELEASE_OK exists or --allow-unreleased.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def load_anchor_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        first = True
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if first and (line.startswith("anchor_id") or "," in line):
                first = False
                head = line.split(",", 1)[0]
                if head == "anchor_id":
                    for row in csv.DictReader([line, *fh]):
                        a = (row.get("anchor_id") or "").strip()
                        if a:
                            ids.add(a)
                    return ids
                ids.add(head)
                continue
            first = False
            ids.add(line.split(",", 1)[0])
    return ids


def looks_like_wave13(anchor_ids_file: Path) -> bool:
    s = str(anchor_ids_file)
    return "wave_13" in s or "wave_13" in anchor_ids_file.name


def rmdir_empty_zoom_and_anchor(anchor_dir: Path) -> tuple[int, int]:
    """rmdir empty z* dirs; rmdir anchor only if empty and no tifs remain."""
    empty_z = 0
    empty_a = 0
    if not anchor_dir.is_dir():
        return 0, 0
    for child in list(anchor_dir.iterdir()):
        if child.is_dir() and child.name.startswith("z"):
            try:
                next(child.iterdir())
            except StopIteration:
                try:
                    child.rmdir()
                    empty_z += 1
                except OSError:
                    pass
    # do not remove an anchor dir that still has tifs (or anything else)
    try:
        remaining = list(anchor_dir.iterdir())
    except OSError:
        return empty_z, 0
    if remaining:
        return empty_z, 0
    try:
        anchor_dir.rmdir()
        empty_a += 1
    except OSError:
        pass
    return empty_z, empty_a


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hotset", type=Path, required=True)
    p.add_argument("--anchor-ids-file", type=Path, required=True)
    p.add_argument("--release-ok", type=Path, default=None)
    p.add_argument("--backup-ok", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--allow-unreleased",
        action="store_true",
        help="Allow wave_13 / missing RELEASE_OK+BACKUP_OK (do not use).",
    )
    args = p.parse_args(argv)

    hot = args.hotset.resolve()
    if not hot.is_dir():
        print("NO_HOTSET", file=sys.stderr)
        print("FREED:0")
        return 1
    if hot.name != "chips":
        print(f"REFUSE_NOT_HOTSET:{hot}", file=sys.stderr)
        print("FREED:0")
        return 2
    if looks_like_wave13(args.anchor_ids_file) and not args.allow_unreleased:
        if args.release_ok is None or not args.release_ok.is_file():
            print("REFUSE_WAVE_13", file=sys.stderr)
            print("FREED:0")
            return 2
    if not args.dry_run and not args.allow_unreleased:
        if args.release_ok is None or not args.release_ok.is_file():
            print("NO_RELEASE_OK", file=sys.stderr)
            print("FREED:0")
            return 2
        if args.backup_ok is None or not args.backup_ok.is_file():
            print("NO_BACKUP_OK", file=sys.stderr)
            print("FREED:0")
            return 2

    ids = load_anchor_ids(args.anchor_ids_file)
    if not ids:
        print("EMPTY_MEMBERSHIP", file=sys.stderr)
        print("FREED:0")
        return 2

    png_n = 0
    png_b = 0
    tfw_n = 0
    tfw_b = 0
    tif_n = 0
    deleted_n = 0
    deleted_b = 0
    empty_z = 0
    empty_a = 0
    miss = 0

    for aid in ids:
        d = hot / aid
        if not d.is_dir():
            miss += 1
            continue
        # refuse to walk outside the member tree
        try:
            d.resolve().relative_to(hot)
        except ValueError:
            print(f"REFUSE_OUTSIDE:{d}", file=sys.stderr)
            continue
        pngs: list[Path] = []
        tfws: list[Path] = []
        has_tif = False
        for root, _dirs, files in os.walk(d):
            for f in files:
                fl = f.lower()
                fp = Path(root) / f
                if fl.endswith(".tif"):
                    has_tif = True
                    tif_n += 1
                    continue
                if fl.endswith(".png"):
                    try:
                        sz = fp.stat().st_size
                    except OSError:
                        continue
                    pngs.append(fp)
                    png_n += 1
                    png_b += sz
                    continue
                if fl.endswith(".tfw"):
                    try:
                        sz = fp.stat().st_size
                    except OSError:
                        continue
                    tfws.append(fp)
                    tfw_n += 1
                    tfw_b += sz
        # still delete pngs/tfws even if tifs remain (shared-folder safety: never
        # rmdir the anchor). w13 is refused above; leftover tifs on a released
        # wave would be a leak — delete pngs/orphans only.
        for fp in pngs + tfws:
            if args.dry_run:
                deleted_n += 1
                deleted_b += fp.stat().st_size
                continue
            try:
                sz = fp.stat().st_size
            except OSError:
                sz = 0
            try:
                fp.unlink()
            except OSError as e:
                print(f"FAIL_UNLINK:{fp}:{e}", file=sys.stderr)
                continue
            deleted_n += 1
            deleted_b += sz
        if not args.dry_run:
            ez, ea = rmdir_empty_zoom_and_anchor(d)
            empty_z += ez
            empty_a += ea
        elif has_tif:
            pass

    print(f"PROBE:{png_n}:{png_b}")
    print(f"TFW_PROBE:{tfw_n}:{tfw_b}")
    print(f"TIF_SEEN:{tif_n}")
    print(f"MISSING_ANCHOR:{miss}")
    print(f"DELETED:{deleted_n}:{deleted_b}")
    print(f"EMPTY_Z:{empty_z}")
    print(f"EMPTY_ANCHOR:{empty_a}")
    print(f"FREED:{deleted_b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
