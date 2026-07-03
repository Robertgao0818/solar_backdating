#!/usr/bin/env python3
"""Chip lifecycle CLI: stage -> hash -> delete, without ever losing a hash.

ISSUE-13 (docs/replan_v2/ISSUE-13-gehi-availability-cache.md, PRD D14). GEHI
chips (`*.tif`) are a re-derivable cache, not a source of truth — the
production stack downloads far more chips than it needs to keep around
long-term, and disk fills up. But every chip that fed a scoring decision has
to remain *verifiable* after its bytes are gone: `chip_provenance.jsonl`
sidecars (written by `gehi_download.build_chip_provenance`, see
docs/resolution_provenance.md) already carry `chip_sha256` as the join key
into the scoring sidecar. This module formalizes deleting the bytes while
keeping that hash trail intact, plus a companion `deletion_manifest.jsonl`
recording exactly what was removed and when.

There is deliberately no new hashing machinery here — `sha256_file` and the
`CHIP_PROVENANCE_FIELDS` shape are imported from `gehi_download.py`
unchanged; this module is the missing repo-tracked release tool, not a
reimplementation of the hash-provenance contract.

CLI:

    python scripts/temporal/chip_lifecycle.py release --run-dir <dir> [--dry-run] [--force]

Design notes:

- Only `*.tif` chip files are ever deleted. Review PNGs, `*.jsonl` sidecars,
  scan-state JSON, and everything else under `--run-dir` are left alone —
  PNGs are cheap, human-legible review artifacts (worth keeping for audit),
  while `.tif` chips are the actual disk hogs (10s-100s of KB each, times
  millions of anchor/date/zoom combinations).
- A chip missing a provenance record (pre-ISSUE-18 downloads, or a chip whose
  provenance line was lost) gets **backfilled** — hashed and appended as a
  provenance record — *before* deletion, never deleted un-hashed.
- `chip_provenance.jsonl` sidecars can exist at multiple levels under one
  `--run-dir` (e.g. one per repetition in a reliability study). Each chip is
  governed by its *nearest enclosing* sidecar (the sidecar whose parent
  directory is the deepest ancestor of the chip path); a chip with no
  enclosing sidecar falls back to a `chip_provenance.jsonl` created at
  `--run-dir` itself.
- Refuses to run (dry-run or real) if any `scan_states/*.json` file under
  `--run-dir` was modified within the last 30 minutes, unless `--force` — the
  window exists so this tool never races a scan that's still actively writing
  chips.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_download import CHIP_PROVENANCE_FIELDS, sha256_file  # noqa: E402

DEFAULT_REFUSAL_WINDOW_MINUTES = 30.0
DELETION_MANIFEST_FILENAME = "deletion_manifest.jsonl"
PROVENANCE_FILENAME = "chip_provenance.jsonl"

# `<anchor_id>_<capture_date YYYYMMDD>_v<version>.tif`, e.g.
# jhb_full382_..._c0002019_20210730_v296.tif — matches the layout written by
# `gehi_download.py`'s zoom-ladder download (chips_dir/<anchor_id>/z<zoom>/...).
CHIP_FILENAME_RE = re.compile(r"^(?P<anchor_id>.+)_(?P<date>\d{8})_v(?P<version>\d+)\.tif$")
ZOOM_DIR_RE = re.compile(r"^z(?P<zoom>\d+)$")


@dataclass(frozen=True)
class ChipEntry:
    path: Path
    size_bytes: int
    chip_sha256: str | None  # None until hashed (see plan_release)
    has_provenance: bool
    sidecar: Path  # the chip_provenance.jsonl this chip is governed by


@dataclass
class ReleasePlan:
    run_dir: Path
    to_delete: list[ChipEntry] = field(default_factory=list)
    to_backfill: list[ChipEntry] = field(default_factory=list)  # subset of to_delete
    sidecars_touched: set[Path] = field(default_factory=set)

    @property
    def total_size_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.to_delete)

    def describe(self) -> str:
        lines = [
            f"release plan for {self.run_dir}:",
            f"  chips to delete:   {len(self.to_delete)} ({self.total_size_bytes / 1e6:.2f} MB)",
            f"  need backfill:     {len(self.to_backfill)}",
            f"  sidecars touched:  {len(self.sidecars_touched)}",
        ]
        for entry in self.to_backfill:
            lines.append(f"    backfill: {entry.path} -> {entry.sidecar}")
        return "\n".join(lines)


@dataclass
class ReleaseResult:
    plan: ReleasePlan
    dry_run: bool
    deleted: list[Path] = field(default_factory=list)
    backfilled: list[Path] = field(default_factory=list)
    manifests_written: list[Path] = field(default_factory=list)


class RefusalWindowError(RuntimeError):
    """Raised when a scan_states/*.json under run_dir was modified too recently."""


def discover_tif_files(run_dir: Path) -> list[Path]:
    return sorted(p for p in run_dir.rglob("*.tif") if p.is_file())


def discover_provenance_sidecars(run_dir: Path) -> list[Path]:
    return sorted(run_dir.rglob(PROVENANCE_FILENAME))


def discover_scan_state_files(run_dir: Path) -> list[Path]:
    """Return every JSON file that lives directly inside a `scan_states/` dir.

    Matches on the parent directory name rather than a filename substring:
    production scan_state files are named `<anchor_id>.json` with no
    "scan_state" token in the name itself (see `scan_state.state_path_for`).
    Excludes backup directories like `scan_states_failed_backup_*` by exact
    name match.
    """
    return sorted(p for p in run_dir.rglob("*.json") if p.parent.name == "scan_states")


def check_refusal_window(
    run_dir: Path,
    *,
    force: bool,
    window_minutes: float = DEFAULT_REFUSAL_WINDOW_MINUTES,
    now: float | None = None,
) -> Path | None:
    """Return the offending scan_state path if the run is too recent to touch.

    Returns None when it is safe to proceed (nothing recent, or `force=True`).
    Never raises; callers decide whether to raise `RefusalWindowError`.
    """
    if force:
        return None
    now = now if now is not None else time.time()
    cutoff = now - window_minutes * 60.0
    for path in discover_scan_state_files(run_dir):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            return path
    return None


def parse_chip_filename(path: Path) -> dict[str, object]:
    """Best-effort parse of `<anchor_id>_<date>_v<version>.tif` + `z<zoom>/` parent.

    Returns `{}` when the filename doesn't match the expected shape (still
    handled gracefully by the caller — an unparseable chip is backfilled with
    `None` capture_date/version/zoom rather than skipped).
    """
    match = CHIP_FILENAME_RE.match(path.name)
    if not match:
        return {}
    out: dict[str, object] = {
        "anchor_id": match.group("anchor_id"),
        "capture_date": f"{match.group('date')[:4]}-{match.group('date')[4:6]}-{match.group('date')[6:8]}",
        "version": int(match.group("version")),
    }
    zoom_match = ZOOM_DIR_RE.match(path.parent.name)
    if zoom_match:
        out["achieved_zoom"] = int(zoom_match.group("zoom"))
    return out


def nearest_sidecar(chip_path: Path, sidecars: list[Path]) -> Path | None:
    """Return the sidecar whose parent dir is the deepest ancestor of `chip_path`."""
    best: Path | None = None
    best_depth = -1
    for sidecar in sidecars:
        parent = sidecar.parent
        try:
            chip_path.relative_to(parent)
        except ValueError:
            continue
        depth = len(parent.parts)
        if depth > best_depth:
            best = sidecar
            best_depth = depth
    return best


def load_provenance_paths(sidecar: Path) -> set[str]:
    """Return the set of resolved `chip_path` strings already recorded in `sidecar`."""
    known: set[str] = set()
    if not sidecar.exists():
        return known
    with sidecar.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            chip_path = record.get("chip_path")
            if not chip_path:
                continue
            try:
                known.add(str(Path(chip_path).resolve()))
            except OSError:
                known.add(str(chip_path))
    return known


def build_backfill_record(path: Path, chip_sha256: str) -> dict[str, object]:
    """Build a `CHIP_PROVENANCE_FIELDS`-shaped record for a chip with no provenance line.

    Raster geometry fields are left `None` (the point is to preserve the hash
    before deletion, not to re-run the full `build_chip_provenance` raster
    read) — `raster_error` documents why.
    """
    parsed = parse_chip_filename(path)
    record: dict[str, object] = {field_name: None for field_name in CHIP_PROVENANCE_FIELDS}
    record.update(
        {
            "anchor_id": parsed.get("anchor_id"),
            "capture_date": parsed.get("capture_date"),
            "version": parsed.get("version"),
            "provider": None,
            "requested_zoom_ladder": [],
            "achieved_zoom": parsed.get("achieved_zoom"),
            "status": "backfilled_by_chip_lifecycle",
            "chip_path": str(path),
            "chip_sha256": chip_sha256,
            "raster_error": "backfilled by chip_lifecycle.py release (no provenance sidecar line found)",
        }
    )
    return record


def plan_release(run_dir: Path) -> ReleasePlan:
    """Build the release plan: which chips need backfill, which get deleted."""
    tif_files = discover_tif_files(run_dir)
    sidecars = discover_provenance_sidecars(run_dir)
    default_sidecar = run_dir / PROVENANCE_FILENAME

    plan = ReleasePlan(run_dir=run_dir)
    # Cache each sidecar's known chip_path set once (sidecars can be large).
    known_paths_by_sidecar: dict[Path, set[str]] = {}

    for tif_path in tif_files:
        sidecar = nearest_sidecar(tif_path, sidecars) or default_sidecar
        if sidecar not in known_paths_by_sidecar:
            known_paths_by_sidecar[sidecar] = load_provenance_paths(sidecar)
        resolved = str(tif_path.resolve())
        has_provenance = resolved in known_paths_by_sidecar[sidecar]
        size_bytes = tif_path.stat().st_size
        entry = ChipEntry(
            path=tif_path,
            size_bytes=size_bytes,
            chip_sha256=None,
            has_provenance=has_provenance,
            sidecar=sidecar,
        )
        plan.to_delete.append(entry)
        plan.sidecars_touched.add(sidecar)
        if not has_provenance:
            plan.to_backfill.append(entry)
    return plan


def execute_release(plan: ReleasePlan, *, dry_run: bool) -> ReleaseResult:
    """Backfill missing provenance, write deletion manifests, then delete chips.

    Order matters and is the whole point of this function: every chip is
    hashed and its hash is durably on disk (in a provenance sidecar AND a
    deletion manifest) before the `.tif` bytes are removed.
    """
    result = ReleaseResult(plan=plan, dry_run=dry_run)
    if dry_run:
        return result

    manifest_records: dict[Path, list[dict[str, object]]] = {sc: [] for sc in plan.sidecars_touched}
    backfill_records: dict[Path, list[dict[str, object]]] = {sc: [] for sc in plan.sidecars_touched}

    now_iso = datetime.now(timezone.utc).isoformat()
    for entry in plan.to_delete:
        chip_sha256 = sha256_file(entry.path)
        if not entry.has_provenance:
            backfill_records[entry.sidecar].append(build_backfill_record(entry.path, chip_sha256))
            result.backfilled.append(entry.path)
        manifest_records[entry.sidecar].append(
            {
                "path": str(entry.path),
                "chip_sha256": chip_sha256,
                "size_bytes": entry.size_bytes,
                "deleted_at": now_iso,
            }
        )

    # Write backfill records first (append to the provenance sidecar itself),
    # then the deletion manifest, then actually unlink — hashes are on disk in
    # two places before any byte of chip data is removed.
    for sidecar, records in backfill_records.items():
        if not records:
            continue
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with sidecar.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    for sidecar, records in manifest_records.items():
        if not records:
            continue
        manifest_path = sidecar.parent / DELETION_MANIFEST_FILENAME
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        result.manifests_written.append(manifest_path)

    for entry in plan.to_delete:
        entry.path.unlink(missing_ok=True)
        result.deleted.append(entry.path)

    return result


def cmd_release(run_dir: Path, *, dry_run: bool, force: bool) -> int:
    if not run_dir.exists():
        print(f"[chip_lifecycle] run-dir not found: {run_dir}", file=sys.stderr)
        return 1
    offending = check_refusal_window(run_dir, force=force)
    if offending is not None:
        print(
            f"[chip_lifecycle] refusing: {offending} was modified within the last "
            f"{DEFAULT_REFUSAL_WINDOW_MINUTES:.0f} minutes (looks like an active scan). "
            "Pass --force to override.",
            file=sys.stderr,
        )
        return 1

    plan = plan_release(run_dir)
    print(plan.describe())
    if dry_run:
        print("[chip_lifecycle] --dry-run: no files modified or deleted.")
        return 0

    result = execute_release(plan, dry_run=False)
    print(
        f"[chip_lifecycle] backfilled {len(result.backfilled)} provenance record(s), "
        f"wrote {len(result.manifests_written)} deletion manifest(s), "
        f"deleted {len(result.deleted)} chip file(s)."
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    release = subparsers.add_parser("release", help="Hash-backfill, manifest, and delete chip .tif files.")
    release.add_argument("--run-dir", type=Path, required=True)
    release.add_argument("--dry-run", action="store_true", help="Print the plan; touch nothing on disk.")
    release.add_argument(
        "--force",
        action="store_true",
        help="Bypass the 30-minute active-scan refusal window (see check_refusal_window).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "release":
        raise SystemExit(cmd_release(args.run_dir, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
