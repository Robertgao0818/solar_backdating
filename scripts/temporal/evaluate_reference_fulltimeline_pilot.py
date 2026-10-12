#!/usr/bin/env python3
"""Audit the frozen full-timeline pilot and write intervals from raw labels.

Stdout is a deterministic audit summary. The command does not call Gemini,
does not download imagery, and does not treat a narrower interval as more
accurate. Old raw sequences are used only when the published table marks them
complete and the date/code lengths match.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.fulltimeline_manifest import assemble_manifest, scoring_requests  # noqa: E402
from scripts.temporal.reference_fulltimeline import (  # noqa: E402
    MODEL_ALIAS,
    POLICY_CENSUS_PRECISION,
    WINDOW_START,
    apply_manifest_gap_states,
    audit_tables,
    build_conclusion,
    compare_old_interval,
    infer_interval,
    in_analysis_window,
    plan_rechecks,
    repeat_support,
    replay_adaptive,
    timeline_observations,
)


DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004"
)
OLD_ROOT = Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
OLD_INTERVALS = OLD_ROOT / "deliverable_2026-09-02/ct_citywide_install_dated_20260902.csv"
OLD_SEQUENCES = OLD_ROOT / "sequences_20260923/ct_citywide_install_dated_20260902_with_sequences.csv"

INTERVAL_FIELDS = [
    "anchor_id",
    "grid_id",
    "grid_role",
    "arm",
    "status",
    "censoring",
    "install_date",
    "interval_start",
    "interval_end",
    "last_absent_date",
    "earliest_present_date",
    "upper_bound_date",
    "upper_bound_precision",
    "conflict_frames",
    "undated_reason",
    "n_present",
    "n_absent",
    "n_unknown",
    "n_call_failed",
    "labels_modified",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _load_raw(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"malformed raw log {path}:{line_number}") from exc
    return rows


def _aerial_verdict(raw: Sequence[Mapping[str, Any]], anchor: Mapping[str, str]) -> str:
    day = str(anchor.get("census_date") or "")[:10]
    for row in raw:
        if str(row.get("arm")) != "reference_aerial":
            continue
        if str(row.get("anchor_id")) != anchor["anchor_id"]:
            continue
        if str(row.get("capture_date") or "")[:10] != day:
            continue
        if str(row.get("call_status")) != "ok":
            continue
        verdict = str(row.get("verdict") or "")
        if verdict in {"present", "absent", "unknown"}:
            return verdict
    return "unknown"


def _recover_raw(row: Mapping[str, str] | None, census_date: str) -> tuple[bool, list[str]]:
    if not row:
        return False, []
    if str(row.get("sequence_coverage") or "") != "complete":
        return False, []
    dates = [item[:10] for item in str(row.get("obs_dates") or "").split(";") if item.strip()]
    codes = str(row.get("obs_code_raw") or "").split()
    if not dates or len(dates) != len(codes):
        return False, []
    window = [day for day in dates if in_analysis_window(day, census_date)]
    return True, window


def _load_old(anchor_ids: Sequence[str]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    import duckdb

    if not OLD_INTERVALS.is_file():
        return {}, {}
    con = duckdb.connect()
    con.execute("CREATE TABLE ids(anchor_id VARCHAR)")
    con.executemany("INSERT INTO ids VALUES (?)", [(anchor_id,) for anchor_id in anchor_ids])
    interval_rows = con.execute(
        f"""
        SELECT source_anchor_id, latest_absent_date, earliest_present_date,
               install_date, cohort, date_status, left_censored
        FROM read_csv('{OLD_INTERVALS}', header=true, all_varchar=true)
        WHERE source_anchor_id IN (SELECT anchor_id FROM ids)
        """
    ).fetchall()
    intervals = {
        row[0]: {
            "latest_absent_date": row[1] or "",
            "earliest_present_date": row[2] or "",
            "install_date": row[3] or "",
            "cohort": row[4] or "",
            "date_status": row[5] or "",
            "left_censored": row[6] or "",
        }
        for row in interval_rows
    }
    sequences: dict[str, dict[str, str]] = {}
    if OLD_SEQUENCES.is_file():
        sequence_rows = con.execute(
            f"""
            SELECT source_anchor_id, sequence_coverage, obs_dates, obs_code_raw
            FROM read_csv('{OLD_SEQUENCES}', header=true, all_varchar=true)
            WHERE source_anchor_id IN (SELECT anchor_id FROM ids)
            """
        ).fetchall()
        sequences = {
            row[0]: {
                "sequence_coverage": row[1] or "",
                "obs_dates": row[2] or "",
                "obs_code_raw": row[3] or "",
            }
            for row in sequence_rows
        }
    return intervals, sequences


def _quality(candidates: Sequence[Mapping[str, str]], anchors: Sequence[Mapping[str, str]], manifest: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grid_of = {row["anchor_id"]: row["grid_id"] for row in anchors}
    downloadable: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    manifest_by = {(row["anchor_id"], row["capture_date"]): row for row in manifest}
    for row in candidates:
        if str(row.get("callable") or "") != "1":
            continue
        grid_id = grid_of.get(row["anchor_id"], "")
        downloadable[grid_id] += 1
        found = manifest_by.get((row["anchor_id"], row["capture_date"][:10]))
        if found and found.get("terminal_state") not in {"download_failed", "quality_failed", "image_missing", "image_unreadable"}:
            if found.get("candidate_sha256"):
                passed[grid_id] += 1
    rows = []
    for grid_id in sorted(set(grid_of.values())):
        denominator = downloadable[grid_id]
        numerator = passed[grid_id]
        rate = (numerator / denominator) if denominator else 1.0
        rows.append(
            {
                "grid_id": grid_id,
                "n_downloadable": denominator,
                "n_quality_pass": numerator,
                "quality_pass_rate": f"{rate:.6f}",
                "diagnosed_below_95": int(denominator > 0 and rate < 0.95),
            }
        )
    return rows


def _paired_rows(raw: Sequence[Mapping[str, Any]], anchors: Sequence[Mapping[str, str]], manifest: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    paired_ids = {row["anchor_id"] for row in anchors if str(row.get("paired") or "") in {"1", "true", "True"}}
    degradation = {(row["anchor_id"], row["capture_date"]): row for row in manifest}
    by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in raw:
        if str(row.get("stage")) != "first_pass" or str(row.get("call_status")) != "ok":
            continue
        if str(row.get("anchor_id")) not in paired_ids:
            continue
        by_key[(str(row["anchor_id"]), str(row["capture_date"])[:10], str(row.get("arm")))] = row
    dates = sorted({(key[0], key[1]) for key in by_key})
    rows = []
    for anchor_id, day in dates:
        t2 = by_key.get((anchor_id, day, "t2"))
        t3 = by_key.get((anchor_id, day, "t3"))
        if t2 is None or t3 is None:
            continue
        meta = degradation.get((anchor_id, day), {})
        aerial_ok = str(meta.get("t3_degradation") or "") != "candidate_only_reference_unavailable"
        rows.append(
            {
                "anchor_id": anchor_id,
                "capture_date": day,
                "t2_verdict": t2.get("verdict"),
                "t3_verdict": t3.get("verdict"),
                "verdict_same": int(t2.get("verdict") == t3.get("verdict")),
                "candidate_sha256_t2": t2.get("candidate_sha256"),
                "candidate_sha256_t3": t3.get("candidate_sha256"),
                "candidate_bytes_same": int(t2.get("candidate_sha256") == t3.get("candidate_sha256") and bool(t2.get("candidate_sha256"))),
                "in_effect_comparison": int(aerial_ok),
            }
        )
    return rows


def evaluate(root: Path) -> dict[str, Any]:
    plan_dir = root / "plan"
    anchors = _read_csv(plan_dir / "anchor_selection.csv")
    candidates = _read_csv(plan_dir / "candidates.csv")
    references = _read_csv(plan_dir / "references.csv")
    grids = _read_csv(plan_dir / "grid_selection.csv")
    raw = _load_raw(root / "observations" / "raw.jsonl")
    render_path = root / "imagery" / "render_index.csv"
    render_rows = _read_csv(render_path) if render_path.is_file() else []
    manifest = assemble_manifest(anchors, candidates, references, render_rows, raw)
    requests = scoring_requests(manifest, anchors)
    annotated = apply_manifest_gap_states(candidates, manifest)
    version_path = plan_dir / "MODEL_VERSION.json"
    frozen_version = MODEL_ALIAS
    if version_path.is_file():
        frozen_version = str(json.loads(version_path.read_text(encoding="utf-8")).get("model_version") or MODEL_ALIAS)
    intervals = []
    supported = []
    adaptive_rows = []
    for anchor in sorted(anchors, key=lambda row: row["anchor_id"]):
        observations = timeline_observations(annotated, raw, anchor_id=anchor["anchor_id"], arm="t3")
        aerial_pv = _aerial_verdict(raw, anchor)
        interval = infer_interval(
            observations,
            expected_dates=[row["capture_date"] for row in observations],
            aerial_pv=aerial_pv,
            aerial_date=str(anchor.get("census_date") or "")[:10],
            aerial_precision=str(anchor.get("census_date_precision") or POLICY_CENSUS_PRECISION),
        )
        interval.update(
            {
                "anchor_id": anchor["anchor_id"],
                "grid_id": anchor["grid_id"],
                "grid_role": anchor["grid_role"],
                "arm": "t3",
                "labels_modified": 0,
            }
        )
        intervals.append(interval)
        rechecks = [
            row
            for row in raw
            if str(row.get("anchor_id")) == anchor["anchor_id"]
            and str(row.get("arm")) == "t3"
            and str(row.get("stage")) in {"boundary_recheck", "conflict_recheck"}
        ]
        support = repeat_support(observations, rechecks)
        if support["support"] == "repeat_supported":
            supported.append({**interval, **support})
        labels = [
            row
            for row in observations
            if str(row["capture_date"])[:10] >= WINDOW_START
        ]
        replay = replay_adaptive(
            anchor_id=anchor["anchor_id"],
            region_key=str(anchor.get("region_key") or "cape_town"),
            grid_id=anchor["grid_id"],
            census_date=str(anchor.get("census_date") or "")[:10],
            labels=labels,
        )
        adaptive_rows.append(replay)
        # plan_rechecks is imported so the audit path uses the same caps as the runner.
        plan_rechecks(interval, observations)
    old_intervals, old_sequences = _load_old([row["anchor_id"] for row in anchors])
    comparisons = []
    for interval in intervals:
        anchor_id = str(interval["anchor_id"])
        census = next(row["census_date"] for row in anchors if row["anchor_id"] == anchor_id)
        recovered, raw_dates = _recover_raw(old_sequences.get(anchor_id), census[:10])
        comparison = compare_old_interval(
            interval,
            old_intervals.get(anchor_id),
            raw_sequence_recovered=recovered,
            raw_dates=raw_dates,
        )
        comparison["anchor_id"] = anchor_id
        comparisons.append(comparison)
    quality = _quality(candidates, anchors, manifest)
    paired = _paired_rows(raw, anchors, manifest)
    audit = audit_tables(
        manifest=manifest,
        raw_records=raw,
        intervals=intervals,
        requests=requests,
        frozen_model_version=frozen_version,
    )
    status_counts = Counter(str(row.get("status") or "") for row in intervals)
    narrower = sum(1 for row in comparisons if row.get("width_change") == "narrower")
    wider = sum(1 for row in comparisons if row.get("width_change") == "wider")
    same = sum(1 for row in comparisons if row.get("width_change") == "same_width")
    year_changed = sum(int(row.get("year_bound_changed") or 0) for row in comparisons)
    recovered_n = sum(1 for row in comparisons if row.get("old_raw_sequence") == "recovered")
    old_present = sum(int(row.get("old_interval_present") or 0) for row in comparisons)
    effect_rows = [row for row in paired if int(row["in_effect_comparison"])]
    disagree = sum(1 for row in effect_rows if not int(row["verdict_same"]))
    diagnosed = [row["grid_id"] for row in quality if int(row["diagnosed_below_95"])]
    lock = json.loads((plan_dir / "run_lock.json").read_text(encoding="utf-8"))
    summary = dict(lock["summary"])
    summary.update(
        {
            "valid_response_rate": audit["valid_response_rate"],
            "candidate_byte_agreement": audit["candidate_byte_agreement"],
            "accuracy_pass": False,
            "what_changed": (
                f"同一 2019 窗口内，和新发表区间比，更窄 {narrower} 个、更宽 {wider} 个、同宽 {same} 个；"
                f"发表表能对上的目标 {old_present} 个。旧原始序列按 complete 且日期与原始码等长找回 {recovered_n} 条，"
                f"其余是 not_recovered，没有补造 Top-52 序列。"
                f"配对样本里航拍可用的日期 {len(effect_rows)} 个，其中两帧和三帧判决不同的 {disagree} 个。"
                f"质量低于 95% 的网格：{','.join(diagnosed) if diagnosed else '无'}。这些网格仍留在分母里。"
            ),
            "install_year_effect": (
                f"有界区间的年份端点发生变化的目标 {year_changed} 个。"
                f"左删失 {status_counts['left_censored']} 个，没有把 2019-01-01 或第一张影像写成安装日。"
                f"航拍上界 {status_counts['aerial_upper_bound']} 个，上界精度是 policy_census_bound。"
            ),
            "undatable_targets": (
                f"冲突待复核 {status_counts['conflict_pending_review']}，"
                f"时间线未完成 {status_counts['incomplete_timeline']}，"
                f"不能定年 {status_counts['undated']}，"
                f"只有未知或影像缺口的目标留在 undated / incomplete，没有被写成 absent。"
            ),
        }
    )
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    _write_csv(results / "intervals_raw.csv", intervals, INTERVAL_FIELDS)
    support_fields = INTERVAL_FIELDS + ["support", "n_compared", "n_agreed", "disagreement_dates"]
    _write_csv(results / "intervals_repeat_supported.csv", supported, support_fields)
    _write_csv(results / "intervals_adjudicated.csv", [], INTERVAL_FIELDS)
    _write_csv(results / "paired_comparison.csv", paired, [
        "anchor_id", "capture_date", "t2_verdict", "t3_verdict", "verdict_same",
        "candidate_sha256_t2", "candidate_sha256_t3", "candidate_bytes_same", "in_effect_comparison",
    ])
    _write_csv(results / "adaptive_replay.csv", adaptive_rows, [
        "anchor_id", "status", "picked_dates", "skipped_dates", "n_rounds",
        "excluded_pre_2019", "reference_frames_used_as_evidence",
    ])
    _write_csv(results / "interval_comparison.csv", comparisons, sorted({key for row in comparisons for key in row}))
    _write_csv(results / "quality_by_grid.csv", quality, [
        "grid_id", "n_downloadable", "n_quality_pass", "quality_pass_rate", "diagnosed_below_95",
    ])
    (results / "conclusion.md").write_text(build_conclusion(summary), encoding="utf-8")
    ledger_rows = 0
    ledger_path = root / "quota_ledger.jsonl"
    if ledger_path.is_file():
        with ledger_path.open(encoding="utf-8") as handle:
            ledger_rows = sum(1 for line in handle if line.strip())
    budget = {}
    budget_path = plan_dir / "budget.json"
    if budget_path.is_file():
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
    payload = {
        "audit": audit,
        "summary": {
            "N": summary["N"],
            "H": summary["H"],
            "H_pair": summary["H_pair"],
            "n_grids": summary["n_grids"],
            "n_development_grids": summary["n_development_grids"],
            "n_evaluation_grids": summary["n_evaluation_grids"],
            "seed": summary["seed"],
            "accuracy_pass": False,
        },
        "budget": {
            "status": budget.get("status", ""),
            "work_budget": budget.get("work_budget", ""),
            "gross_safe_budget": budget.get("gross_safe_budget", ""),
            "measured_prompt_tokens_mean": budget.get("measured_prompt_tokens_mean", ""),
            "measured_output_tokens_mean": budget.get("measured_output_tokens_mean", ""),
            "measured_latency_s_mean": budget.get("measured_latency_s_mean", ""),
            "max_output_tokens": budget.get("max_output_tokens", ""),
            "illustrative_example_used_as_count": 0,
        },
        "quota_ledger_rows": ledger_rows,
        "n_intervals": len(intervals),
        "status_counts": dict(sorted(status_counts.items())),
        "quality_grids_below_95": diagnosed,
        "n_grids_in_plan": len(grids),
    }
    (results / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (results / "ledger_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"audit": audit, "summary": summary, "n_grids": len(grids)}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = evaluate(args.output_root)
    audit = result["audit"]
    summary = result["summary"]
    sys.stdout.write(
        "\n".join(
            [
                f"audit_ok={str(bool(audit['ok'])).lower()}",
                f"valid_response_rate={float(audit['valid_response_rate']):.6f}",
                f"candidate_byte_agreement={float(audit['candidate_byte_agreement']):.6f}",
                f"foreign_model_versions={int(audit['foreign_model_versions'])}",
                f"reference_as_evidence={int(audit['reference_as_evidence'])}",
                f"raw_overwrites={int(audit['raw_overwrites'])}",
                "accuracy_pass=false",
                f"n_grids={int(summary['n_grids'])}",
            ]
        )
        + "\n"
    )
    if not audit["ok"]:
        print("audit_failures=" + ",".join(audit["failures"]), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
