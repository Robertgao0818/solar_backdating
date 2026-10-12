#!/usr/bin/env python3
"""Rescore every v2 T3 install date with gemini-3.5-flash-lite.

The finished gemini-3.1-flash-lite ledger stays where it is. This process
reads that pack's request PNGs and writes a new raw log, status table, and
interval table. It does not send T2, and it does not treat model agreement
as accuracy.

Re-running this command continues the new ledger. It does not delete raw
rows, locks, or the PNG cache.
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

from scripts.temporal.reference_fulltimeline import (  # noqa: E402
    MODEL_ALIAS,
    PIXEL_UNINFORMATIVE_DATES,
)
from scripts.temporal.run_reference_fulltimeline_scan import (  # noqa: E402
    DEFAULT_OUTPUT,
    SCORE_IN_FLIGHT,
    RawLog,
    Scorer,
    _drain,
    _hold_lock,
    _load_v2_plan,
    _log,
    _pending,
    _preflight,
    _v2_open_dates,
    _write_json,
    requested_model_alias,
)
from scripts.temporal.score_v2_fulltimeline import (  # noqa: E402
    EXPECTED_CALLABLE,
    EXPECTED_ROOFS,
    LITE_PLAN,
    V2_PACK,
    build_job,
    smoke_selection,
    write_products,
)


COMPARE_ALIAS = "gemini-3.5-flash-lite"
COMPARE_RUN_ID = "reference_fulltimeline_v2_10grid_20261005_gemini35_flash_lite"
COMPARE_ROOT = Path.home() / "zasolar_data" / "geid_temporal" / COMPARE_RUN_ID
EXPECTED_T3_CALLS = EXPECTED_CALLABLE - len(PIXEL_UNINFORMATIVE_DATES)
BASELINE_FILES = (
    "observations/raw.jsonl",
    "observations/SCORE_DONE.json",
    "results/t3_status.csv",
    "results/intervals_t3.csv",
)
VERDICT_FIELDS = [
    "anchor_id",
    "grid_id",
    "grid_role",
    "capture_date",
    "t3_status_31",
    "t3_status_35",
    "model_version_31",
    "model_version_35",
    "n_images_31",
    "n_images_35",
    "candidate_sha_agree",
    "aerial_sha_agree",
    "satellite_sha_agree",
    "same_images",
    "same_status",
    "agreement_is_accuracy",
]
INTERVAL_FIELDS = [
    "anchor_id",
    "grid_id",
    "grid_role",
    "status_31",
    "status_35",
    "same_status",
    "interval_start_31",
    "interval_end_31",
    "interval_start_35",
    "interval_end_35",
    "labels_modified_31",
    "labels_modified_35",
    "agreement_is_accuracy",
]


def refuse_compare_output(root: Path, *, forbidden: Sequence[Path]) -> None:
    """Stop if the new ledger would land inside a finished imagery tree."""

    resolved = root.resolve()
    for blocked in forbidden:
        other = blocked.resolve()
        overlaps = resolved == other or resolved.is_relative_to(other) or other.is_relative_to(resolved)
        if overlaps:
            raise SystemExit(
                f"refusing comparison output {resolved}; it overlaps protected root {other}"
            )


def require_finished_baseline(baseline: Path, *, expected: Path = V2_PACK) -> None:
    """The comparison target is the finished 3.1 pack, including its products."""

    if baseline.resolve() != expected.resolve():
        raise SystemExit(f"baseline {baseline} is not the finished 3.1 pack {expected}")
    missing = [name for name in BASELINE_FILES if not (baseline / name).is_file()]
    if missing:
        raise SystemExit(f"finished 3.1 product missing: {missing}")


def baseline_stamp(baseline: Path) -> dict[str, dict[str, int]]:
    """Size, mtime, and inode of the files this comparison must not rewrite."""

    stamped: dict[str, dict[str, int]] = {}
    for name in BASELINE_FILES:
        stat = (baseline / name).stat()
        stamped[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
    return stamped


def remember_baseline(output: Path, baseline: Path) -> None:
    """Record the 3.1 files, and stop on a later run if they moved."""

    current = baseline_stamp(baseline)
    path = output / "plan" / "baseline_stamp.json"
    if not path.is_file():
        _write_json(path, {"files": current})
        return
    prior = json.loads(path.read_text(encoding="utf-8")).get("files") or {}
    changed = sorted(name for name in BASELINE_FILES if prior.get(name) != current[name])
    if changed:
        raise SystemExit(
            "finished 3.1 files changed since this comparison started: " + ", ".join(changed)
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def _index_rows(
    rows: Sequence[Mapping[str, str]],
    key_fields: Sequence[str],
) -> dict[tuple[str, ...], Mapping[str, str]]:
    indexed: dict[tuple[str, ...], Mapping[str, str]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "")[:10] if field == "capture_date" else str(row.get(field) or "") for field in key_fields)
        if key in indexed:
            raise SystemExit(f"duplicate comparison key {key}")
        indexed[key] = row
    return indexed


def _hash_flags(baseline: Mapping[str, str], compare: Mapping[str, str]) -> dict[str, int]:
    pairs = {
        "candidate_sha_agree": ("candidate_sha256", "candidate_sha256"),
        "aerial_sha_agree": ("aerial_sha256", "aerial_sha256"),
        "satellite_sha_agree": ("satellite_sha256", "satellite_sha256"),
    }
    flags = {
        name: int(str(baseline.get(left) or "") == str(compare.get(right) or ""))
        for name, (left, right) in pairs.items()
    }
    flags["same_images"] = int(all(flags.values()))
    return flags


def compare_status_rows(
    baseline_rows: Sequence[Mapping[str, str]],
    compare_rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join terminal T3 labels. Agreement on the same hashes is not accuracy."""

    baseline = _index_rows(baseline_rows, ("anchor_id", "capture_date"))
    compare = _index_rows(compare_rows, ("anchor_id", "capture_date"))
    joined = sorted(set(baseline) & set(compare))
    rows: list[dict[str, Any]] = []
    crosstab: Counter[str] = Counter()
    by_role: dict[str, Counter[str]] = {}
    n_same_images = 0
    n_same_status_same_images = 0
    for key in joined:
        left = baseline[key]
        right = compare[key]
        flags = _hash_flags(left, right)
        same_status = int(str(left.get("t3_status") or "") == str(right.get("t3_status") or ""))
        role = str(right.get("grid_role") or left.get("grid_role") or "")
        row = {
            "anchor_id": key[0],
            "grid_id": right.get("grid_id") or left.get("grid_id") or "",
            "grid_role": role,
            "capture_date": key[1],
            "t3_status_31": left.get("t3_status") or "",
            "t3_status_35": right.get("t3_status") or "",
            "model_version_31": left.get("model_version") or "",
            "model_version_35": right.get("model_version") or "",
            "n_images_31": left.get("n_images") or "",
            "n_images_35": right.get("n_images") or "",
            **flags,
            "same_status": same_status,
            "agreement_is_accuracy": 0,
        }
        rows.append(row)
        if not flags["same_images"]:
            continue
        n_same_images += 1
        n_same_status_same_images += same_status
        crosstab[f"{row['t3_status_31']}|{row['t3_status_35']}"] += 1
        role_counts = by_role.setdefault(role, Counter())
        role_counts["n"] += 1
        role_counts["same_status" if same_status else "different_status"] += 1
    summary = {
        "n_baseline": len(baseline),
        "n_compare": len(compare),
        "n_joined": len(joined),
        "n_unjoined_baseline": len(set(baseline) - set(compare)),
        "n_unjoined_compare": len(set(compare) - set(baseline)),
        "n_same_images": n_same_images,
        "n_hash_mismatch": len(joined) - n_same_images,
        "n_same_status_same_images": n_same_status_same_images,
        "n_different_status_same_images": n_same_images - n_same_status_same_images,
        "agreement_is_accuracy": 0,
        "crosstab_same_images": dict(sorted(crosstab.items())),
        "by_role_same_images": {role: dict(by_role[role]) for role in sorted(by_role)},
    }
    return rows, summary


