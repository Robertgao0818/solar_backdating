#!/usr/bin/env python3
"""Attach RUN 3 raw presence sequences to the frozen JHB 2026-07-18 deliverable.

The econ handover CSV/GPKG stores only the collapsed install bracket. The
per-frame Gemini verdicts stay in ``run3_v2/{a24,a48}/scan_states``. This
script copies the frozen polygon table and adds the aligned raw sequence,
plus a long observation table (one row per scored frame).

Token columns are pipe-separated and share one chronological order
(capture_date, round_id, chip_index). ``seq_present`` is the raw verdict
before dip repair: ``1`` present, ``0`` absent, ``.`` unscored.

The 2026-07-18 interval file was built before ``infer_install_dates``
learned to drop post-census frames (commit 2edf38d, 2026-08-13). Published
RUN 3 evidence is every non-reference frame, including the post-census
reference frame. ``seq_evidence`` follows that July rule. ``seq_post_census``
is 1 when ``capture_date >= census_date``, so a robustness check can drop
those frames and rerun the later rule. ``seq_usable`` is 1 only for
``quality_flag == usable`` with a non-null verdict. ``seq_dip_flipped``
marks raw absents flipped to present by dip repair under the July evidence
set.

The frozen deliverable is not modified. Replay of today's
``infer_install_dates`` with the census-date fence turned off must match
``install_intervals_all.csv`` before any output is written.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import fiona
import pandas as pd
import pyogrio

from scripts.temporal.infer_install_dates import (
    DEFAULT_CENSUS_MID_DATE,
    INTERVAL_VALUE_FIELDS,
    apply_dip_repair,
    infer_one,
    load_vexcel_capture_dates,
    parse_iso,
)
from scripts.temporal.scan_state import ScanState, load_scan_state

GEOM_LAYER = "solar_install_dated"
OBS_LAYER = "presence_observations"
TOKEN_SEP = "|"
REPLAY_FIELDS = tuple(
    field for field in INTERVAL_VALUE_FIELDS if field not in {"notes", "scan_state_path"}
)

SEQ_FIELDS = (
    "seq_census_date",
    "seq_dates",
    "seq_present",
    "seq_quality",
    "seq_provider",
    "seq_evidence",
    "seq_post_census",
    "seq_usable",
    "seq_dip_flipped",
    "seq_n",
    "seq_n_decoder",
    "seq_n_absent_decoder",
    "seq_n_present_decoder",
    "seq_n_unusable_decoder",
    "seq_n_dip_flipped",
)

OBS_FIELDS = (
    "source_feature_id",
    "source_anchor_id",
    "lane",
    "source_grid",
    "obs_order",
    "round_id",
    "round_type",
    "chip_index",
    "capture_date",
    "pv_present",
    "quality_flag",
    "provider",
    "decision_source",
    "confidence",
    "in_evidence",
    "post_census",
    "usable",
    "dip_flipped",
    "census_date",
)


@dataclass(frozen=True)
class SequenceExport:
    summary: dict[str, object]
    observations: list[dict[str, object]]


def present_token(value: bool | None) -> str:
    if value is True:
        return "1"
    if value is False:
        return "0"
    if value is None:
        return "."
    raise TypeError(f"pv_present must be bool or None, got {value!r}")


def _july_evidence_state(state: ScanState) -> ScanState:
    """Copy with the 2026-08-13 census-date fence turned off.

    ``_evidence_results`` skips ``capture_date >= census_date`` only when
    ``census_date`` is set. RUN 3 intervals were inferred on 2026-07-18,
    before that fence existed, so every non-reference frame was evidence.
    """
    copied = copy.deepcopy(state)
    copied.census_date = None
    return copied


def _published_evidence(reference_only: bool) -> bool:
    return not reference_only


def _post_census(state: ScanState, capture_date: str) -> bool:
    cutoff = (state.census_date or "").strip()[:10]
    return bool(cutoff) and capture_date[:10] >= cutoff


def sequence_from_state(state: ScanState, *, census_mid_date: date) -> SequenceExport:
    """Raw chronological sequence plus the July 18 dip-repair mask.

    Present tokens are read from the untouched scan state. Dip repair runs
    on a copy whose census-date fence is off, matching the interval build.
    """
    _repaired, flipped_dates = apply_dip_repair(
        _july_evidence_state(state),
        census_mid_date=census_mid_date,
        flank_min_confidence=0.5,
    )
    flipped = set(flipped_dates)
    rows: list[tuple[str, int, int, dict[str, object]]] = []
    for rnd in state.rounds:
        for result in rnd.results:
            capture = (result.capture_date or "")[:10]
            if len(capture) != 10:
                raise ValueError(
                    f"{state.anchor_id}: bad capture_date {result.capture_date!r}"
                )
            evidence = _published_evidence(bool(result.reference_only))
            post_census = _post_census(state, capture)
            usable = result.quality_flag == "usable" and result.pv_present is not None
            dip = int(capture in flipped and result.pv_present is False)
            rows.append(
                (
                    capture,
                    int(rnd.round_id),
                    int(result.chip_index),
                    {
                        "round_id": int(rnd.round_id),
                        "round_type": rnd.round_type,
                        "chip_index": int(result.chip_index),
                        "capture_date": capture,
                        "pv_present": "" if result.pv_present is None else str(int(result.pv_present)),
                        "present_token": present_token(result.pv_present),
                        "quality_flag": result.quality_flag,
                        "provider": result.provider or "TM",
                        "decision_source": result.decision_source,
                        "confidence": "" if result.confidence is None else str(result.confidence),
                        "in_evidence": int(evidence),
                        "post_census": int(post_census),
                        "usable": int(usable),
                        "dip_flipped": dip,
                        "census_date": (state.census_date or "")[:10],
                    },
                )
            )
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    dates = [item[0] for item in rows]
    if len(dates) != len(set(dates)):
        raise ValueError(f"{state.anchor_id}: duplicate capture_date in scan state")
    obs = [item[3] for item in rows]
    for order, item in enumerate(obs):
        item["obs_order"] = order

    def _join(key: str) -> str:
        return TOKEN_SEP.join(str(item[key] if key != "pv_present" else item["present_token"]) for item in obs)

    decoder = [item for item in obs if item["in_evidence"] == 1 and item["usable"] == 1]
    # Decoder counts are post dip-repair, matching install_intervals_*.csv.
    n_dip = sum(int(item["dip_flipped"]) for item in obs)
    n_present = sum(
        1
        for item in decoder
        if item["present_token"] == "1" or item["dip_flipped"] == 1
    )
    n_absent = sum(
        1
        for item in decoder
        if item["present_token"] == "0" and item["dip_flipped"] == 0
    )
    n_decoder = sum(1 for item in obs if item["in_evidence"] == 1)
    n_unusable = n_decoder - len(decoder)
    summary = {
        "seq_census_date": (state.census_date or "")[:10],
        "seq_dates": _join("capture_date"),
        "seq_present": _join("pv_present"),
        "seq_quality": _join("quality_flag"),
        "seq_provider": _join("provider"),
        "seq_evidence": _join("in_evidence"),
        "seq_post_census": _join("post_census"),
        "seq_usable": _join("usable"),
        "seq_dip_flipped": _join("dip_flipped"),
        "seq_n": len(obs),
        "seq_n_decoder": n_decoder,
        "seq_n_absent_decoder": n_absent,
        "seq_n_present_decoder": n_present,
        "seq_n_unusable_decoder": n_unusable,
        "seq_n_dip_flipped": n_dip,
    }
    _check_token_widths(state.anchor_id, summary)
    return SequenceExport(summary=summary, observations=obs)


def _check_token_widths(anchor_id: str, summary: dict[str, object]) -> None:
    widths = {
        key: str(summary[key]).count(TOKEN_SEP) + (1 if summary[key] else 0)
        for key in (
            "seq_dates",
            "seq_present",
            "seq_quality",
            "seq_provider",
            "seq_evidence",
            "seq_post_census",
            "seq_usable",
            "seq_dip_flipped",
        )
    }
    if len(set(widths.values())) != 1:
        raise ValueError(f"{anchor_id}: token columns diverged {widths}")
    if widths["seq_dates"] != int(summary["seq_n"]):
        raise ValueError(f"{anchor_id}: seq_n does not match token count")


def _replay_mismatches(
    got: dict[str, object],
    want: dict[str, str],
) -> list[str]:
    bad: list[str] = []
    for field in REPLAY_FIELDS:
        if str(got[field]) != str(want.get(field, "")):
            bad.append(field)
    return bad


def _load_intervals(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = {row["anchor_id"]: row for row in rows}
    if len(out) != len(rows):
        raise SystemExit(f"duplicate anchor_id in {path}")
    return out


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_gpkg(
    src_gpkg: Path,
    dst_gpkg: Path,
    seq_by_id: dict[int, dict[str, object]],
    observations: list[dict[str, object]],
) -> None:
    if dst_gpkg.exists():
        dst_gpkg.unlink()
    with fiona.open(src_gpkg, layer=GEOM_LAYER) as src:
        schema = {
            "geometry": src.schema["geometry"],
            "properties": dict(src.schema["properties"]),
        }
        for key in SEQ_FIELDS:
            schema["properties"][key] = "int" if key.startswith("seq_n") else "str"
        with fiona.open(
            dst_gpkg,
            "w",
            driver="GPKG",
            crs=src.crs,
            schema=schema,
            layer=GEOM_LAYER,
        ) as dst:
            n = 0
            for feat in src:
                props = dict(feat["properties"])
                sid = int(props["source_feature_id"])
                extra = seq_by_id.get(sid)
                if extra is None:
                    raise SystemExit(f"gpkg source_feature_id {sid} has no sequence")
                props.update(extra)
                dst.write({"geometry": feat["geometry"], "properties": props})
                n += 1
            if n != len(seq_by_id):
                raise SystemExit(f"gpkg rows {n} != sequence rows {len(seq_by_id)}")
    frame = pd.DataFrame(observations, columns=list(OBS_FIELDS))
    pyogrio.write_dataframe(frame, dst_gpkg, layer=OBS_LAYER, append=True)


def export(args: argparse.Namespace) -> dict:
    deliverable_csv = args.deliverable_dir / (
        "jhb_full382_fpcut_install_dated_2026-07-18-run3.csv"
    )
    deliverable_gpkg = args.deliverable_dir / (
        "jhb_full382_fpcut_install_dated_2026-07-18-run3.gpkg"
    )
    intervals = _load_intervals(args.intervals)
    vexcel = load_vexcel_capture_dates(args.vexcel_csv)
    census_mid = parse_iso(args.census_mid_date)

    with deliverable_csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        base_fields = list(reader.fieldnames or [])
        polygons = list(reader)
    if len(polygons) != len(intervals):
        raise SystemExit(
            f"deliverable rows {len(polygons)} != interval rows {len(intervals)}"
        )

    seq_by_id: dict[int, dict[str, object]] = {}
    observations: list[dict[str, object]] = []
    mismatches: list[str] = []
    decision_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    failed_but_usable = 0
    for poly in polygons:
        sid = int(poly["source_feature_id"])
        anchor = poly["source_anchor_id"]
        lane = poly["lane"]
        state_path = args.run_root / lane / "scan_states" / f"{anchor}.json"
        state = load_scan_state(state_path)
        if state is None:
            raise SystemExit(f"missing scan state {state_path}")
        if state.anchor_id != anchor:
            raise SystemExit(f"{state_path} anchor_id {state.anchor_id} != {anchor}")
        exported = sequence_from_state(state, census_mid_date=census_mid)
        seq_by_id[sid] = exported.summary
        poly.update(exported.summary)
        for item in exported.observations:
            decision_counts[str(item["decision_source"])] += 1
            quality_counts[str(item["quality_flag"])] += 1
            if item["decision_source"] == "gemini_failed" and item["usable"] == 1:
                failed_but_usable += 1
            observations.append(
                {
                    "source_feature_id": sid,
                    "source_anchor_id": anchor,
                    "lane": lane,
                    "source_grid": poly["source_grid"],
                    **item,
                }
            )
        repaired, _flipped = apply_dip_repair(
            _july_evidence_state(state),
            census_mid_date=census_mid,
            flank_min_confidence=0.5,
        )
        replay = infer_one(
            repaired,
            census_mid_date=census_mid,
            scan_state_path=state_path,
            vexcel_capture_by_grid=vexcel,
        ).as_row()
        want = intervals.get(anchor)
        if want is None:
            mismatches.append(f"{anchor}: not in intervals")
            continue
        bad = _replay_mismatches(replay, want)
        if bad:
            shown = {field: (replay[field], want.get(field)) for field in bad}
            mismatches.append(f"{anchor}: {shown}")
            if len(mismatches) >= 20:
                break

    if mismatches:
        for line in mismatches:
            print(line, file=sys.stderr)
        raise SystemExit(
            f"replay against {args.intervals} failed "
            f"({len(mismatches)} shown, cap 20); wrote nothing"
        )
    if failed_but_usable:
        raise SystemExit(
            f"{failed_but_usable} gemini_failed rows are marked usable; refusing to write"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.output_dir / (
        "jhb_full382_fpcut_install_dated_2026-07-18-run3_with_sequences.csv"
    )
    out_obs = args.output_dir / "jhb_run3_presence_observations.csv"
    out_gpkg = args.output_dir / (
        "jhb_full382_fpcut_install_dated_2026-07-18-run3_with_sequences.gpkg"
    )
    _write_csv(out_csv, base_fields + list(SEQ_FIELDS), polygons)
    _write_csv(out_obs, list(OBS_FIELDS), observations)
    _write_gpkg(deliverable_gpkg, out_gpkg, seq_by_id, observations)

    report = {
        "polygons": len(polygons),
        "observations": len(observations),
        "decision_source": dict(decision_counts),
        "quality_flag": dict(quality_counts),
        "seq_n": dict(Counter(int(row["seq_n"]) for row in polygons)),
        "dip_flipped_frames": sum(int(row["seq_n_dip_flipped"]) for row in polygons),
        "anchors_with_dip": sum(1 for row in polygons if int(row["seq_n_dip_flipped"]) > 0),
        "replay_fields": list(REPLAY_FIELDS),
        "replay_matched_anchors": len(polygons),
        "outputs": {
            "csv": str(out_csv),
            "observations_csv": str(out_obs),
            "gpkg": str(out_gpkg),
        },
    }
    report_path = args.output_dir / "sequence_export_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"polygons={report['polygons']} observations={report['observations']} "
        f"dip_flipped_frames={report['dip_flipped_frames']} -> {args.output_dir}"
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliverable-dir", type=Path, default=root / "deliverable_run3_2026-07-18")
    parser.add_argument("--run-root", type=Path, default=root / "run3_v2")
    parser.add_argument(
        "--intervals",
        type=Path,
        default=root / "run3_v2/intervals/install_intervals_all.csv",
    )
    parser.add_argument(
        "--vexcel-csv",
        type=Path,
        default=Path("/home/gao/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"),
    )
    parser.add_argument("--census-mid-date", default=DEFAULT_CENSUS_MID_DATE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "deliverable_run3_sequences_2026-09-23",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    export(parse_args(argv))


if __name__ == "__main__":
    main()
