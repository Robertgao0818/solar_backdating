#!/usr/bin/env python3
"""Stratified Gemini-3.5 visual recovery pilot for CoJ gate failures."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import run_r4_shortgap_adjudication as v3
from scripts.temporal import run_r4_shortgap_window_recovery as v5
from scripts.validation.gemini_solar_image_review import RateLimiter
from scripts.validation.issue29_coj_external_robustness import client_config, render_crop

DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
COJ_ROOT = DATA_ROOT / "coj_reference_audit_v2_2026-07"
COJ_FULL = COJ_ROOT / "full"
R0_MANIFEST = DATA_ROOT / "run3_native_line_2026-07/r0_manifest_v1/manifest.parquet"
V4_ROOT = DATA_ROOT / "r4_shortgap_window_review_v4"
V5_ROOT = DATA_ROOT / "r4_shortgap_window_recovery_v5"
RUN_ID = "coj_gate_failure_recovery_pilot_v1"
MODEL_ALIAS = "gemini-3.5-flash-low"
EXPECTED_SAMPLE = 426
EXPECTED_CENSUS = 120
EXPECTED_DRAW = 306
SAMPLE_SEED = "coj-gate-failure-recovery-pilot-v1@2026-07-24"
RULE_VERSION = "coj_gate_failure_visual_recovery_v1@2026-07-24"
MAX_OUTPUT_TOKENS = 8192
MAX_ATTEMPTS = 6
DEFAULT_WORKERS = 30
DEFAULT_QPS = 6.0

STATES = ("present", "absent", "unclear")
TARGET_MATCH = ("same_target", "unclear")
SEQUENCE_STATUSES = v5.SEQUENCE_STATUSES
FRAME_IDS = v5.FRAME_IDS
FIRST_PV_FRAMES = v5.FIRST_PV_FRAMES
CONSENSUS_FIELDS = (
    "target_match", "coj_2019_state", "coj_2023_state",
    "sequence_status", "first_pv_frame",
)

PROMPT = """Review one rooftop target using three images in this order:
1. CONFIRMED-PRESENT REFERENCE montage from 2024 and/or 2025.
2. City of Johannesburg aerial montage: CoJ-2019 then CoJ-2023.
3. Chronological R0 historical strip F01 through F06; crossed grey cells are blank.

First decide whether the marked/cropped roof is the same target across sources.
The references show what the target and its PV array look like, but do not copy
their present state into older images. Judge CoJ-2019, CoJ-2023, and every
nonblank R0 frame from visible pixels. Allow changes in source, scale, colour,
parallax, resolution, and registration. Do not count solar water heaters,
skylights, vents, glare, shadows, dark roof paint, or a neighbouring roof as PV.

For the R0 strip choose:
- monotonic_install when readable nonblank frames are absent then present and
  stay present; first_pv_frame is the first present F01..F06.
- already_present_before_F01 when all readable nonblank frames are present;
  first_pv_frame=before_F01.
- not_present_through_F06 when all readable nonblank frames are absent;
  first_pv_frame=after_F06 or none.
- present_to_absent_or_nonmonotonic for a readable nonmonotonic pattern;
  first_pv_frame=unclear.
- unreviewable when target correspondence or visibility is insufficient;
  first_pv_frame=unclear.

