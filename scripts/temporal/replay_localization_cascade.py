#!/usr/bin/env python3
"""R2 skeleton: replay the semantic-localization cascade (PRD §5/§6/§9 item 2,
``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``) over real RUN
3 anchors and emit one ``TargetLocalizationObservation`` JSONL row per
``(anchor_id, capture_date)`` observation.

Cascade contract (§5.2): ``identity`` (trust TFW/Run3 geometry) ->
``phase_correlation`` (coarse translation lock; escalate on a weak lock) ->
``weak_lock`` (SP+LightGlue + RANSAC, bounded translation/similarity only).
Each stage is a plain function ``StageInput -> TargetLocalizationObservation``
so a future R2 iteration can swap/extend stages without touching the CLI or
the join/output plumbing below. This skeleton implements ``identity_stage``
for real; ``phase_correlation_stage`` and ``weak_lock_stage`` are stubs that
raise ``NotImplementedError`` with their return contract spelled out in the
docstring, per the team-lead brief ("本次只做 identity_stage 真实现,phase-
correlation/weak-lock 两级留 stub").

Input: today's R0 manifest (PRD §3.2) is still being built in parallel by
another workstream, so this script joins directly off the two artifacts that
already exist on disk -- ``anchors_all.csv`` (per-anchor nominal chip
geometry) and one or more ``chip_provenance.jsonl`` files (per-
(anchor_id, capture_date) chip provenance, keyed off ``achieved_zoom``/
``chip_path``/``raster_error``). ``--manifest`` accepts a parquet path for
when R0 lands; this skeleton does not yet know that schema, so passing
``--manifest`` today raises ``NotImplementedError`` rather than silently
misreading an unrelated file.

Nothing here mutates or duplicates real data products -- reads only. Output
goes wherever ``--out`` points (default under
``~/zasolar_data/geid_temporal/run3_native_line_2026-07/``, never committed).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from solar_backdating.localization import CASCADE_STAGES, TargetLocalizationObservation

GT_ROOT = Path("~/zasolar_data/geid_temporal").expanduser()
DEFAULT_ANCHORS_CSV = (
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "anchors_v2" / "anchors_all.csv"
)
DEFAULT_PROVENANCE_JSONL = [
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "run3_v2" / "a24" / "chip_provenance.jsonl",
    GT_ROOT / "fullscan_gemini_backdating_2026-07" / "run3_v2" / "a48" / "chip_provenance.jsonl",
]
DEFAULT_OUT_DIR = GT_ROOT / "run3_native_line_2026-07" / "localization_cascade"

# chip_provenance.jsonl statuses that mean "there is a usable raster on disk".
# ``raster_error`` still gets checked per-row (a status can be "downloaded" or
# "skipped_existing" and still carry a raster read error).
_USABLE_STATUSES = frozenset({"downloaded", "skipped_existing"})


# --------------------------------------------------------------------------- #
# Stage input/output contract                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StageInput:
    """Everything a cascade stage needs to produce one
    ``TargetLocalizationObservation``. Deliberately a superset of what
    ``identity_stage`` uses today -- ``chip_path``/``reference_chip_path`` are
    plumbed through now so the two stub stages' signatures don't need to
    change when they grow real bodies.

    ``anchor_bbox_lonlat`` is ``(lon_min, lat_min, lon_max, lat_max)`` -- the
    nominal chip footprint from ``anchors_all.csv``'s ``chip_lon_min`` /
    ``chip_lat_min`` / ``chip_lon_max`` / ``chip_lat_max`` columns. This is a
    proxy for the actual PV polygon (whose vertices live in the source census
    ``.gpkg``, not in this CSV) -- adequate for an identity-trust skeleton,
    but a real R2 registration stage will need the finer polygon threaded in
    via the R0 manifest (§3.2) once that lands.

    ``prior_observation`` carries the previous cascade stage's output so a
    later stage can read e.g. the identity stage's trusted footprint before
    attempting a correction; ``None`` for the first stage run on an
    observation.
    """

    anchor_id: str
    capture_date: date
    anchor_bbox_lonlat: tuple[float, float, float, float]
    chip_path: Path | None
    reference_chip_path: Path | None
    prior_observation: TargetLocalizationObservation | None


StageFn = Callable[[StageInput], TargetLocalizationObservation]


def identity_stage(inp: StageInput) -> TargetLocalizationObservation:
    """§9 item 2 / §5.2 default: trust TFW/Run3 geometry as-is, no correction.

    Always ``building_found=True``, ``roof_plane_matched=True``,
    ``target_localized=True``, ``transform_type="identity"`` with empty
    ``transform_params``. ``projected_target_polygon`` is the anchor's
    nominal chip bbox (see ``StageInput.anchor_bbox_lonlat``) as a closed
    lon/lat ring -- not a correction, just the trusted footprint expressed in
    the schema's polygon field.

    This stage never abstains and never fails -- it is the fallback every
    observation gets before (optionally) being escalated to
    ``phase_correlation_stage``.
    """
    lon_min, lat_min, lon_max, lat_max = inp.anchor_bbox_lonlat
    ring = (
        (lon_min, lat_min),
        (lon_max, lat_min),
        (lon_max, lat_max),
        (lon_min, lat_max),
        (lon_min, lat_min),
    )
    return TargetLocalizationObservation(
        anchor_id=inp.anchor_id,
        capture_date=inp.capture_date,
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="identity",
        transform_params={},
        registration_confidence=1.0,
        shift_uncertainty_m=0.0,
        projected_target_polygon=ring,
        failure_reason="",
        cascade_stage="identity",
        abstain=False,
    )


def phase_correlation_stage(inp: StageInput) -> TargetLocalizationObservation:
    """STUB (§5.2 cascade tier 1). Not implemented in this skeleton.

    Intended contract once implemented: read ``inp.chip_path`` and
    ``inp.reference_chip_path`` (same-domain GEHI latest reliable ``present``
    frame -- never a cross-domain Vexcel template, per §5.2's "跨域 cos 0.31"
    KILL precedent), register with
    ``scripts.temporal.chip_displacement``'s phase-correlation primitives
    (``ShiftResult``: ``dy_px``/``dx_px``/``psr``/``alignment_score``) with PV
    polygon + buffer masked out of the correlation surface (§5.2's
    anti-self-proof rule), and emit a ``TargetLocalizationObservation`` with
    ``cascade_stage="phase_correlation"``:

    * a confident lock (PSR above a floor, offset within
      ``transform_within_bounds``) -> ``transform_type="translation"``,
      ``target_localized=True``, ``abstain=False``;
    * a weak lock (PSR below the floor) -> escalate to
      ``weak_lock_stage`` rather than finalizing here (does not return in
      that case in the real implementation);
    * an out-of-bounds or conflicting lock -> ``abstain=True``,
      ``target_localized=False``,
      ``failure_reason="transform_out_of_bounds"`` or
      ``"transform_conflict"``.

    Raises ``NotImplementedError`` unconditionally today.
    """
    raise NotImplementedError(
        "phase_correlation_stage is a cascade-contract stub (PRD §5.2 tier 1); "
        "not implemented in the R2 skeleton"
    )


def weak_lock_stage(inp: StageInput) -> TargetLocalizationObservation:
    """STUB (§5.2 cascade tier 2, escalation target of a weak
    ``phase_correlation_stage`` lock). Not implemented in this skeleton.

    Intended contract once implemented: SuperPoint+LightGlue keypoint
    matching + RANSAC (ISSUE-24; interfaces to reuse/adapt live in
    ``scripts/temporal/pilot_learned_match_2026-07-09.py`` and
    ``scripts/temporal/probe_weaklock_2026-07-10.py`` -- read for pattern,
    per those files' own "frozen, read-only" convention, not imported
    verbatim), same PV-polygon-and-buffer feature mask as
    ``phase_correlation_stage``, restricted to bounded
    translation/similarity fits (``transform_within_bounds``). ISSUE-24's
    final verdict was GO (91.3% corroborated) but with an 8.7% "dark zone" of
    unrecoverable pairs -- a dark-zone result here must emit
    ``abstain=True``, ``target_localized=False``,
    ``failure_reason="dark_zone"``, never a forced fit.

    Raises ``NotImplementedError`` unconditionally today.
    """
    raise NotImplementedError(
        "weak_lock_stage is a cascade-contract stub (PRD §5.2 tier 2); "
        "not implemented in the R2 skeleton"
    )


STAGE_FUNCS: dict[str, StageFn] = {
    "identity": identity_stage,
    "phase_correlation": phase_correlation_stage,
    "weak_lock": weak_lock_stage,
}


# --------------------------------------------------------------------------- #
# Data loading / join (anchors_all.csv x chip_provenance.jsonl)               #
# --------------------------------------------------------------------------- #


def load_anchor_bboxes(anchors_csv: Path) -> dict[str, tuple[float, float, float, float]]:
    """``anchor_id -> (lon_min, lat_min, lon_max, lat_max)`` from
    ``anchors_all.csv``'s ``chip_lon_min``/``chip_lat_min``/``chip_lon_max``/
    ``chip_lat_max`` columns."""
    out: dict[str, tuple[float, float, float, float]] = {}
    with anchors_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            out[row["anchor_id"]] = (
                float(row["chip_lon_min"]),
                float(row["chip_lat_min"]),
                float(row["chip_lon_max"]),
                float(row["chip_lat_max"]),
            )
    return out


def iter_provenance_observations(paths: list[Path]) -> Iterator[tuple[str, date, Path]]:
    """Yield ``(anchor_id, capture_date, chip_path)`` for every usable
    ``chip_provenance.jsonl`` row across ``paths``, in file order. "Usable"
    means a recognized status, no ``raster_error``, and a parseable
    ``capture_date``. Duplicate ``(anchor_id, capture_date)`` rows (e.g. an
    anchor scanned in both the A24 and A48 provenance files) are yielded once
    each here; the caller dedupes by keeping first-seen."""
    for path in paths:
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("status") not in _USABLE_STATUSES:
                    continue
                if rec.get("raster_error"):
                    continue
                chip_path = rec.get("chip_path")
                if not chip_path:
                    continue
                try:
                    d = date.fromisoformat(str(rec["capture_date"])[:10])
                except (KeyError, ValueError):
                    continue
                yield rec["anchor_id"], d, Path(chip_path)


def build_stage_inputs(
    anchors_csv: Path, provenance_jsonl: list[Path], limit: int | None
) -> list[StageInput]:
    bboxes = load_anchor_bboxes(anchors_csv)
    seen: set[tuple[str, date]] = set()
    inputs: list[StageInput] = []
    for anchor_id, capture_date, chip_path in iter_provenance_observations(provenance_jsonl):
        key = (anchor_id, capture_date)
        if key in seen:
            continue
        bbox = bboxes.get(anchor_id)
        if bbox is None:
            continue  # provenance row for an anchor not in this anchors_all.csv cut
        seen.add(key)
        inputs.append(
            StageInput(
                anchor_id=anchor_id,
                capture_date=capture_date,
                anchor_bbox_lonlat=bbox,
                chip_path=chip_path,
                reference_chip_path=None,  # R2: same-domain reference selection not yet wired
                prior_observation=None,
            )
        )
        if limit is not None and len(inputs) >= limit:
            break
    return inputs


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def run_cascade(
    inputs: list[StageInput], stage_name: str
) -> tuple[list[TargetLocalizationObservation], Counter]:
    if stage_name not in CASCADE_STAGES:
        raise ValueError(f"--stage must be one of {CASCADE_STAGES}, got {stage_name!r}")
    stage_fn = STAGE_FUNCS[stage_name]
    observations: list[TargetLocalizationObservation] = []
    stats: Counter = Counter()
    for inp in inputs:
        stats["attempted"] += 1
        tlo = stage_fn(inp)  # stub stages raise NotImplementedError -- fail loud, not swallowed
        observations.append(tlo)
        stats["cascade_stage:" + tlo.cascade_stage] += 1
        stats["target_localized"] += int(tlo.target_localized)
        stats["abstain"] += int(tlo.abstain)
        stats["building_found"] += int(tlo.building_found)
        stats["roof_plane_matched"] += int(tlo.roof_plane_matched)
    return observations, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS_CSV)
    parser.add_argument(
        "--provenance-jsonl",
        type=Path,
        nargs="+",
        default=DEFAULT_PROVENANCE_JSONL,
        help="One or more chip_provenance.jsonl files (default: RUN 3 a24+a48).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "R0 frozen manifest parquet (PRD §3.2). Not yet implemented in this "
            "skeleton -- passing this raises NotImplementedError rather than "
            "silently falling back to --anchors-csv/--provenance-jsonl."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=CASCADE_STAGES,
        default="identity",
        help="Which single cascade stage to run over every observation (default: identity).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap on observations processed.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR / "observations.jsonl")
    args = parser.parse_args(argv)

    if args.manifest is not None:
        raise NotImplementedError(
            "--manifest (R0 frozen parquet) is not yet implemented in this skeleton; "
            "R0 is being built in parallel -- use --anchors-csv/--provenance-jsonl for now"
        )

    inputs = build_stage_inputs(args.anchors_csv, args.provenance_jsonl, args.limit)
    if not inputs:
        print("no (anchor_id, capture_date) observations joined -- check input paths", file=sys.stderr)
        return 1

    observations, stats = run_cascade(inputs, args.stage)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for tlo in observations:
            f.write(json.dumps(tlo.to_json_record()) + "\n")

    summary: dict[str, Any] = {
        "stage": args.stage,
        "n_observations": len(observations),
        "n_anchors": len({tlo.anchor_id for tlo in observations}),
        **{k: v for k, v in sorted(stats.items())},
    }
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"wrote {len(observations)} observations -> {args.out}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
