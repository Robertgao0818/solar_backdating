#!/usr/bin/env python3
"""Merge Cape Town scan-state scopes into one flat ``<anchor_id>.json`` directory.

Production scoring wrote states into several immutable roots (primary a24/a48,
CT-08 canary originals, and the two-anchor gate_retry2 rescue). The 2026-08-03
final scoring audit loaded **5 scopes / 21,460 files / 21,453 unique anchors**.
``infer_install_dates.py`` takes a single ``--scan-states-dir``, so replay needs
this merge.

Earlier ``--scan-states-dir`` wins (frozen precedence). Identical bytes are
deduped. Files that differ only in ``started_at`` / ``updated_at`` are treated
as identical (same rule as ``verdict_store`` replay-diff). Any other byte
conflict fails unless ``--allow-supersede`` is set, in which case the winner
is kept and every loser is recorded in the manifest.

This script never calls GEHI or Gemini. It copies JSON only — not chips.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.scoring_provenance import canonical_hash
from scripts.temporal.verdict_store import REPLAY_IGNORED_FIELDS


# Reconstructs ``ct_full_reconciliation_final_scoring_user60_20260803.json``:
# 5 ``--scan-states-dir`` values, 21,460 files, 21,453 unique anchor_ids.
# Order is frozen precedence (earlier wins). retry2 precedes the canary
# originals so the two rescued orchestrator-error anchors keep the retry.
CT52_FROZEN_SCOPE_RELS: tuple[str, ...] = (
    "primary/scan_states/a24",
    "primary/scan_states/a48",
    "canary/gate_retry2/scan_states/retry2",
    "canary/gate_retry2/scan_states/original_A24",
    "canary/gate_retry2/scan_states/original_A48",
)
CT52_FROZEN_EXPECTED_COUNT = 21_453
SCHEMA_VERSION = "ct_scan_state_merge_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_state_obj(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_state_digest(path: Path) -> str:
    payload = _load_state_obj(path)
    if isinstance(payload, dict):
        for field in REPLAY_IGNORED_FIELDS:
            payload.pop(field, None)
    return canonical_hash(payload)


def list_state_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"scan-states dir not found: {directory}")
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def frozen_ct52_scopes(production_root: Path) -> list[Path]:
    scopes = [production_root / rel for rel in CT52_FROZEN_SCOPE_RELS]
    missing = [str(path) for path in scopes if not path.is_dir()]
    if missing:
        raise FileNotFoundError(
            "frozen CT-52 scan-state scopes missing under "
            f"{production_root}: {missing}"
        )
    return scopes


@dataclass(frozen=True)
class Candidate:
    path: Path
    scope_index: int
    scope_label: str
    sha256: str


@dataclass
class MergeResult:
    n_input_files: int
    n_unique_anchors: int
    n_copied: int
    identical_dups: list[dict[str, str]]
    timestamp_only_dups: list[dict[str, str]]
    superseded: list[dict[str, str]]
    copied: list[dict[str, str]]


def merge_scan_states(
    scopes: list[Path],
    output_dir: Path,
    *,
    allow_supersede: bool = False,
    expected_count: int | None = None,
    hardlink: bool = False,
    dry_run: bool = False,
) -> MergeResult:
    if not scopes:
        raise ValueError("at least one --scan-states-dir is required")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to write into non-empty output dir: {output_dir}")

    by_anchor: dict[str, list[Candidate]] = defaultdict(list)
    for scope_index, scope in enumerate(scopes):
        label = str(scope)
        for path in list_state_files(scope):
            by_anchor[path.stem].append(
                Candidate(
                    path=path,
                    scope_index=scope_index,
                    scope_label=label,
                    sha256=sha256_file(path),
                )
            )

    identical_dups: list[dict[str, str]] = []
    timestamp_only_dups: list[dict[str, str]] = []
    superseded: list[dict[str, str]] = []
    copied: list[dict[str, str]] = []
    conflicts: list[str] = []

    for anchor_id, candidates in sorted(by_anchor.items()):
        winner = min(candidates, key=lambda item: (item.scope_index, item.path.as_posix()))
        winner_canonical = None
        for candidate in candidates:
            if candidate.path.resolve() == winner.path.resolve():
                continue
            row = {
                "anchor_id": anchor_id,
                "winner_scope": winner.scope_label,
                "winner_path": str(winner.path),
                "winner_sha256": winner.sha256,
                "loser_scope": candidate.scope_label,
                "loser_path": str(candidate.path),
                "loser_sha256": candidate.sha256,
            }
            if candidate.sha256 == winner.sha256:
                identical_dups.append(row)
                continue
            if winner_canonical is None:
                winner_canonical = canonical_state_digest(winner.path)
            loser_canonical = canonical_state_digest(candidate.path)
            if loser_canonical == winner_canonical:
                row["conflict_class"] = "timestamp_only"
                timestamp_only_dups.append(row)
                continue
            row["conflict_class"] = "canonical_bytes"
            if allow_supersede:
                superseded.append(row)
                continue
            conflicts.append(
                f"{anchor_id}: {winner.path} ({winner.sha256[:12]}) vs "
                f"{candidate.path} ({candidate.sha256[:12]})"
            )
        copied.append(
            {
                "anchor_id": anchor_id,
                "source_path": str(winner.path),
                "source_scope": winner.scope_label,
                "sha256": winner.sha256,
                "n_candidates": str(len(candidates)),
            }
        )

    if conflicts:
        preview = "; ".join(conflicts[:8])
        raise ValueError(
            f"{len(conflicts)} canonical-byte collision(s) under frozen precedence. "
            f"Pass --allow-supersede to keep the earlier --scan-states-dir and record "
            f"losers in the manifest. Examples: {preview}"
        )

    if expected_count is not None and len(by_anchor) != expected_count:
        raise ValueError(
            f"unique merged anchors {len(by_anchor)} != --expected-count {expected_count}"
        )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        for row in copied:
            source = Path(row["source_path"])
            dest = output_dir / f"{row['anchor_id']}.json"
            if dest.exists() or dest.is_symlink():
                raise FileExistsError(f"refusing to overwrite {dest}")
            if hardlink:
                os.link(source, dest)
            else:
                shutil.copy2(source, dest)

    return MergeResult(
        n_input_files=sum(len(items) for items in by_anchor.values()),
        n_unique_anchors=len(by_anchor),
        n_copied=len(copied),
        identical_dups=identical_dups,
        timestamp_only_dups=timestamp_only_dups,
        superseded=superseded,
        copied=copied,
    )


def result_to_manifest(
    result: MergeResult,
    *,
    scopes: list[Path],
    output_dir: Path,
    allow_supersede: bool,
    expected_count: int | None,
    hardlink: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scopes": [str(path) for path in scopes],
        "output_dir": str(output_dir),
        "allow_supersede": allow_supersede,
        "hardlink": hardlink,
        "expected_count": expected_count,
        "n_input_files": result.n_input_files,
        "n_unique_anchors": result.n_unique_anchors,
        "n_copied": result.n_copied,
        "n_identical_dups": len(result.identical_dups),
        "n_timestamp_only_dups": len(result.timestamp_only_dups),
        "n_superseded": len(result.superseded),
        "identical_dups": result.identical_dups,
        "timestamp_only_dups": result.timestamp_only_dups,
        "superseded": result.superseded,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--scan-states-dir",
        type=Path,
        action="append",
        default=[],
        help="Scan-state directory (repeatable). Earlier flags win on anchor_id collision.",
    )
    parser.add_argument(
        "--production-root",
        type=Path,
        default=None,
        help="CT production_lite root. Expands the frozen 5-scope list when "
        "--scan-states-dir is omitted.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Empty dest dir for <anchor_id>.json copies.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Merge manifest JSON. Default: <output>/../merge_manifest.json",
    )
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument(
        "--allow-supersede",
        action="store_true",
        help="Keep the higher-precedence file when canonical payload bytes differ "
        "(needed for gate_retry2 retry2 vs original). Default: fail loud.",
    )
    parser.add_argument("--hardlink", action="store_true", help="Hardlink instead of copy (same filesystem).")
    parser.add_argument("--dry-run", action="store_true", help="Write the manifest only; do not copy files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scan_states_dir:
        scopes = [path.expanduser() for path in args.scan_states_dir]
    elif args.production_root is not None:
        scopes = frozen_ct52_scopes(args.production_root.expanduser())
    else:
        raise SystemExit("pass --scan-states-dir (repeatable) or --production-root")

    expected = args.expected_count
    if expected is None and args.production_root is not None and not args.scan_states_dir:
        expected = CT52_FROZEN_EXPECTED_COUNT

    output_dir = args.output.expanduser()
    manifest_path = (
        args.manifest.expanduser()
        if args.manifest is not None
        else output_dir.parent / "merge_manifest.json"
    )
    try:
        result = merge_scan_states(
            scopes,
            output_dir,
            allow_supersede=args.allow_supersede,
            expected_count=expected,
            hardlink=args.hardlink,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    manifest = result_to_manifest(
        result,
        scopes=scopes,
        output_dir=output_dir,
        allow_supersede=args.allow_supersede,
        expected_count=expected,
        hardlink=args.hardlink,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    selection_path = manifest_path.with_name("merge_selection.csv")
    with selection_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["anchor_id", "source_path", "source_scope", "sha256", "n_candidates"],
        )
        writer.writeheader()
        writer.writerows(result.copied)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"Wrote {result.n_copied} scan states -> {output_dir}")
    print(f"Wrote merge manifest -> {manifest_path}")
    print(f"Wrote selection roster -> {selection_path}")


if __name__ == "__main__":
    main()