def _modified(value: object) -> int:
    return int(str(value or "0") not in {"0", ""})


def compare_interval_rows(
    baseline_rows: Sequence[Mapping[str, str]],
    compare_rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join install-interval statuses. A conflict is left unrepaired."""

    baseline = _index_rows(baseline_rows, ("anchor_id",))
    compare = _index_rows(compare_rows, ("anchor_id",))
    joined = sorted(set(baseline) & set(compare))
    rows: list[dict[str, Any]] = []
    crosstab: Counter[str] = Counter()
    n_same = 0
    n_modified = 0
    for key in joined:
        left = baseline[key]
        right = compare[key]
        same_status = int(str(left.get("status") or "") == str(right.get("status") or ""))
        n_same += same_status
        modified = _modified(left.get("labels_modified")) or _modified(right.get("labels_modified"))
        n_modified += modified
        row = {
            "anchor_id": key[0],
            "grid_id": right.get("grid_id") or left.get("grid_id") or "",
            "grid_role": right.get("grid_role") or left.get("grid_role") or "",
            "status_31": left.get("status") or "",
            "status_35": right.get("status") or "",
            "same_status": same_status,
            "interval_start_31": left.get("interval_start") or "",
            "interval_end_31": left.get("interval_end") or "",
            "interval_start_35": right.get("interval_start") or "",
            "interval_end_35": right.get("interval_end") or "",
            "labels_modified_31": left.get("labels_modified") or "",
            "labels_modified_35": right.get("labels_modified") or "",
            "agreement_is_accuracy": 0,
        }
        rows.append(row)
        crosstab[f"{row['status_31']}|{row['status_35']}"] += 1
    summary = {
        "n_baseline": len(baseline),
        "n_compare": len(compare),
        "n_joined": len(joined),
        "n_same_status": n_same,
        "n_different_status": len(joined) - n_same,
        "n_labels_modified": n_modified,
        "agreement_is_accuracy": 0,
        "crosstab": dict(sorted(crosstab.items())),
    }
    return rows, summary


def write_model_comparison(baseline: Path, compare: Path) -> dict[str, Any]:
    """Write the joined tables. The caller writes SCORE_DONE only after this."""

    verdict_rows, verdicts = compare_status_rows(
        _read_csv(baseline / "results" / "t3_status.csv"),
        _read_csv(compare / "results" / "t3_status.csv"),
    )
    interval_rows, intervals = compare_interval_rows(
        _read_csv(baseline / "results" / "intervals_t3.csv"),
        _read_csv(compare / "results" / "intervals_t3.csv"),
    )
    if verdicts["n_baseline"] != EXPECTED_CALLABLE or verdicts["n_compare"] != EXPECTED_CALLABLE:
        raise SystemExit(
            f"comparison status tables are not {EXPECTED_CALLABLE} callable dates: {verdicts['n_baseline']} vs {verdicts['n_compare']}"
        )
    if verdicts["n_joined"] != EXPECTED_CALLABLE:
        raise SystemExit(f"comparison joined {verdicts['n_joined']} of {EXPECTED_CALLABLE} dates")
    if intervals["n_joined"] != EXPECTED_ROOFS:
        raise SystemExit(f"comparison joined {intervals['n_joined']} of {EXPECTED_ROOFS} roofs")
    if intervals["n_labels_modified"] != 0:
        raise SystemExit("labels_modified is not 0; conflicts were not left as recorded")
    summary = {
        "agreement_is_accuracy": 0,
        "baseline_model": MODEL_ALIAS,
        "compare_model": COMPARE_ALIAS,
        "verdicts": verdicts,
        "intervals": intervals,
        "note": (
            "Same terminal labels on the same image hashes are model agreement. "
            "They are not evidence that either label is correct."
        ),
    }
    results = compare / "results"
    _write_csv(results / "compare_verdicts.csv", verdict_rows, VERDICT_FIELDS)
    _write_csv(results / "compare_intervals.csv", interval_rows, INTERVAL_FIELDS)
    _write_json(results / "compare_summary.json", summary)
    return summary


def _compare_budget(root: Path) -> dict[str, Any]:
    """T3-only call budget. H_pair stays 0 so T2 is not reserved."""

    summary = {
        "N": EXPECTED_ROOFS,
        "H": EXPECTED_CALLABLE,
        "H_pair": 0,
        "n_paired_anchors": 0,
        "n_review_anchors": 0,
    }
    from scripts.temporal.run_reference_fulltimeline_scan import _budget_numbers

    payload = {
        **summary,
        **_budget_numbers(summary),
        "run_id": COMPARE_RUN_ID,
        "status": "provisional_call_budget",
        "frozen": False,
        "model_alias": COMPARE_ALIAS,
        "temperature": 0,
        "max_output_tokens": 4096,
        "thinking_level": "",
        "image_preprocessing": "png_lossless_same_grid",
        "illustrative_example_used_as_count": 0,
    }
    _write_json(root / "plan" / "budget.json", payload)
    if requested_model_alias(payload) != COMPARE_ALIAS:
        raise SystemExit("comparison budget did not keep gemini-3.5-flash-lite")
    return payload


def _check_noncalls(noncalls: Sequence[Mapping[str, Any]], *, smoke: bool) -> None:
    if smoke:
        if noncalls:
            raise SystemExit("smoke dates are not sendable")
        return
    found = {(str(row["anchor_id"]), str(row["capture_date"])[:10]) for row in noncalls}
    if found != set(PIXEL_UNINFORMATIVE_DATES):
        raise SystemExit(f"unexpected non-calls: {len(noncalls)}")
    if any(str(row.get("call_status") or "") != "pixel_uninformative" for row in noncalls):
        raise SystemExit("a callable date was withheld for a reason other than pixel_uninformative")


def _ok_t3(raw_rows: Sequence[Mapping[str, Any]], keys: set[tuple[str, str]] | None = None) -> list[Mapping[str, Any]]:
    found = []
    for row in raw_rows:
        if str(row.get("arm") or "") != "t3" or str(row.get("stage") or "") != "first_pass":
            continue
        if str(row.get("call_status") or "") != "ok":
            continue
        key = (str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])
        if keys is not None and key not in keys:
            continue
        found.append(row)
    return found


def _check_smoke(raw_rows: Sequence[Mapping[str, Any]], keys: set[tuple[str, str]]) -> None:
    """The full window starts only after these four bodies have three images."""

    found = {
        (str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10]): row
        for row in _ok_t3(raw_rows, keys)
    }
    problems: list[str] = []
    for anchor_id, day in sorted(keys):
        row = found.get((anchor_id, day))
        if row is None or int(row.get("n_images") or 0) != 3:
            problems.append(f"{anchor_id[-8:]} {day} images={None if row is None else row.get('n_images')}")
            continue
        if list(row.get("image_roles") or []) != ["candidate", "aerial_reference", "satellite_reference"]:
            problems.append(f"{anchor_id[-8:]} {day} roles={row.get('image_roles')}")
        elif any(int(size) <= 0 for size in row.get("image_bytes") or []):
            problems.append(f"{anchor_id[-8:]} {day} empty image")
        elif str(row.get("model_version") or "") != COMPARE_ALIAS:
            problems.append(f"{anchor_id[-8:]} {day} model={row.get('model_version')}")
        elif str(row.get("finish_reason") or "") == "MAX_TOKENS":
            problems.append(f"{anchor_id[-8:]} {day} truncated")
    t2 = [
        row
        for row in raw_rows
        if str(row.get("arm") or "") == "t2"
        and (str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10]) in keys
    ]
    if t2:
        problems.append(f"t2 rows={len(t2)}")
    if problems:
        raise SystemExit("smoke refused the full window: " + "; ".join(problems))


def _assert_model_bodies(raw_rows: Sequence[Mapping[str, Any]]) -> None:
    versions = {str(row.get("model_version") or "") for row in _ok_t3(raw_rows)}
    if versions != {COMPARE_ALIAS}:
        raise SystemExit(f"successful T3 model versions are {sorted(versions)}")
    one_image = sum(1 for row in _ok_t3(raw_rows) if int(row.get("n_images") or 0) == 1)
    if one_image:
        raise SystemExit(f"one-image successful T3 calls={one_image}")
    t2_ok = [
        row
        for row in raw_rows
        if str(row.get("arm") or "") == "t2" and str(row.get("call_status") or "") == "ok"
    ]
    if t2_ok:
        raise SystemExit(f"T3-only run stored {len(t2_ok)} successful T2 calls")


def _require_smoke_record(root: Path) -> None:
    path = root / "plan" / "SMOKE_OK.json"
    if not path.is_file():
        raise SystemExit("refusing the full window until --smoke has passed in this output root")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("model_version") != COMPARE_ALIAS or int(payload.get("n_images") or 0) != 3:
        raise SystemExit("smoke record is not gemini-3.5-flash-lite with three images")


def run(*, smoke: bool, output_root: Path, baseline_root: Path, plan_dir: Path) -> None:
    refuse_compare_output(output_root, forbidden=(V2_PACK, DEFAULT_OUTPUT, baseline_root))
    require_finished_baseline(baseline_root)
    if V2_PACK.resolve() != baseline_root.resolve():
        raise SystemExit("image reads and the 3.1 baseline must be the same finished pack")
    output_root.mkdir(parents=True, exist_ok=True)
    lock = _hold_lock(output_root / "plan" / "runner.lock")
    try:
        remember_baseline(output_root, baseline_root)
        if not smoke:
            _require_smoke_record(output_root)
        plan = _load_v2_plan(plan_dir)
        budget = _compare_budget(output_root)
        raw = RawLog(output_root / "observations" / "raw.jsonl")
        scorer = Scorer(output_root, raw, budget, run_id=COMPARE_RUN_ID, window_id=COMPARE_RUN_ID)
        version = _preflight(output_root, scorer)
        if version != COMPARE_ALIAS:
            raise SystemExit(f"preflight modelVersion {version!r} is not {COMPARE_ALIAS}")
        only = smoke_selection(plan["anchors"], plan["candidates"]) if smoke else None
        _log(f"[compare] building T3 requests from the finished pack smoke={int(smoke)}")
        # PNGs stay in the finished pack. build_job must not be pointed at output_root.
        noncalls, requests = build_job(V2_PACK, plan, only=only)
        t3_requests = [row for row in requests if str(row.get("arm") or "") == "t3"]
        other_arms = sorted({str(row.get("arm") or "") for row in requests if str(row.get("arm") or "") != "t3"})
        _check_noncalls(noncalls, smoke=smoke)
        expected = 4 if smoke else EXPECTED_T3_CALLS
        if len(t3_requests) != expected:
            raise SystemExit(f"T3 requests {len(t3_requests)} != {expected}")
        for row in noncalls:
            scorer.raw.commit(row)
        pending = _pending(t3_requests, scorer.raw)
        _log(
            f"[compare] t3={len(t3_requests)} pending={len(pending)} "
            f"noncalls={len(noncalls)} withheld_arms={other_arms}"
        )
        _drain(scorer, t3_requests, in_flight=SCORE_IN_FLIGHT)
        if smoke:
            _check_smoke(scorer.raw.rows, only or set())
            _write_json(
                output_root / "plan" / "SMOKE_OK.json",
                {
                    "smoke_ok": 1,
                    "model_version": COMPARE_ALIAS,
                    "n_dates": len(only or ()),
                    "n_images": 3,
                    "score_done": 0,
                },
            )
            _log("[compare] smoke passed; SCORE_DONE was not written")
            sys.stdout.write(
                "\n".join(
                    [
                        "stage=gemini35_t3_smoke",
                        "smoke_ok=1",
                        "score_done=0",
                        f"model_version={COMPARE_ALIAS}",
                        "n_images=3",
                        f"n_dates={len(only or ())}",
                    ]
                )
                + "\n"
            )
            return
        still_open = _pending(t3_requests, scorer.raw)
        open_dates = _v2_open_dates(plan, scorer.raw.rows)
        if still_open or open_dates:
            raise SystemExit(
                f"scoring stopped with pending_requests={len(still_open)} pending_callable_t3={open_dates}"
            )
        remember_baseline(output_root, baseline_root)
        _assert_model_bodies(scorer.raw.rows)
        _log("[compare] writing T3 status and intervals")
        write_products(output_root, plan, scorer.raw.rows, write_paired=False)
        paired = output_root / "results" / "paired_comparison.csv"
        if paired.exists():
            raise SystemExit("T3-only run wrote a paired comparison")
        summary = write_model_comparison(baseline_root, output_root)
        remember_baseline(output_root, baseline_root)
        verdicts = summary["verdicts"]
        _write_json(
            output_root / "observations" / "SCORE_DONE.json",
            {
                "pending_manifest": 0,
                "terminal_t3_count": EXPECTED_CALLABLE,
                "n_requests": len(t3_requests),
                "model_alias": COMPARE_ALIAS,
                "agreement_is_accuracy": 0,
                "n_same_status_same_images": verdicts["n_same_status_same_images"],
                "n_different_status_same_images": verdicts["n_different_status_same_images"],
                "n_hash_mismatch": verdicts["n_hash_mismatch"],
            },
        )
        sys.stdout.write(
            "\n".join(
                [
                    "stage=gemini35_t3",
                    f"terminal_t3_count={EXPECTED_CALLABLE}",
                    f"n_requests={len(t3_requests)}",
                    f"model_version={COMPARE_ALIAS}",
                    f"n_same_status_same_images={verdicts['n_same_status_same_images']}",
                    f"n_different_status_same_images={verdicts['n_different_status_same_images']}",
                    f"n_hash_mismatch={verdicts['n_hash_mismatch']}",
                    "agreement_is_accuracy=0",
                ]
            )
            + "\n"
        )
    finally:
        lock.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=COMPARE_ROOT)
    parser.add_argument("--baseline-root", type=Path, default=V2_PACK)
    parser.add_argument("--plan-dir", type=Path, default=LITE_PLAN)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    run(
        smoke=args.smoke,
        output_root=args.output_root,
        baseline_root=args.baseline_root,
        plan_dir=args.plan_dir,
    )


if __name__ == "__main__":
    main()
