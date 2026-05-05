#!/usr/bin/env python3
"""Phase-0 install-interval inference: scan_state.json -> install_intervals.csv.

Each terminal `scan_state.json` produces exactly one row. Status values map to
intervals as follows:

  done_appears
      latest absent + earliest present from merged usable observations.
      install_interval_start = latest_absent_date
      install_interval_end   = earliest_present_date
      install_mid_estimate   = midpoint of the two
      confidence:  high (gap <= 6mo) / medium (gap <= 24mo) / low (gap > 24mo)

  done_installed_during_census
      latest absent in scan + census imagery mid-date as upper bound.
      install_interval_start = latest_absent_date
      install_interval_end   = census_mid_date
      install_mid_estimate   = census_mid_date  (upper bound; no finer signal)
      confidence:  high if latest_absent within 1y of census_mid_date else medium

  done_already_present_before_geid_history
      Open lower bound; earliest scored vintage is the closed upper bound.
      install_interval_start = ""
      install_interval_end   = earliest scored capture_date
      install_mid_estimate   = ""
      confidence: low

  done_ambiguous_*
      No interval emitted. Status preserved verbatim, scan_state.notes copied
      so a human reviewer can investigate.

The legacy presence_timeseries-driven flow has been removed; the legacy
geid_temporal_common.InstallInterval dataclass and `infer_install_interval`
helpers stay only for tests that exercise the old GEID smoke fixtures.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.scan_state import (
    RoundResult,
    ScanState,
    TERMINAL_STATUSES,
    load_scan_state,
)


DEFAULT_SCAN_STATES_DIR = (
    Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states"
)
# --output default resolved at runtime to <scan_states_dir>.parent / install_intervals.csv
# so a custom --scan-states-dir doesn't silently overwrite the JHB smoke output.
DEFAULT_CENSUS_MID_DATE = "2024-06-30"

OUTPUT_FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "status",
    "latest_absent_date",
    "earliest_present_date",
    "install_interval_start",
    "install_interval_end",
    "install_mid_estimate",
    "n_observations",
    "n_absent",
    "n_present",
    "n_unusable",
    "n_rounds",
    "scan_state_path",
    "confidence",
    "notes",
]


@dataclass
class Phase0InstallInterval:
    anchor_id: str
    region_key: str
    grid_id: str
    status: str
    latest_absent_date: str = ""
    earliest_present_date: str = ""
    install_interval_start: str = ""
    install_interval_end: str = ""
    install_mid_estimate: str = ""
    n_observations: int = 0
    n_absent: int = 0
    n_present: int = 0
    n_unusable: int = 0
    n_rounds: int = 0
    scan_state_path: str = ""
    confidence: str = "low"
    notes: str = ""

    def as_row(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in OUTPUT_FIELDS}


def parse_iso(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _midpoint(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=delta // 2)


def _confidence_for_appears(gap_days: int) -> str:
    if gap_days <= 183:
        return "high"
    if gap_days <= 730:
        return "medium"
    return "low"


def _confidence_for_census(latest_absent_to_census_days: int) -> str:
    if latest_absent_to_census_days <= 365:
        return "high"
    return "medium"


def _all_results(state: ScanState) -> list[RoundResult]:
    return [r for rnd in state.rounds for r in rnd.results]


def _usable(results: list[RoundResult]) -> list[RoundResult]:
    return [r for r in results if r.quality_flag == "usable" and r.pv_present is not None]


def infer_one(state: ScanState, *, census_mid_date: date, scan_state_path: Path) -> Phase0InstallInterval:
    all_results = _all_results(state)
    usable = _usable(all_results)
    n_obs = len(all_results)
    n_present = sum(1 for r in usable if r.pv_present)
    n_absent = sum(1 for r in usable if not r.pv_present)
    n_unusable = sum(
        1
        for r in all_results
        if r.quality_flag != "usable" or r.pv_present is None
    )

    base = Phase0InstallInterval(
        anchor_id=state.anchor_id,
        region_key=state.region_key,
        grid_id=state.grid_id,
        status=state.status,
        n_observations=n_obs,
        n_absent=n_absent,
        n_present=n_present,
        n_unusable=n_unusable,
        n_rounds=len(state.rounds),
        scan_state_path=str(scan_state_path),
        notes=state.notes,
    )

    if state.status == "done_appears":
        absent_dates = [r for r in usable if not r.pv_present]
        present_dates = [r for r in usable if r.pv_present]
        if not absent_dates or not present_dates:
            base.confidence = "low"
            base.notes = (
                f"{state.notes} | inconsistent: status=done_appears but no transition pair found"
            ).strip(" |")
            return base
        latest_absent = max(absent_dates, key=lambda r: r.capture_date)
        earliest_present = min(present_dates, key=lambda r: r.capture_date)
        start_d = parse_iso(latest_absent.capture_date)
        end_d = parse_iso(earliest_present.capture_date)
        base.latest_absent_date = latest_absent.capture_date
        base.earliest_present_date = earliest_present.capture_date
        if start_d > end_d:
            base.install_interval_start = ""
            base.install_interval_end = ""
            base.install_mid_estimate = ""
            base.confidence = "low"
            base.notes = (
                f"{state.notes} | inverted_interval: latest_absent {latest_absent.capture_date} "
                f"> earliest_present {earliest_present.capture_date} "
                f"(state machine should classify as done_ambiguous_nonmonotonic; check scan_state)"
            ).strip(" |")
            return base
        gap_days = (end_d - start_d).days
        mid = _midpoint(start_d, end_d)
        base.install_interval_start = latest_absent.capture_date
        base.install_interval_end = earliest_present.capture_date
        base.install_mid_estimate = mid.isoformat()
        base.confidence = _confidence_for_appears(gap_days)
        return base

    if state.status == "done_installed_during_census":
        absent_obs = [r for r in usable if not r.pv_present]
        if not absent_obs:
            base.confidence = "low"
            base.notes = (
                f"{state.notes} | inconsistent: status=done_installed_during_census but no absent observation"
            ).strip(" |")
            return base
        latest_absent = max(absent_obs, key=lambda r: r.capture_date)
        start_d = parse_iso(latest_absent.capture_date)
        gap_days = (census_mid_date - start_d).days
        base.latest_absent_date = latest_absent.capture_date
        base.earliest_present_date = ""
        base.install_interval_start = latest_absent.capture_date
        base.install_interval_end = census_mid_date.isoformat()
        base.install_mid_estimate = census_mid_date.isoformat()
        base.confidence = _confidence_for_census(gap_days)
        if start_d >= census_mid_date:
            # Post-hoc census-GT prior check: each anchor is from channel2_micro T1
            # GT — the census-period imagery is known to have PV. If the latest scan
            # observation is absent at or after census_mid_date, the algorithm is
            # contradicting that prior. Most plausible cause: marker fell on a roof
            # aisle / shadow / wrong segment and missed the PV.
            base.status = "done_ambiguous_marker_missed_pv"
            base.confidence = "low"
            base.install_interval_start = ""
            base.install_interval_end = ""
            base.install_mid_estimate = ""
            base.notes = (
                f"{state.notes} | marker_missed_pv: latest_absent {latest_absent.capture_date} "
                f">= census_mid_date {census_mid_date.isoformat()} contradicts census-GT prior "
                f"(anchor is PV-positive at census per channel2_micro T1 GT). Needs human review."
            ).strip(" |")
        return base

    if state.status == "done_already_present_before_geid_history":
        if not usable:
            base.confidence = "low"
            return base
        earliest_obs = min(usable, key=lambda r: r.capture_date)
        base.earliest_present_date = earliest_obs.capture_date
        base.install_interval_end = earliest_obs.capture_date
        base.confidence = "low"
        return base

    if state.status.startswith("done_ambiguous_"):
        base.confidence = "low"
        return base

    if state.status == "scanning":
        base.confidence = "low"
        base.notes = (f"{state.notes} | scan_in_progress").strip(" |")
        return base

    base.confidence = "low"
    base.notes = (f"{state.notes} | unknown_status={state.status}").strip(" |")
    return base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scan-states-dir", type=Path, default=DEFAULT_SCAN_STATES_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path. Default: <scan-states-dir>/../install_intervals.csv "
        "(co-located with the scan run instead of overwriting the JHB smoke output).",
    )
    parser.add_argument(
        "--census-mid-date",
        type=str,
        default=DEFAULT_CENSUS_MID_DATE,
        help="ISO YYYY-MM-DD upper bound used by status=done_installed_during_census. "
        "Default 2024-06-30 (JHB Vexcel mid-year fallback). "
        "Per-region lookup from regions.yaml lands in Task G.",
    )
    parser.add_argument(
        "--require-terminal",
        action="store_true",
        help="Skip scan_states whose status is not in TERMINAL_STATUSES instead of writing them with a 'scanning' row.",
    )
    parser.add_argument(
        "--allow-load-failures",
        action="store_true",
        help="Continue and write a (possibly partial) CSV even if some scan_state files fail to load. "
        "Default behavior is to abort, which prevents silently dropping rows when a spec_version "
        "mismatch or corrupted state file appears.",
    )
    return parser.parse_args()


def write_intervals(intervals: list[Phase0InstallInterval], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for interval in intervals:
            writer.writerow(interval.as_row())


def main() -> None:
    args = parse_args()
    if not args.scan_states_dir.exists():
        raise SystemExit(f"Scan states dir not found: {args.scan_states_dir}")

    output_path = (
        args.output
        if args.output is not None
        else args.scan_states_dir.parent / "install_intervals.csv"
    )

    try:
        census_mid = parse_iso(args.census_mid_date)
    except ValueError as exc:
        raise SystemExit(f"Invalid --census-mid-date {args.census_mid_date!r}: {exc}")

    state_files = sorted(args.scan_states_dir.glob("*.json"))
    if not state_files:
        raise SystemExit(f"No scan_state JSON files in {args.scan_states_dir}")

    intervals: list[Phase0InstallInterval] = []
    load_failures: list[tuple[Path, str]] = []
    for state_path in state_files:
        try:
            state = load_scan_state(state_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] failed to load {state_path}: {exc}", file=sys.stderr)
            load_failures.append((state_path, str(exc)))
            continue
        if state is None:
            continue
        if args.require_terminal and state.status not in TERMINAL_STATUSES:
            print(f"[SKIP] non-terminal {state.anchor_id}: status={state.status}", file=sys.stderr)
            continue
        interval = infer_one(state, census_mid_date=census_mid, scan_state_path=state_path)
        intervals.append(interval)

    if load_failures and not args.allow_load_failures:
        raise SystemExit(
            f"{len(load_failures)} scan_state file(s) failed to load: "
            f"{[str(p) for p, _ in load_failures]}. "
            f"Aborting to avoid silently dropping rows. Pass --allow-load-failures to override."
        )
    if not intervals:
        raise SystemExit(
            f"Zero install intervals produced from {len(state_files)} scan_state file(s); "
            f"refusing to write empty CSV."
        )

    write_intervals(intervals, output_path)

    by_status: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    for interval in intervals:
        by_status[interval.status] += 1
        by_confidence[interval.confidence] += 1
    print(f"Wrote {len(intervals)} install intervals -> {output_path}")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")
    print("confidence breakdown:")
    for level, count in sorted(by_confidence.items()):
        print(f"  {level}: {count}")


if __name__ == "__main__":
    main()