Never select a blank slot. If target_match=unclear, both CoJ states must be
unclear and the R0 sequence must be unreviewable. Return JSON only."""

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "target_match", "coj_2019_state", "coj_2023_state",
        "sequence_status", "first_pv_frame", "confidence", "reason",
    ],
    "properties": {
        "target_match": {"type": "string", "enum": list(TARGET_MATCH)},
        "coj_2019_state": {"type": "string", "enum": list(STATES)},
        "coj_2023_state": {"type": "string", "enum": list(STATES)},
        "sequence_status": {"type": "string", "enum": list(SEQUENCE_STATUSES)},
        "first_pv_frame": {"type": "string", "enum": list(FIRST_PV_FRAMES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1},
    },
}

_LOCK = threading.Lock()


def canonical_sha(value: Any) -> str:
    return v3.sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def stable_hash(*parts: Any) -> str:
    return v3.sha256_bytes("\x1f".join(map(str, parts)).encode())


def validate_response(
    value: Mapping[str, Any], valid_frames: set[str] | None = None,
) -> dict[str, Any]:
    expected = {
        "target_match", "coj_2019_state", "coj_2023_state",
        "sequence_status", "first_pv_frame", "confidence", "reason",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("response fields differ from pilot schema")
    target = str(value["target_match"])
    coj19, coj23 = str(value["coj_2019_state"]), str(value["coj_2023_state"])
    if target not in TARGET_MATCH or coj19 not in STATES or coj23 not in STATES:
        raise ValueError("out-of-vocabulary target/CoJ state")
    sequence = v5.validate_response({
        "sequence_status": value["sequence_status"],
        "first_pv_frame": value["first_pv_frame"],
        "confidence": value["confidence"],
        "reason": value["reason"],
    })
    if target == "unclear" and (
        coj19 != "unclear" or coj23 != "unclear"
        or sequence["sequence_status"] != "unreviewable"
    ):
        raise ValueError("unclear target has definite image decisions")
    if (
        valid_frames is not None
        and sequence["sequence_status"] == "monotonic_install"
        and sequence["first_pv_frame"] not in valid_frames
    ):
        raise ValueError("first_pv_frame points to blank R0 slot")
    return {
        "target_match": target,
        "coj_2019_state": coj19,
        "coj_2023_state": coj23,
        **sequence,
    }


def needs_third_rep(reps: Sequence[Mapping[str, Any]]) -> bool:
    if len(reps) != 2:
        raise ValueError("two primary reps required")
    return any(reps[0][key] != reps[1][key] for key in CONSENSUS_FIELDS)


def consensus(reps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(reps) not in {2, 3}:
        raise ValueError("two or three reps required")
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for rep in reps:
        key = tuple(str(rep[field]) for field in CONSENSUS_FIELDS)
        groups.setdefault(key, []).append(rep)
    winner = max(groups, key=lambda key: len(groups[key]))
    agreeing = groups[winner]
    if len(agreeing) < 2:
        return {
            "target_match": "unclear",
            "coj_2019_state": "unclear",
            "coj_2023_state": "unclear",
            "sequence_status": "unreviewable",
            "first_pv_frame": "unclear",
            "confidence": None,
            "reason": "three-way full-tuple disagreement",
            "agreeing_reps": [],
            "rep_count": len(reps),
            "consensus_source": "NO_TWO_VOTE_CONSENSUS",
        }
    return {
        **dict(zip(CONSENSUS_FIELDS, winner)),
        "confidence": sum(float(rep["confidence"]) for rep in agreeing) / len(agreeing),
        "reason": " | ".join(dict.fromkeys(str(rep["reason"]) for rep in agreeing)),
        "agreeing_reps": [int(rep["_rep"]) for rep in agreeing],
        "rep_count": len(reps),
        "consensus_source": "GEMINI_35_TWO_VOTE_FULL_TUPLE",
    }


def largest_remainder_allocation(
    sizes: Mapping[tuple[Any, ...], int], total: int,
) -> dict[tuple[Any, ...], int]:
    population = sum(sizes.values())
    raw = {key: total * size / population for key, size in sizes.items()}
    allocated = {key: min(size, math.floor(raw[key])) for key, size in sizes.items()}
    remaining = total - sum(allocated.values())
    order = sorted(
        sizes,
        key=lambda key: (raw[key] - allocated[key], stable_hash(SAMPLE_SEED, *key)),
        reverse=True,
    )
    for key in order:
        if remaining == 0:
            break
        if allocated[key] < sizes[key]:
            allocated[key] += 1
            remaining -= 1
    if remaining:
        raise ValueError("could not allocate stratified draw")
    return allocated


def build_sample() -> pd.DataFrame:
    targets = pd.read_parquet(COJ_ROOT / "reference_targets.parquet")
    observations = pd.read_parquet(COJ_FULL / "full_coj_observations.parquet")
    v4 = pd.read_parquet(V4_ROOT / "window_review.parquet")
    v5_verdicts = pd.read_parquet(V5_ROOT / "recovery_verdicts.parquet")
    gate = observations.groupby("anchor_id")["reference_gate_pass"].first()
    uninformative = (
        observations.assign(
            _uninformative=observations["automated_verdict"].eq("uninformative")
        )
        .groupby("anchor_id")["_uninformative"].sum()
    )
    failed_ids = set(gate.loc[gate.eq(False)].index.astype(str))
    census_ids = failed_ids & set(v4["anchor_id"].astype(str))
    if len(failed_ids) != 4261 or len(census_ids) != EXPECTED_CENSUS:
        raise ValueError("unexpected CoJ gate-failure or V4-overlap population")

    base = targets.loc[targets["anchor_id"].astype(str).isin(failed_ids)].copy()
    base["audit_uninformative_count"] = base["anchor_id"].map(uninformative).astype(int)
    base["v4_overlap"] = base["anchor_id"].astype(str).isin(census_ids)
    base = base.merge(
        v4[["anchor_id", "verdict"]].rename(columns={"verdict": "v4_verdict"}),
        on="anchor_id", how="left", validate="one_to_one",
    ).merge(
        v5_verdicts[["anchor_id", "sequence_status", "first_pv_frame"]].rename(
            columns={
                "sequence_status": "v5_sequence_status",
                "first_pv_frame": "v5_first_pv_frame",
            }
        ),
        on="anchor_id", how="left", validate="one_to_one",
    )
    base["sample_stratum"] = (
        base["reference_arm"].astype(str) + "|"
        + base["area_bin"].astype(str) + "|"
        + base["chip_arm"].astype(str) + "|"
        + base["split"].astype(str)
    )
    base["selection_hash"] = [
        stable_hash(SAMPLE_SEED, anchor_id)
        for anchor_id in base["anchor_id"].astype(str)
    ]
    census = base.loc[base["v4_overlap"]].copy()
    census["sample_component"] = "V4_OVERLAP_CENSUS"
    census["stratum_population"] = len(census)
    census["stratum_sample"] = len(census)
    census["inclusion_probability"] = 1.0
    census["sampling_weight"] = 1.0

    remaining = base.loc[~base["v4_overlap"]].copy()
    special = remaining.loc[remaining["audit_uninformative_count"].gt(0)].copy()
    if len(special) != 57:
        raise ValueError(f"unexpected non-overlap uninformative targets: {len(special)}")
    special["sample_component"] = "UNINFORMATIVE_CENSUS"
    special["stratum_population"] = [
        int((remaining["audit_uninformative_count"] == value).sum())
        for value in special["audit_uninformative_count"]
    ]
    special["stratum_sample"] = special["stratum_population"]
    special["inclusion_probability"] = 1.0
    special["sampling_weight"] = 1.0

    ordinary = remaining.loc[remaining["audit_uninformative_count"].eq(0)].copy()
    needed = EXPECTED_DRAW - len(special)
    sizes = ordinary.groupby("sample_stratum").size().to_dict()
    allocation = largest_remainder_allocation(
        {tuple([key]): int(size) for key, size in sizes.items()}, needed
    )
    drawn = []
    for stratum, rows in ordinary.groupby("sample_stratum", sort=True):
        n = allocation[(stratum,)]
        if n == 0:
            continue
        selected = rows.sort_values("selection_hash").head(n).copy()
        selected["sample_component"] = "STRATIFIED_DRAW"
        selected["stratum_population"] = len(rows)
        selected["stratum_sample"] = n
        selected["inclusion_probability"] = n / len(rows)
        selected["sampling_weight"] = len(rows) / n
        drawn.append(selected)
    sample = pd.concat([census, special, *drawn], ignore_index=True)
    if len(sample) != EXPECTED_SAMPLE or sample["anchor_id"].nunique() != EXPECTED_SAMPLE:
        raise ValueError("sample does not contain 426 unique anchors")
    if int(sample["v4_overlap"].sum()) != 120:
        raise ValueError("sample lost V4 overlap census")
    return sample.sort_values("anchor_id").reset_index(drop=True)


def _select_r0_rows(rows: pd.DataFrame, lower: Any, upper: Any) -> pd.DataFrame:
    usable = rows.loc[rows["chip_png_path"].notna()].copy()
    usable = usable.loc[usable["chip_png_path"].map(lambda path: Path(str(path)).is_file())]
    usable = usable.sort_values(["capture_date", "chip_png_path"]).drop_duplicates(
        "capture_date", keep="last"
    )
    if len(usable) <= 6:
        return usable
    dates = pd.to_datetime(usable["capture_date"])
    lo = pd.to_datetime(lower, errors="coerce")
    hi = pd.to_datetime(upper, errors="coerce")
    if pd.isna(lo) and pd.isna(hi):
        indices = sorted({0, 1, len(usable) // 2, len(usable) - 3, len(usable) - 2, len(usable) - 1})
        return usable.iloc[indices]
    center = hi if pd.isna(lo) else lo if pd.isna(hi) else lo + (hi - lo) / 2
    priorities = []
    for index, date in enumerate(dates):
        boundary = int((not pd.isna(lo) and date == lo) or (not pd.isna(hi) and date == hi))
        priorities.append((-boundary, abs((date - center).days), index))
    chosen = sorted(item[2] for item in sorted(priorities)[:6])
    return usable.iloc[chosen]


def _save_montage(
    paths: Sequence[Path | None], labels: Sequence[str], path: Path, cell: tuple[int, int],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = v3._montage(paths, labels, cell_size=cell)
    image.save(path, format="PNG", optimize=False)
    return v3.sha256_file(path)


def _readable_image(path: Path) -> bool:
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.load()
        return True
    except (OSError, ValueError):
        return False


def build_packets(sample: pd.DataFrame, out_root: Path) -> pd.DataFrame:
    sequence = pd.read_parquet(COJ_FULL / "full_sequence_observations.parquet")
    r0 = pd.read_parquet(R0_MANIFEST)
    v4_packets = pd.read_parquet(V4_ROOT / "packet_index.parquet").set_index("anchor_id")
    v4_candidates = pd.read_parquet(V4_ROOT / "candidate_manifest.parquet").set_index("anchor_id")
    seq_groups = {key: group for key, group in sequence.groupby("anchor_id")}
    r0_groups = {key: group for key, group in r0.groupby("anchor_id")}
    prompt_sha = v3.sha256_bytes(PROMPT.encode())
    schema_sha = canonical_sha(RESPONSE_SCHEMA)
    rule_sha = v3.sha256_bytes(RULE_VERSION.encode())
    records = []
    for index, row in enumerate(sample.to_dict("records"), 1):
        anchor_id = str(row["anchor_id"])
        packet_dir = out_root / "packets" / stable_hash(anchor_id)[:2] / anchor_id
        seq = seq_groups[anchor_id]
        references = seq.loc[seq["role"].str.startswith("reference_")].sort_values("role")
        coj = seq.loc[seq["role"].str.startswith("audit_")].sort_values("role")
        if len(coj) != 2 or not 1 <= len(references) <= 2:
            raise ValueError(f"{anchor_id}: invalid reference/CoJ image set")
        ref_paths = [Path(str(path)) for path in references["image_path"]]
        coj_paths = [Path(str(path)) for path in coj["image_path"]]
        if not all(path.is_file() for path in [*ref_paths, *coj_paths]):
            raise FileNotFoundError(f"{anchor_id}: missing frozen reference/CoJ crop")
        if not all(_readable_image(path) for path in ref_paths):
            raise ValueError(f"{anchor_id}: unreadable confirmed-present reference crop")
        rerendered_roles = []
        for position, (_, coj_row) in enumerate(coj.iterrows()):
            if _readable_image(coj_paths[position]):
                continue
            role = str(coj_row["role"])
            rerender_path = packet_dir / f"{role}_rerender.png"
            render_row = pd.Series({
                **row,
                "coj_path": str(coj_row["source_path"]),
            })
            render_crop(render_row, rerender_path)
            if not _readable_image(rerender_path):
                raise ValueError(f"{anchor_id}/{role}: rerender remains unreadable")
            coj_paths[position] = rerender_path
            rerendered_roles.append(role)
        ref_path = packet_dir / "reference_montage.png"
        coj_path = packet_dir / "coj_montage.png"
        ref_sha = _save_montage(
            ref_paths, references["role"].astype(str).tolist(), ref_path, (256, 256)
        )
        coj_sha = _save_montage(
            coj_paths, ["CoJ-2019", "CoJ-2023"], coj_path, (256, 256)
        )
        if bool(row["v4_overlap"]):
            source_packet = v4_packets.loc[anchor_id].to_dict()
            candidate = v4_candidates.loc[anchor_id].to_dict()
            provenance = v5._slot_provenance(source_packet, candidate)
            strip_path = Path(str(source_packet["temporal_strip_path"]))
            strip_sha = str(source_packet["temporal_strip_sha256"])
            strip_source = "V4_FROZEN_STRIP"
            source_v4_packet_sha = str(source_packet["packet_sha256"])
        else:
            selected = _select_r0_rows(
                r0_groups[anchor_id],
                row.get("install_interval_start"),
                row.get("install_interval_end"),
            )
            provenance = []
            paths: list[Path | None] = []
            labels = []
            for position, (_, frame_row) in enumerate(selected.iterrows(), 1):
                frame_id = f"F{position:02d}"
                path = Path(str(frame_row["chip_png_path"]))
                paths.append(path)
                labels.append(frame_id)
                provenance.append({
                    "frame_id": frame_id,
                    "source_slot": f"r0_selected_{position}",
                    "source_present": True,
                    "capture_date": str(frame_row["capture_date"])[:10],
                    "src_tiff_path": str(frame_row["src_tiff_path"]),
                    "src_tiff_sha256": str(frame_row["src_tiff_sha256"]),
                    "render_path": str(path),
                    "render_sha256": str(frame_row["chip_png_sha256"]),
                })
            for position in range(len(paths) + 1, 7):
                frame_id = f"F{position:02d}"
                paths.append(None)
                labels.append(frame_id)
                provenance.append({
                    "frame_id": frame_id, "source_slot": f"blank_{position}",
                    "source_present": False, "capture_date": None,
                    "src_tiff_path": None, "src_tiff_sha256": None,
                    "render_path": None, "render_sha256": None,
                })
            strip_path = packet_dir / "r0_temporal_strip.png"
            strip_sha = _save_montage(paths, labels, strip_path, (192, 192))
            strip_source = "R0_BOUNDARY_CENTERED_SELECTION"
            source_v4_packet_sha = None
        packet_sha = v3.sha256_bytes(
            "\0".join([
                RUN_ID, anchor_id, ref_sha, coj_sha, strip_sha,
                prompt_sha, schema_sha, rule_sha, str(row["selection_hash"]),
            ]).encode()
        )
        records.append({
            "anchor_id": anchor_id,
            "reference_montage_path": str(ref_path),
            "reference_montage_sha256": ref_sha,
            "coj_montage_path": str(coj_path),
            "coj_montage_sha256": coj_sha,
            "coj_rerendered_roles": rerendered_roles,
            "r0_temporal_strip_path": str(strip_path),
            "r0_temporal_strip_sha256": strip_sha,
            "r0_slot_provenance": provenance,
            "r0_strip_source": strip_source,
            "source_v4_packet_sha256": source_v4_packet_sha,
            "prompt_sha256": prompt_sha,
            "response_schema_sha256": schema_sha,
            "rule_sha256": rule_sha,
            "packet_sha256": packet_sha,
        })
        if index % 100 == 0:
            print(f"[prepare] packets {index}/{len(sample)}", flush=True)
    return pd.DataFrame(records).sort_values("anchor_id").reset_index(drop=True)


def prepare(out_root: Path) -> dict[str, Any]:
    lock_path = out_root / "RUN_LOCK.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock.get("run_id") != RUN_ID:
            raise ValueError("existing output has different run identity")
        return {"status": "ALREADY_PREPARED", "sample": lock["sample_size"]}
    sample = build_sample()
    packets = build_packets(sample, out_root)
    v3.atomic_parquet(out_root / "sample_manifest.parquet", sample)
    v3.atomic_parquet(out_root / "packet_index.parquet", packets)
    lock = {
        "run_id": RUN_ID,
        "sample_seed": SAMPLE_SEED,
        "sample_size": len(sample),
        "v4_overlap_census": int(sample["v4_overlap"].sum()),
        "nonoverlap_draw": int((~sample["v4_overlap"]).sum()),
        "sample_component_counts": sample["sample_component"].value_counts().to_dict(),
        "model_alias": MODEL_ALIAS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "prompt_sha256": v3.sha256_bytes(PROMPT.encode()),
        "response_schema_sha256": canonical_sha(RESPONSE_SCHEMA),
        "rule_version": RULE_VERSION,
        "rule_sha256": v3.sha256_bytes(RULE_VERSION.encode()),
        "sample_manifest_sha256": v3.sha256_file(out_root / "sample_manifest.parquet"),
        "packet_index_sha256": v3.sha256_file(out_root / "packet_index.parquet"),
        "source_hashes": {
            str(path): v3.sha256_file(path)
            for path in (
                COJ_ROOT / "reference_targets.parquet",
                COJ_FULL / "full_coj_observations.parquet",
                COJ_FULL / "full_sequence_observations.parquet",
                R0_MANIFEST,
                V4_ROOT / "window_review.parquet",
                V4_ROOT / "packet_index.parquet",
                V5_ROOT / "recovery_verdicts.parquet",
            )
        },
        "transport": {"workers": DEFAULT_WORKERS, "qps": DEFAULT_QPS},
        "smoke_anchor_ids": sample["anchor_id"].astype(str).head(10).tolist(),
    }
    v3.atomic_json(lock_path, lock)
    v3.artifact_manifest(out_root)
    return {
        "status": "PREPARED", "sample": len(sample),
        "components": lock["sample_component_counts"],
    }


def augment_zero_strata(out_root: Path) -> dict[str, Any]:
    """Add one frozen unit from every initially zero-allocated micro-stratum."""
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    if lock.get("strata_coverage_amendment"):
        return lock["strata_coverage_amendment"]
    sample_path = out_root / "sample_manifest.parquet"
    packet_path = out_root / "packet_index.parquet"
    sample = pd.read_parquet(sample_path)
    if len(sample) != EXPECTED_SAMPLE:
        raise ValueError("strata amendment requires the original 426-row sample")

    targets = pd.read_parquet(COJ_ROOT / "reference_targets.parquet")
    observations = pd.read_parquet(COJ_FULL / "full_coj_observations.parquet")
    v4 = pd.read_parquet(V4_ROOT / "window_review.parquet")
    v5_verdicts = pd.read_parquet(V5_ROOT / "recovery_verdicts.parquet")
    gate = observations.groupby("anchor_id")["reference_gate_pass"].first()
    uninformative = (
        observations.assign(
            _uninformative=observations["automated_verdict"].eq("uninformative")
        ).groupby("anchor_id")["_uninformative"].sum()
    )
    failed_ids = set(gate.loc[gate.eq(False)].index.astype(str))
    v4_ids = set(v4["anchor_id"].astype(str))
    ordinary = targets.loc[
        targets["anchor_id"].astype(str).isin(failed_ids - v4_ids)
    ].copy()
    ordinary["audit_uninformative_count"] = (
        ordinary["anchor_id"].map(uninformative).astype(int)
    )
    ordinary = ordinary.loc[ordinary["audit_uninformative_count"].eq(0)].copy()
    ordinary["sample_stratum"] = (
        ordinary["reference_arm"].astype(str) + "|"
        + ordinary["area_bin"].astype(str) + "|"
        + ordinary["chip_arm"].astype(str) + "|"
        + ordinary["split"].astype(str)
    )
    sampled_strata = set(
        sample.loc[sample["sample_component"].eq("STRATIFIED_DRAW"), "sample_stratum"]
    )
    missing_strata = sorted(set(ordinary["sample_stratum"]) - sampled_strata)
    supplement_rows = []
    for stratum in missing_strata:
        rows = ordinary.loc[ordinary["sample_stratum"].eq(stratum)].copy()
        rows["selection_hash"] = [
            stable_hash(SAMPLE_SEED, anchor_id)
            for anchor_id in rows["anchor_id"].astype(str)
        ]
        chosen = rows.sort_values("selection_hash").head(1).copy()
        chosen["v4_overlap"] = False
        chosen["v4_verdict"] = None
        chosen = chosen.merge(
            v5_verdicts[["anchor_id", "sequence_status", "first_pv_frame"]].rename(
                columns={
                    "sequence_status": "v5_sequence_status",
                    "first_pv_frame": "v5_first_pv_frame",
                }
            ),
            on="anchor_id", how="left", validate="one_to_one",
        )
        chosen["sample_component"] = "ZERO_CELL_SUPPLEMENT"
        chosen["stratum_population"] = len(rows)
        chosen["stratum_sample"] = 1
        chosen["inclusion_probability"] = 1 / len(rows)
        chosen["sampling_weight"] = len(rows)
        supplement_rows.append(chosen)
    supplement = pd.concat(supplement_rows, ignore_index=True)
    if len(supplement) != 9 or int(supplement["stratum_population"].sum()) != 30:
        raise ValueError("unexpected zero-cell supplement")
    supplement = supplement.reindex(columns=sample.columns)
    supplement_packets = build_packets(supplement, out_root)
    packets = pd.read_parquet(packet_path)

    archive = out_root / "pre_amendment"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sample_path, archive / "sample_manifest_426.parquet")
    shutil.copy2(packet_path, archive / "packet_index_426.parquet")
    combined_sample = pd.concat([sample, supplement], ignore_index=True).sort_values(
        "anchor_id"
    ).reset_index(drop=True)
    combined_packets = pd.concat(
        [packets, supplement_packets], ignore_index=True
    ).sort_values("anchor_id").reset_index(drop=True)
    v3.atomic_parquet(sample_path, combined_sample)
    v3.atomic_parquet(packet_path, combined_packets)
    amendment = {
        "status": "STRATA_COVERAGE_AMENDED",
        "reason": "nine fine strata had zero initial allocation",
        "original_sample_size": 426,
        "supplement_size": 9,
        "supplement_population_covered": 30,
        "final_sample_size": 435,
        "original_sample_manifest_sha256": v3.sha256_file(
            archive / "sample_manifest_426.parquet"
        ),
        "original_packet_index_sha256": v3.sha256_file(
            archive / "packet_index_426.parquet"
        ),
        "supplement_anchor_ids": supplement["anchor_id"].astype(str).tolist(),
        "amended_utc": datetime.now(timezone.utc).isoformat(),
    }
    lock["sample_size"] = 435
    lock["sample_manifest_sha256"] = v3.sha256_file(sample_path)
    lock["packet_index_sha256"] = v3.sha256_file(packet_path)
    lock["strata_coverage_amendment"] = amendment
    v3.atomic_json(lock_path, lock)
    v3.atomic_parquet(out_root / "strata_supplement.parquet", supplement)
    v3.artifact_manifest(out_root)
    return amendment


def admit_version(lock: dict[str, Any], returned: Any) -> str:
    version = str(returned or "").strip()
    if not version:
        raise v3.ModelVersionDrift("missing exact model version")
    with _LOCK:
        expected = lock.get("exact_model_version")
        if expected is None:
            lock["exact_model_version"] = version
        elif expected != version:
            raise v3.ModelVersionDrift(f"expected {expected!r}, got {version!r}")
    return version


def run_rep(
    packet: Mapping[str, Any], rep: int, scope: str, out_root: Path,
    lock: dict[str, Any], caller: Callable[..., tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    anchor_id = str(packet["anchor_id"])
    path = out_root / scope / "reps" / anchor_id / f"rep{rep}.json"
    identity = {
        "_run_id": RUN_ID, "_packet_sha256": packet["packet_sha256"],
        "_model_alias": MODEL_ALIAS, "_prompt_sha256": packet["prompt_sha256"],
        "_response_schema_sha256": packet["response_schema_sha256"],
        "_rule_sha256": packet["rule_sha256"],
    }
    if path.exists():
        payload = json.loads(path.read_text())
        if any(payload.get(key) != value for key, value in identity.items()):
            raise ValueError(f"{anchor_id}: resume identity mismatch")
        admit_version(lock, payload.get("_exact_model_version"))
        return payload
    images = [
        Path(packet["reference_montage_path"]), Path(packet["coj_montage_path"]),
        Path(packet["r0_temporal_strip_path"]),
    ]
    for key, image in zip(
        ("reference_montage_sha256", "coj_montage_sha256", "r0_temporal_strip_sha256"),
        images,
    ):
        if not image.is_file() or v3.sha256_file(image) != packet[key]:
            raise ValueError(f"{anchor_id}: packet image integrity failure")
    attempts_path = out_root / scope / "attempts.jsonl"
    prior = []
    if attempts_path.exists():
        for line in attempts_path.read_text().splitlines():
            record = json.loads(line)
            if record.get("anchor_id") == anchor_id and int(record.get("rep", -1)) == rep:
                prior.append(int(record["attempt"]))
    last_error = ""
    valid_frames = {
        item["frame_id"] for item in list(packet["r0_slot_provenance"])
        if item["source_present"]
    }
    for attempt in range(max(prior, default=0) + 1, MAX_ATTEMPTS + 1):
        salt = stable_hash(RUN_ID, scope, anchor_id, rep, attempt, packet["packet_sha256"])
        started = time.time()
        raw: Mapping[str, Any] = {}
        try:
            text, raw = caller(
                image_paths=images, prompt=PROMPT, model=MODEL_ALIAS,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA, routing_salt=salt,
            )
            exact = admit_version(lock, raw.get("modelVersion"))
            parsed = validate_response(json.loads(text), valid_frames)
            parsed.update({
                **identity, "_rep": rep, "_attempt": attempt,
                "_routing_salt": salt, "_exact_model_version": exact,
            })
            v3.atomic_json(path, parsed)
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "VALID", "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "model_alias": MODEL_ALIAS, "exact_model_version": exact,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started,
                "response": parsed,
                "raw": {
                    "modelVersion": raw.get("modelVersion"),
                    "responseId": raw.get("responseId"),
                    "usageMetadata": raw.get("usageMetadata"),
                    "finishReasons": [
                        item.get("finishReason") for item in raw.get("candidates", [])
                    ],
                    "transport_retries": raw.get("_transport_retries", 0),
                },
            })
            v3.atomic_json(out_root / "RUN_LOCK.json", lock)
            return parsed
        except v3.ModelVersionDrift:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "RETRYABLE_FAILURE", "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started, "error": last_error,
            })
    raise RuntimeError(f"{anchor_id}/rep{rep}: {last_error}")


def run_packets(
    packets: Sequence[Mapping[str, Any]], out_root: Path, scope: str,
    workers: int, qps: float, allow_third: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    caller = v3.native_caller(client_config(MODEL_ALIAS), RateLimiter(qps))
    rep_rows, verdict_rows = [], []

    def one(packet: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        reps = [
            run_rep(packet, rep, scope, out_root, lock, caller) for rep in (1, 2)
        ]
        if allow_third and needs_third_rep(reps):
            reps.append(run_rep(packet, 3, scope, out_root, lock, caller))
        result = consensus(reps)
        return reps, {
            "anchor_id": packet["anchor_id"], **result,
            "decision_source": "GEMINI_35_COJ_GATE_FAILURE_PILOT",
            "packet_sha256": packet["packet_sha256"],
            "r0_temporal_strip_sha256": packet["r0_temporal_strip_sha256"],
            "r0_strip_source": packet["r0_strip_source"],
            "source_v4_packet_sha256": packet["source_v4_packet_sha256"],
            "model_alias": MODEL_ALIAS,
            "exact_model_version": reps[0]["_exact_model_version"],
            "prompt_sha256": packet["prompt_sha256"],
            "response_schema_sha256": packet["response_schema_sha256"],
            "rule_version": RULE_VERSION, "rule_sha256": packet["rule_sha256"],
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, packet) for packet in packets]
        for index, future in enumerate(as_completed(futures), 1):
            reps, verdict = future.result()
            rep_rows.extend(reps)
            verdict_rows.append(verdict)
            if index % 50 == 0 or index == len(futures):
                print(f"[{scope}] completed {index}/{len(futures)}", flush=True)
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    return (
        pd.DataFrame(rep_rows).sort_values(["_packet_sha256", "_rep"]),
        pd.DataFrame(verdict_rows).sort_values("anchor_id"),
    )


def run_smoke(out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    ids = set(lock["smoke_anchor_ids"])
    selected = [row for row in packets.to_dict("records") if row["anchor_id"] in ids]
    reps, verdicts = run_packets(selected, out_root, "smoke", workers, qps, False)
    if len(reps) != 20:
        raise RuntimeError("smoke did not complete 20 primary reps")
    v3.atomic_parquet(out_root / "smoke" / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "smoke" / "verdicts.parquet", verdicts)
    status = {
        "status": "SMOKE_PASS", "anchors": 10, "reps": 20,
        "model_alias": MODEL_ALIAS,
        "exact_model_version": json.loads(
            (out_root / "RUN_LOCK.json").read_text()
        )["exact_model_version"],
    }
    v3.atomic_json(out_root / "smoke" / "status.json", status)
    v3.artifact_manifest(out_root)
    return status


def build_sidecars(
    sample: pd.DataFrame, packets: pd.DataFrame, verdicts: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    merged = sample.merge(verdicts, on="anchor_id", validate="one_to_one")
    packet_by_id = packets.set_index("anchor_id")
    frame_labels, intervals, coj_labels, censored, manual = [], [], [], [], []
    provenance_keys = [
        "agreeing_reps", "rep_count", "confidence", "model_alias",
        "exact_model_version", "prompt_sha256", "response_schema_sha256",
        "packet_sha256", "r0_temporal_strip_sha256", "source_v4_packet_sha256",
        "rule_version", "rule_sha256",
    ]
    for row in merged.to_dict("records"):
        common = {key: row[key] for key in provenance_keys}
        for year in (2019, 2023):
            state = row[f"coj_{year}_state"]
            if state != "unclear" and row["target_match"] == "same_target":
                coj_labels.append({
                    "anchor_id": row["anchor_id"], "coj_epoch": year,
                    "acquisition_interval_start": f"{year}-01-01",
                    "acquisition_interval_end": f"{year}-12-31",
                    "pv_label": state, **common,
                })
        slots = [
            dict(item)
            for item in list(packet_by_id.loc[row["anchor_id"], "r0_slot_provenance"])
            if item["source_present"]
        ]
        status, first = row["sequence_status"], row["first_pv_frame"]
        if row["target_match"] != "same_target":
            continue
        if status == "monotonic_install":
            first_index = FRAME_IDS.index(first)
            for item in slots:
                label = "present" if FRAME_IDS.index(item["frame_id"]) >= first_index else "absent"
                frame_labels.append({
                    "anchor_id": row["anchor_id"], **item, "pv_label": label,
                    "sequence_status": status, "first_pv_frame": first, **common,
                })
            before = [
                item for item in slots if FRAME_IDS.index(item["frame_id"]) < first_index
            ]
            if before:
                absent = before[-1]
                present = next(item for item in slots if item["frame_id"] == first)
                intervals.append({
                    "anchor_id": row["anchor_id"],
                    "last_absent_frame": absent["frame_id"],
                    "last_absent_capture_date": absent["capture_date"],
                    "last_absent_src_tiff_sha256": absent["src_tiff_sha256"],
                    "first_present_frame": present["frame_id"],
                    "first_present_capture_date": present["capture_date"],
                    "first_present_src_tiff_sha256": present["src_tiff_sha256"],
                    "interval_days": (
                        pd.Timestamp(present["capture_date"])
                        - pd.Timestamp(absent["capture_date"])
                    ).days,
                    "original_interval_start": row["install_interval_start"],
                    "original_interval_end": row["install_interval_end"],
                    "boundary_changed": not (
                        str(row["install_interval_start"])[:10] == absent["capture_date"]
                        and str(row["install_interval_end"])[:10] == present["capture_date"]
                    ),
                    **common,
                })
        elif status in {"already_present_before_F01", "not_present_through_F06"}:
            label = "present" if status == "already_present_before_F01" else "absent"
            for item in slots:
                frame_labels.append({
                    "anchor_id": row["anchor_id"], **item, "pv_label": label,
                    "sequence_status": status, "first_pv_frame": first, **common,
                })
            boundary = slots[0] if label == "present" else slots[-1]
            censored.append({
                "anchor_id": row["anchor_id"],
                "censor_type": "left" if label == "present" else "right",
                "boundary_frame": boundary["frame_id"],
                "boundary_capture_date": boundary["capture_date"],
                "visible_frame_count": len(slots), **common,
            })
        elif status == "present_to_absent_or_nonmonotonic":
            manual.append({
                "anchor_id": row["anchor_id"],
                "queue_reason": "NONMONOTONIC_SEQUENCE",
                "reason": row["reason"], **common,
            })
    return {
        "recovery_verdicts": merged,
        "r0_frame_labels": pd.DataFrame(frame_labels),
        "r0_install_intervals": pd.DataFrame(intervals),
        "coj_frame_labels": pd.DataFrame(coj_labels),
        "censored": pd.DataFrame(censored),
        "manual_review_queue": pd.DataFrame(manual),
    }


def run_production(out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    smoke = json.loads((out_root / "smoke" / "status.json").read_text())
    if smoke.get("status") != "SMOKE_PASS":
        raise RuntimeError("production requires passed smoke")
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    sample = pd.read_parquet(out_root / "sample_manifest.parquet")
    reps, verdicts = run_packets(
        packets.to_dict("records"), out_root, "production", workers, qps, True
    )
    if set(verdicts["exact_model_version"]) != {smoke["exact_model_version"]}:
        raise v3.ModelVersionDrift("production exact version differs from smoke")
    outputs = build_sidecars(sample, packets, verdicts)
    v3.atomic_parquet(out_root / "rep_verdicts.parquet", reps)
    for name, frame in outputs.items():
        v3.atomic_parquet(out_root / f"{name}.parquet", frame)
    merged = outputs["recovery_verdicts"]
    intervals = outputs["r0_install_intervals"]
    attempts = [
        json.loads(line)
        for line in (out_root / "production" / "attempts.jsonl").read_text().splitlines()
    ]
    summary = {
        "status": "COMPLETE", "sample_size": len(sample),
        "sample_component_counts": sample["sample_component"].value_counts().to_dict(),
        "v4_overlap": int(sample["v4_overlap"].sum()),
        "v5_rejected_overlap": int(sample["v5_sequence_status"].notna().sum()),
        "model_alias": MODEL_ALIAS, "exact_model_version": smoke["exact_model_version"],
        "rep_rows": len(reps), "third_rep_anchors": int(reps["_rep"].eq(3).sum()),
        "primary_agreement_rate": 1 - int(reps["_rep"].eq(3).sum()) / len(sample),
        "consensus_source_counts": merged["consensus_source"].value_counts().to_dict(),
        "sequence_status_counts": merged["sequence_status"].value_counts().to_dict(),
        "target_match_counts": merged["target_match"].value_counts().to_dict(),
        "coj_2019_state_counts": merged["coj_2019_state"].value_counts().to_dict(),
        "coj_2023_state_counts": merged["coj_2023_state"].value_counts().to_dict(),
        "r0_install_intervals": len(intervals),
        "r0_boundary_changed": int(intervals["boundary_changed"].sum()) if len(intervals) else 0,
        "r0_frame_labels": len(outputs["r0_frame_labels"]),
        "coj_frame_labels": len(outputs["coj_frame_labels"]),
        "censored": len(outputs["censored"]),
        "manual_review": len(outputs["manual_review_queue"]),
        "attempt_rows": len(attempts),
        "retryable_failure_rows": sum(row["status"] != "VALID" for row in attempts),
        "sampling_weight_total": float(sample["sampling_weight"].sum()),
        "weighted_sequence_status_counts": {
            str(key): float(value)
            for key, value in merged.groupby("sequence_status")["sampling_weight"].sum().items()
        },
        "weighted_monotonic_rate": float(
            merged.loc[merged["sequence_status"].eq("monotonic_install"), "sampling_weight"].sum()
            / sample["sampling_weight"].sum()
        ),
        "weighted_interval_eligible": float(
            intervals.merge(
                sample[["anchor_id", "sampling_weight"]], on="anchor_id"
            )["sampling_weight"].sum()
        ) if len(intervals) else 0.0,
        "weighted_boundary_changed": float(
            intervals.merge(
                sample[["anchor_id", "sampling_weight"]], on="anchor_id"
            ).loc[lambda frame: frame["boundary_changed"], "sampling_weight"].sum()
        ) if len(intervals) else 0.0,
        "v5_overlap_status_agreement": int(
            (
                merged.loc[merged["v5_sequence_status"].notna(), "sequence_status"]
                == merged.loc[merged["v5_sequence_status"].notna(), "v5_sequence_status"]
            ).sum()
        ),
        "v5_overlap_exact_status_frame_agreement": int(
            (
                merged.loc[merged["v5_sequence_status"].notna(), "sequence_status"]
                == merged.loc[merged["v5_sequence_status"].notna(), "v5_sequence_status"]
            ).mul(
                merged.loc[merged["v5_sequence_status"].notna(), "first_pv_frame"]
                == merged.loc[merged["v5_sequence_status"].notna(), "v5_first_pv_frame"]
            ).sum()
        ),
        "fine_strata_population_without_zero_allocations": int(
            sample.loc[sample["sample_component"].eq("ZERO_CELL_SUPPLEMENT"), "stratum_population"].sum()
        ),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    v3.atomic_json(out_root / "summary.json", summary)
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    lock["finished_utc"] = summary["finished_utc"]
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    v3.artifact_manifest(out_root)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default = DATA_ROOT / RUN_ID
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--out-root", type=Path, default=default)
    augment = sub.add_parser("augment-zero-strata")
    augment.add_argument("--out-root", type=Path, default=default)
    for name in ("smoke", "production"):
        command = sub.add_parser(name)
        command.add_argument("--out-root", type=Path, default=default)
        command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
        command.add_argument("--qps", type=float, default=DEFAULT_QPS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.out_root)
    elif args.command == "augment-zero-strata":
        result = augment_zero_strata(args.out_root)
    elif args.command == "smoke":
        result = run_smoke(args.out_root, args.workers, args.qps)
    else:
        result = run_production(args.out_root, args.workers, args.qps)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
