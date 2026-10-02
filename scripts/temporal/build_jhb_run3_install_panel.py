#!/usr/bin/env python3
"""Expand JHB RUN 3 onto a date x installed x installation-id panel.

Adaptive scoring left most catalog dates unjudged. Under the same
monotonicity rule the published bracket already uses (absent on a date
means absent on every earlier date; present on a date means present on
every later date), those unjudged dates outside a published bracket are
determined. Dates strictly inside the bracket stay blank: filling them
would invent a Gemini verdict the adaptive scan never asked for.

Undated anchors are not extended. Their scored frames stay, and every
other catalog date is blank.

Output is one row per (source_feature_id, capture_date). The date set is
the RUN 3 vintage catalog, plus any scored date or published interval
endpoint the catalog does not list.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BRACKET_PROVIDERS = frozenset(
    {"gehi_merged", "gehi_merged_census", "gehi_merged_leftcensor"}
)
PANEL_FIELDS = (
    "source_feature_id",
    "capture_date",
    "installed",
    "label_source",
    "scored_usable",
    "pv_present_raw",
    "quality_flag",
    "dip_flipped",
    "post_census",
    "catalog_provider",
    "date_provider",
    "on_census_flight",
    "install_interval_start",
    "install_interval_end",
)


def decoder_installed(row: dict[str, str] | None) -> str | None:
    """Published-decoder 0/1 after dip repair, or None if the date was not used."""
    if row is None:
        return None
    if row.get("in_evidence") != "1" or row.get("usable") != "1":
        return None
    if row.get("dip_flipped") == "1":
        return "1"
    present = row.get("pv_present") or ""
    if present in {"0", "1"}:
        return present
    return None


def classify_panel_date(
    capture_date: str,
    *,
    observed: str | None,
    has_bracket: bool,
    left_censored: bool,
    interval_start: str,
    interval_end: str,
    trailing_absent_date: str = "",
) -> tuple[str, str]:
    """Return (installed, label_source). installed is ``0``, ``1``, or blank.

    ``trailing_absent_date`` is an observed absent strictly after the
    published present-side bound. Dates after it are not filled: a trailing
    absent is not treated as panel removal, and it blocks the
    present-forever implication.
    """
    if observed is not None:
        return observed, "observed"
    if not has_bracket:
        return "", "unknown_undated"
    if interval_start and capture_date <= interval_start:
        return "0", "implied_absent"
    if interval_end and capture_date >= interval_end:
        if trailing_absent_date and capture_date > trailing_absent_date:
            return "", "unknown_after_trailing_absent"
        return "1", "implied_present"
    if left_censored or not interval_start:
        return "", "unknown_before_left_censor"
    return "", "unknown_in_interval"


def bracket_conflict(
    capture_date: str,
    observed: str,
    *,
    interval_start: str,
    interval_end: str,
    post_census: bool = False,
    left_censored: bool = False,
) -> str | None:
    if interval_start and capture_date <= interval_start and observed != "0":
        return "present_on_or_before_latest_absent"
    if interval_end and capture_date >= interval_end and observed != "1":
        # The scanner's monotonicity check ignored post-census frames, so a
        # left-censored anchor can still carry one trailing post-census 0.
        if post_census and left_censored and observed == "0":
            return None
        return "absent_on_or_after_earliest_present"
    if (
        interval_start
        and interval_end
        and interval_start < capture_date < interval_end
    ):
        return "decoder_observation_inside_open_interval"
    return None


def _provider_label(providers: set[str]) -> str:
    ordered = [name for name in ("TM", "Wayback") if name in providers]
    extras = sorted(providers - set(ordered))
    return "+".join(ordered + extras)


def _load_flights(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            grid = row["grid_id"].strip()
            raw = (row.get("last_capture_date") or "").strip()
            if grid and raw:
                out[grid] = raw[:10]
    return out


def _load_polygons(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_anchor = {row["source_anchor_id"]: row for row in rows}
    if len(by_anchor) != len(rows):
        raise SystemExit(f"duplicate source_anchor_id in {path}")
    return by_anchor


def _load_observations(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    by_anchor: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            slot = by_anchor[row["source_anchor_id"]]
            date = row["capture_date"]
            if date in slot:
                raise SystemExit(
                    f"duplicate scored date {row['source_anchor_id']} {date}"
                )
            slot[date] = row
    return by_anchor


def _load_catalog(
    path: Path, anchors: set[str] | None = None
) -> dict[str, dict[str, set[str]]]:
    by_anchor: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            anchor_id = row["anchor_id"]
            if anchors is not None and anchor_id not in anchors:
                continue
            by_anchor[anchor_id][row["capture_date"][:10]].add(row.get("provider") or "TM")
    return by_anchor


def build_panel(args: argparse.Namespace) -> dict:
    polygons = _load_polygons(args.polygons)
    observations = _load_observations(args.observations)
    catalog = _load_catalog(args.catalog, getattr(args, "anchors", None))
    flights = _load_flights(args.vexcel_csv)

    missing_catalog = [anchor for anchor in polygons if anchor not in catalog]
    if missing_catalog:
        raise SystemExit(f"{len(missing_catalog)} anchors missing from the vintage catalog")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    installed_counts: Counter[str] = Counter()
    conflicts: list[str] = []
    anchors_with_bracket = 0
    trailing_absent_anchors = 0
    catalog_dates = 0
    extra_dates = 0

    ordered = sorted(polygons.values(), key=lambda row: int(row["source_feature_id"]))
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PANEL_FIELDS)
        writer.writeheader()
        for poly in ordered:
            anchor = poly["source_anchor_id"]
            feature_id = poly["source_feature_id"]
            start = (poly.get("install_interval_start") or "").strip()
            end = (poly.get("install_interval_end") or "").strip()
            provider = (poly.get("date_provider") or "").strip()
            has_bracket = provider in BRACKET_PROVIDERS
            left_censored = poly.get("left_censored") == "1"
            if has_bracket:
                anchors_with_bracket += 1
                if left_censored and start:
                    raise SystemExit(f"{anchor}: left-censored row has an interval start")
                if not left_censored and (not start or not end or start > end):
                    raise SystemExit(
                        f"{anchor}: bracket provider {provider} has start={start!r} end={end!r}"
                    )
                if left_censored and not end:
                    raise SystemExit(f"{anchor}: left-censored row has no interval end")
            elif start or end:
                raise SystemExit(f"{anchor}: undated row still carries an interval")

            flight = flights.get(poly["source_grid"], "")
            scored = observations.get(anchor, {})
            observed_by_date = {
                date: label
                for date, row in scored.items()
                if (label := decoder_installed(row)) is not None
            }
            trailing_absent = ""
            if end:
                later = [date for date, label in observed_by_date.items() if label == "0" and date > end]
                if later:
                    trailing_absent = min(later)
                    trailing_absent_anchors += 1
            dates = set(catalog[anchor])
            catalog_dates += len(dates)
            for extra in list(scored) + [start, end]:
                if extra and extra not in dates:
                    dates.add(extra)
                    extra_dates += 1

            for capture_date in sorted(dates):
                scored_row = scored.get(capture_date)
                observed = observed_by_date.get(capture_date)
                if observed is not None and has_bracket:
                    problem = bracket_conflict(
                        capture_date,
                        observed,
                        interval_start=start,
                        interval_end=end,
                        post_census=bool(scored_row and scored_row.get("post_census") == "1"),
                        left_censored=left_censored,
                    )
                    if problem is not None:
                        conflicts.append(f"{anchor} {capture_date} {problem}")
                        if len(conflicts) >= 20:
                            break
                installed, label_source = classify_panel_date(
                    capture_date,
                    observed=observed,
                    has_bracket=has_bracket,
                    left_censored=left_censored,
                    interval_start=start,
                    interval_end=end,
                    trailing_absent_date=trailing_absent,
                )
                counts[label_source] += 1
                installed_counts[installed or "(blank)"] += 1
                raw = ""
                quality = ""
                dip = ""
                post = ""
                if scored_row is not None:
                    raw = scored_row.get("pv_present") or ""
                    quality = scored_row.get("quality_flag") or ""
                    dip = scored_row.get("dip_flipped") or ""
                    post = scored_row.get("post_census") or ""
                writer.writerow(
                    {
                        "source_feature_id": feature_id,
                        "capture_date": capture_date,
                        "installed": installed,
                        "label_source": label_source,
                        "scored_usable": "1" if observed is not None else "0",
                        "pv_present_raw": raw,
                        "quality_flag": quality,
                        "dip_flipped": dip,
                        "post_census": post,
                        "catalog_provider": _provider_label(catalog[anchor].get(capture_date, set())),
                        "date_provider": provider,
                        "on_census_flight": "1" if flight and capture_date == flight else "0",
                        "install_interval_start": start,
                        "install_interval_end": end,
                    }
                )
            if len(conflicts) >= 20:
                break

    if conflicts:
        for line in conflicts:
            print(line, file=sys.stderr)
        args.output.unlink(missing_ok=True)
        raise SystemExit(
            f"published bracket disagrees with the decoder 0/1 ({len(conflicts)} shown); wrote nothing"
        )

    report = {
        "anchors": len(ordered),
        "anchors_with_published_bracket": anchors_with_bracket,
        "anchors_with_trailing_post_census_absent": trailing_absent_anchors,
        "catalog_dates": catalog_dates,
        "dates_added_beyond_catalog": extra_dates,
        "panel_rows": sum(counts.values()),
        "installed": dict(installed_counts),
        "label_source": dict(counts),
        "output": str(args.output),
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    filled = installed_counts["0"] + installed_counts["1"]
    print(
        f"anchors={len(ordered)} rows={report['panel_rows']} "
        f"installed_filled={filled} blank={installed_counts['(blank)']} -> {args.output}"
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = (
        Path.home()
        / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/deliverable_run3_sequences_2026-09-23"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--polygons",
        type=Path,
        default=root / "jhb_full382_fpcut_install_dated_2026-07-18-run3_with_sequences.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=root / "jhb_run3_presence_observations.csv",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path.home()
        / "zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/gehi_vintage_candidates_full_run3.csv",
    )
    parser.add_argument(
        "--vexcel-csv",
        type=Path,
        default=Path("/home/gao/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"),
    )
    parser.add_argument("--output", type=Path, default=root / "jhb_run3_install_panel.csv")
    parser.add_argument("--report", type=Path, default=root / "install_panel_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build_panel(parse_args(argv))


if __name__ == "__main__":
    main()
