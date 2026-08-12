#!/usr/bin/env python3
"""Read-only recursive audit for the Cape Town top-52 scoring contract.

The command is usable both before Gemini (CT-05 input/chip gate) and after a
primary or rescue run.  It never starts GEHI or Gemini and writes only the
requested JSON report.  A failed gate exits non-zero; skipped post-run gates
are explicit in the report rather than being treated as a silent pass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.run_ct05_chip_pipeline import inspect_raster
from scripts.temporal.scan_state import load_scan_state


NO_HISTORY = {
    "ct_full_inventory_2026_06_21_merged_t00010449",
    "ct_full_inventory_2026_06_21_merged_t00010452",
    "ct_full_inventory_2026_06_21_merged_t00038228",
    "ct_full_inventory_2026_06_21_merged_t00038233",
    "ct_full_inventory_2026_06_21_merged_t00038296",
    "ct_full_inventory_2026_06_21_merged_t00038301",
}
WAYBACK_ONLY = "ct_full_inventory_2026_06_21_merged_t00038314"
GEOMETRY_EXCEPTION = "ct_full_inventory_2026_06_21_merged_t00107162"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def gate(ok: bool, **details: Any) -> dict[str, Any]:
    return {"pass": bool(ok), **details}


def chip_path(chip_root: Path, row: dict[str, str], zoom: int) -> Path:
    anchor = row["anchor_id"]
    date = row["capture_date"][:10].replace("-", "")
    version = row.get("version", "").strip() or "noversion"
    return chip_root / anchor / f"z{zoom}" / f"{anchor}_{date}_v{version}.tif"


def audit_inputs(
    *,
    anchors_csv: Path,
    outcomes_csv: Path | None,
    catalog_csv: Path | None,
    chip_root: Path | None,
    expected_anchor_count: int,
    census_date: str,
    raster_sample: int,
    verify_all_rasters: bool,
    check_exception_roster: bool = True,
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, dict[str, str]]]:
    anchors = list(read_rows(anchors_csv))
    anchor_by_id = {row.get("anchor_id", "").strip(): row for row in anchors}
    anchor_ids = [row.get("anchor_id", "").strip() for row in anchors]
    anchor_set = set(anchor_ids)
    anchor_gate = gate(
        len(anchors) == expected_anchor_count
        and len(anchor_set) == len(anchors)
        and all(anchor_ids),
        actual_count=len(anchors),
        expected_count=expected_anchor_count,
        unique_count=len(anchor_set),
        sha256=sha256(anchors_csv),
    )

    outcomes: dict[str, dict[str, str]] = {}
    outcome_gate: dict[str, Any]
    if outcomes_csv is None:
        outcome_gate = {"pass": True, "status": "skipped"}
    else:
        outcomes = {row.get("anchor_id", "").strip(): row for row in read_rows(outcomes_csv)}
        status_counts = Counter(row.get("catalog_status", "") for row in outcomes.values())
        no_history = {a for a, row in outcomes.items() if row.get("catalog_status") == "no_history"}
        # A nested wave audit intentionally receives a subset manifest.  In
        # that mode the frozen six-anchor roster is not expected to be
        # present in full; it is still important to verify that every
        # no-history anchor belonging to the subset is represented correctly.
        expected_no_history = NO_HISTORY if check_exception_roster else NO_HISTORY & anchor_set
        outcome_gate = gate(
            set(outcomes) == anchor_set
            and no_history == expected_no_history
            and status_counts.get("no_history", 0) == len(expected_no_history),
            actual_count=len(outcomes),
            status_counts=dict(sorted(status_counts.items())),
            missing_from_outcomes=sorted(anchor_set - set(outcomes))[:20],
            extra_outcomes=sorted(set(outcomes) - anchor_set)[:20],
            no_history=sorted(no_history),
            expected_no_history=sorted(expected_no_history),
            no_history_roster_match=no_history == expected_no_history,
            sha256=sha256(outcomes_csv),
        )

    catalog_gate: dict[str, Any]
    if catalog_csv is None:
        catalog_gate = {"pass": True, "status": "skipped"}
    else:
        rows = 0
        catalog_anchor_ids: set[str] = set()
        keys: set[tuple[str, str]] = set()
        duplicate_keys: list[tuple[str, str]] = []
        provider_counts: Counter[str] = Counter()
        post_census_rows = 0
        missing_chip = 0
        empty_chip = 0
        zoom_counts: Counter[str] = Counter()
        sample_candidates: list[dict[str, str]] = []
        raster_checked = 0
        raster_failures: list[dict[str, Any]] = []
        for row in read_rows(catalog_csv):
            rows += 1
            anchor_id = row.get("anchor_id", "").strip()
            capture_date = row.get("capture_date", "").strip()[:10]
            key = (anchor_id, capture_date)
            if key in keys and len(duplicate_keys) < 20:
                duplicate_keys.append(key)
            keys.add(key)
            catalog_anchor_ids.add(anchor_id)
            provider_counts[row.get("provider", "").strip()] += 1
            if capture_date > census_date:
                post_census_rows += 1
            if chip_root is not None:
                paths = [chip_path(chip_root, row, zoom) for zoom in (20, 19, 18)]
                existing = [path for path in paths if path.is_file() and path.stat().st_size > 0]
                if not existing:
                    missing_chip += 1
                    if any(path.exists() and path.stat().st_size == 0 for path in paths):
                        empty_chip += 1
                else:
                    zoom_counts[f"z{next(zoom for zoom, path in zip((20, 19, 18), paths) if path in existing)}"] += 1
                if verify_all_rasters and existing:
                    raster_checked += 1
                    anchor = anchor_by_id.get(anchor_id)
                    if anchor is None:
                        raster_failures.append({"anchor_id": anchor_id, "error": "missing_anchor"})
                    else:
                        result = inspect_raster(existing[0], anchor)
                        if (
                            result.get("decode_status") != "pass"
                            or result.get("bbox_status") != "pass"
                            or result.get("pixel_quality_status") != "pass"
                        ):
                            raster_failures.append(
                                {"anchor_id": anchor_id, "capture_date": capture_date, "path": str(existing[0]), **result}
                            )
                elif raster_sample > 0:
                    # Deterministic bounded sample without retaining the full
                    # 1.18M-row catalog in memory.
                    token = int(hashlib.sha256(f"{anchor_id}|{capture_date}".encode()).hexdigest()[:12], 16)
                    if len(sample_candidates) < raster_sample:
                        sample_candidates.append(row)
                    elif token % max(1, rows) < raster_sample:
                        sample_candidates[token % raster_sample] = row
        if chip_root is not None and not verify_all_rasters and sample_candidates:
            for row in sample_candidates:
                paths = [chip_path(chip_root, row, zoom) for zoom in (20, 19, 18)]
                existing = next((path for path in paths if path.is_file() and path.stat().st_size > 0), None)
                if existing is None:
                    continue
                raster_checked += 1
                anchor = anchor_by_id.get(row.get("anchor_id", ""))
                if anchor is None:
                    raster_failures.append({"anchor_id": row.get("anchor_id"), "error": "missing_anchor"})
                    continue
                result = inspect_raster(existing, anchor)
                if result.get("decode_status") != "pass" or result.get("bbox_status") != "pass" or result.get("pixel_quality_status") != "pass":
                    raster_failures.append(
                        {"anchor_id": row.get("anchor_id"), "capture_date": row.get("capture_date"), "path": str(existing), **result}
                    )
        expected_catalog_anchors = anchor_set - NO_HISTORY
        catalog_gate = gate(
            catalog_anchor_ids == expected_catalog_anchors
            and not duplicate_keys
            and (chip_root is None or missing_chip == 0)
            and not raster_failures,
            row_count=rows,
            unique_anchor_count=len(catalog_anchor_ids),
            anchor_count_with_history=len(catalog_anchor_ids),
            expected_anchor_count_with_history=len(expected_catalog_anchors),
            catalog_coverage_missing=sorted(expected_catalog_anchors - catalog_anchor_ids)[:20],
            expected_no_history_absent=sorted(NO_HISTORY & anchor_set - catalog_anchor_ids),
            unexpected_catalog_anchors=sorted(catalog_anchor_ids - anchor_set)[:20],
            duplicate_anchor_date_keys=duplicate_keys,
            provider_counts=dict(sorted(provider_counts.items())),
            post_census_rows=post_census_rows,
            chip_root=str(chip_root) if chip_root else None,
            chip_missing_count=missing_chip if chip_root else None,
            chip_empty_count=empty_chip if chip_root else None,
            chip_existing_by_preferred_zoom=dict(sorted(zoom_counts.items())) if chip_root else {},
            raster_mode=("all" if verify_all_rasters else f"sample_{raster_sample}") if chip_root else "skipped",
            raster_checked=raster_checked if chip_root else 0,
            raster_failure_count=len(raster_failures),
            raster_failures=raster_failures[:20],
            sha256=sha256(catalog_csv),
        )

    if check_exception_roster:
        exception_gate = gate(
            GEOMETRY_EXCEPTION in anchor_set
            and WAYBACK_ONLY in anchor_set
            and NO_HISTORY <= anchor_set,
            no_history=sorted(NO_HISTORY),
            wayback_only=WAYBACK_ONLY,
            geometry_exception=GEOMETRY_EXCEPTION,
        )
    else:
        exception_gate = {"pass": True, "status": "skipped_subset"}
    return (
        {
            "anchors_bijection": anchor_gate,
            "catalog_outcomes_bijection": outcome_gate,
            "catalog_candidates": catalog_gate,
            "frozen_exception_roster": exception_gate,
        },
        anchors,
        outcomes,
    )


def audit_states(
    state_dirs: list[Path],
    *,
    expected_anchor_ids: set[str],
    census_date: str,
    require_terminal: bool,
) -> dict[str, Any]:
    if not state_dirs:
        return {"pass": True, "status": "skipped"}
    scoped_files: list[tuple[Path, Path]] = []
    for directory in state_dirs:
        scoped_files.extend((directory, path) for path in sorted(directory.glob("*.json")))
    states = []
    failures: list[dict[str, str]] = []
    duplicate_ids: list[str] = []
    seen_anchor_ids: set[str] = set()
    seen_scoped_ids: set[tuple[str, str]] = set()
    reference_leaks: list[dict[str, str]] = []
    reference_misflags: list[dict[str, str]] = []
    operational_failures: list[dict[str, str]] = []
    status_counts: Counter[str] = Counter()
    for directory, path in scoped_files:
        try:
            state = load_scan_state(path)
        except Exception as exc:  # noqa: BLE001 - report every bad state
            failures.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        assert state is not None
        states.append(state)
        status_counts[state.status] += 1
        if state.status.startswith(
            ("done_ambiguous_gemini_failed", "done_ambiguous_orchestrator_error")
        ):
            operational_failures.append(
                {"anchor_id": state.anchor_id, "status": state.status}
            )
        scope_key = (str(directory.resolve()), state.anchor_id)
        if scope_key in seen_scoped_ids:
            duplicate_ids.append(f"{directory.name}:{state.anchor_id}")
        seen_scoped_ids.add(scope_key)
        seen_anchor_ids.add(state.anchor_id)
        if require_terminal and not state.is_terminal:
            failures.append({"path": str(path), "error": f"non_terminal:{state.status}"})
        if state.census_date and state.census_date[:10] != census_date[:10]:
            failures.append({"path": str(path), "error": f"census_date={state.census_date}"})
        for rnd in state.rounds:
            for result in rnd.results:
                is_post = result.capture_date[:10] >= census_date[:10]
                if is_post and not result.reference_only:
                    reference_leaks.append(
                        {"anchor_id": state.anchor_id, "round_id": str(rnd.round_id), "capture_date": result.capture_date}
                    )
                if result.reference_only and (not is_post or rnd.round_type != "initial"):
                    reference_misflags.append(
                        {"anchor_id": state.anchor_id, "round_id": str(rnd.round_id), "capture_date": result.capture_date}
                    )
                result_error = getattr(result, "error", None)
                if result.decision_source == "gemini_failed" or result_error:
                    operational_failures.append(
                        {
                            "anchor_id": state.anchor_id,
                            "round_id": str(rnd.round_id),
                            "chip_index": str(result.chip_index),
                            "decision_source": result.decision_source,
                            "error": result_error or "",
                        }
                    )
    expected_ok = seen_anchor_ids <= expected_anchor_ids
    return gate(
        bool(scoped_files)
        and not failures
        and not duplicate_ids
        and expected_ok
        and not reference_leaks
        and not reference_misflags
        and not operational_failures,
        file_count=len(scoped_files),
        loaded_state_count=len(states),
        state_scope_count=len({str(directory.resolve()) for directory, _ in scoped_files}),
        status_counts=dict(sorted(status_counts.items())),
        missing_expected=sorted(expected_anchor_ids - seen_anchor_ids)[:20],
        unexpected_state_ids=sorted(seen_anchor_ids - expected_anchor_ids)[:20],
        duplicate_state_ids=sorted(set(duplicate_ids)),
        failures=failures[:20],
        reference_evidence_leaks=reference_leaks[:20],
        reference_flag_errors=reference_misflags[:20],
        operational_failure_count=len(operational_failures),
        operational_failures=operational_failures[:20],
    )


def audit_attempts(
    audit_dirs: list[Path], *, expected_alias: str, expected_version: str
) -> tuple[dict[str, Any], int, int, set[str], set[tuple[str, str, str, str]]]:
    if not audit_dirs:
        return {"pass": True, "status": "skipped"}, 0, 0, set(), set()
    paths = []
    for directory in audit_dirs:
        paths.extend(sorted(directory.rglob("*.jsonl")))
    records = 0
    expected_http_attempts = 0
    errors: list[dict[str, Any]] = []
    stage_counts: Counter[str] = Counter()
    logical_ids: set[str] = set()
    slot_keys: set[tuple[str, str, str, str]] = set()
    duplicate_logical_ids = 0
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append({"path": str(path), "line": line_no, "error": str(exc)})
                    continue
                context = row.get("attempt_context") or {}
                logical_id = str(context.get("logical_call_id", "")).strip()
                if logical_id and logical_id in logical_ids:
                    # gate_retry2 retains the earlier audit files alongside
                    # the retry files. Identical logical calls are lineage,
                    # not additional scorer attempts.
                    duplicate_logical_ids += 1
                    requested = str(row.get("requested_alias", "")).strip()
                    returned = str(row.get("returned_model_version", "")).strip()
                    if requested != expected_alias or returned != expected_version:
                        errors.append(
                            {
                                "path": str(path),
                                "line": line_no,
                                "requested_alias": requested,
                                "returned_model_version": returned,
                                "error": "duplicate logical call has model identity drift",
                            }
                        )
                    continue
                if logical_id:
                    logical_ids.add(logical_id)
                records += 1
                try:
                    expected_http_attempts += 1 + int(row.get("transport_retries", 0) or 0)
                except (TypeError, ValueError):
                    errors.append({"path": str(path), "line": line_no, "error": "invalid transport_retries"})
                stage_counts[str(row.get("stage", ""))] += 1
                requested = str(row.get("requested_alias", "")).strip()
                returned = str(row.get("returned_model_version", "")).strip()
                slot_keys.add(
                    (
                        str(context.get("wave_id", "")),
                        str(context.get("anchor_id", "")),
                        str(context.get("round_id", "")),
                        str(context.get("chunk_index", "")),
                    )
                )
                if requested != expected_alias or returned != expected_version:
                    errors.append(
                        {
                            "path": str(path),
                            "line": line_no,
                            "requested_alias": requested,
                            "returned_model_version": returned,
                        }
                    )
    return gate(
        bool(paths) and not errors,
        audit_file_count=len(paths),
        attempt_record_count=records,
        unique_logical_call_count=len(logical_ids),
        duplicate_logical_call_count=duplicate_logical_ids,
        stage_counts=dict(sorted(stage_counts.items())),
        identity_errors=errors[:20],
        expected_requested_alias=expected_alias,
        expected_model_version=expected_version,
        expected_http_attempts=expected_http_attempts,
    ), records, expected_http_attempts, logical_ids, slot_keys


def audit_ledger(
    ledger_path: Path | None,
    *,
    expected_alias: str,
    expected_version: str,
    audit_record_count: int,
    expected_http_attempts: int,
    audit_dirs_present: bool,
    audit_logical_ids: set[str],
    audit_slot_keys: set[tuple[str, str, str, str]],
) -> dict[str, Any]:
    if ledger_path is None:
        return {"pass": not audit_dirs_present, "status": "skipped" if not audit_dirs_present else "missing"}
    if not ledger_path.exists():
        return gate(not audit_dirs_present, status="missing", path=str(ledger_path), row_count=0)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_attempts: set[tuple[str, str, str]] = set()
    standalone_canary_rows: list[dict[str, Any]] = []
    superseded_rows: list[dict[str, Any]] = []
    matched_rows = 0
    with ledger_path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_no}: {exc}")
                continue
            rows.append(row)
            required = ("window_id", "logical_call_id", "attempt_kind", "transport_attempt", "requested_alias", "counted")
            for field in required:
                if field not in row:
                    errors.append(f"line {line_no}: missing {field}")
            key = (str(row.get("logical_call_id", "")), str(row.get("attempt_kind", "")), str(row.get("transport_attempt", "")))
            if key in seen_attempts:
                errors.append(f"line {line_no}: duplicate attempt key {key}")
            seen_attempts.add(key)
            if str(row.get("requested_alias", "")) != expected_alias:
                errors.append(f"line {line_no}: requested_alias drift")
            returned = row.get("returned_model_version")
            status = row.get("http_status")
            if status is not None and 200 <= int(status) < 300 and str(returned or "") != expected_version:
                errors.append(f"line {line_no}: successful modelVersion drift")
            logical_id = str(row.get("logical_call_id", "")).strip()
            if logical_id in audit_logical_ids:
                matched_rows += 1
            elif str(row.get("attempt_kind", "")) == "canary":
                # run_ct_lite_canary_v2 records its response in a dedicated
                # canary JSON artifact, not scorer audit JSONL.
                standalone_canary_rows.append(
                    {"line": line_no, "logical_call_id": logical_id, "window_id": row.get("window_id")}
                )
            else:
                slot_key = (
                    str(row.get("wave_id", "")),
                    str(row.get("anchor_id", "")),
                    str(row.get("round_id", "")),
                    str(row.get("chunk_index", "")),
                )
                if slot_key in audit_slot_keys:
                    # A quota pause can leave a ledger row from the old run
                    # while the same slot is reissued under a new run_id.
                    # Keep the old row as explicit superseded lineage.
                    superseded_rows.append(
                        {
                            "line": line_no,
                            "logical_call_id": logical_id,
                            "wave_id": row.get("wave_id"),
                            "anchor_id": row.get("anchor_id"),
                            "round_id": row.get("round_id"),
                            "chunk_index": row.get("chunk_index"),
                            "http_status": row.get("http_status"),
                            "error_class": row.get("error_class"),
                        }
                    )
                else:
                    errors.append(f"line {line_no}: ledger attempt has no matching audit or superseding slot")
    # Every audit record is one logical scorer attempt; transport_retries add
    # further ledger rows. Compare only rows belonging to the audited logical
    # calls; standalone canaries and superseded pause-lineage are reported
    # separately above.
    if audit_dirs_present and audit_record_count and matched_rows != expected_http_attempts:
        errors.append(
            "ledger/audit matched-attempt count mismatch: "
            f"matched_ledger={matched_rows} audit={expected_http_attempts}"
        )
    return gate(
        not errors,
        path=str(ledger_path),
        row_count=len(rows),
        audit_record_count=audit_record_count,
        expected_http_attempts=expected_http_attempts,
        matched_ledger_attempts=matched_rows,
        standalone_canary_count=len(standalone_canary_rows),
        standalone_canary_rows=standalone_canary_rows[:20],
        superseded_ledger_count=len(superseded_rows),
        superseded_ledger_rows=superseded_rows[:20],
        errors=errors[:20],
        model_tiers=dict(sorted(Counter(str(row.get("model_tier", "")) for row in rows).items())),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--catalog-outcomes-csv", type=Path)
    parser.add_argument("--catalog-csv", type=Path)
    parser.add_argument("--chip-root", type=Path)
    parser.add_argument("--scan-states-dir", type=Path, action="append", default=[])
    parser.add_argument("--audit-dir", type=Path, action="append", default=[])
    parser.add_argument("--quota-ledger", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-anchor-count", type=int, default=21453)
    parser.add_argument("--census-date", default="2025-01-31")
    parser.add_argument("--expected-requested-alias", default="gemini-3.1-flash-lite")
    parser.add_argument("--expected-model-version", default="gemini-3.1-flash-lite")
    parser.add_argument("--require-terminal", action="store_true")
    parser.add_argument("--chip-sample", type=int, default=256)
    parser.add_argument("--verify-all-rasters", action="store_true")
    parser.add_argument(
        "--skip-frozen-exception-roster",
        action="store_true",
        help="Skip the full CT-05 exception-roster gate for an explicitly scoped subset audit.",
    )
    args = parser.parse_args()

    input_gates, anchors, _ = audit_inputs(
        anchors_csv=args.anchors_csv,
        outcomes_csv=args.catalog_outcomes_csv,
        catalog_csv=args.catalog_csv,
        chip_root=args.chip_root,
        expected_anchor_count=args.expected_anchor_count,
        census_date=args.census_date,
        raster_sample=max(0, args.chip_sample),
        verify_all_rasters=args.verify_all_rasters,
        check_exception_roster=not args.skip_frozen_exception_roster,
    )
    anchor_ids = {row.get("anchor_id", "").strip() for row in anchors}
    state_gate = audit_states(
        args.scan_states_dir,
        expected_anchor_ids=anchor_ids,
        census_date=args.census_date,
        require_terminal=args.require_terminal,
    )
    (
        attempt_gate,
        audit_record_count,
        expected_http_attempts,
        audit_logical_ids,
        audit_slot_keys,
    ) = audit_attempts(
        args.audit_dir,
        expected_alias=args.expected_requested_alias,
        expected_version=args.expected_model_version,
    )
    ledger_gate = audit_ledger(
        args.quota_ledger,
        expected_alias=args.expected_requested_alias,
        expected_version=args.expected_model_version,
        audit_record_count=audit_record_count,
        expected_http_attempts=expected_http_attempts,
        audit_dirs_present=bool(args.audit_dir),
        audit_logical_ids=audit_logical_ids,
        audit_slot_keys=audit_slot_keys,
    )
    report = {
        "schema_version": "ct52_scoring_audit_v1",
        "census_date": args.census_date,
        "inputs": {"anchors_csv": str(args.anchors_csv), "catalog_csv": str(args.catalog_csv) if args.catalog_csv else None},
        "expected_model": {
            "requested_alias": args.expected_requested_alias,
            "model_version": args.expected_model_version,
        },
        "gates": {
            **input_gates,
            "scan_states": state_gate,
            "model_identity_attempt_audit": attempt_gate,
            "quota_ledger": ledger_gate,
        },
    }
    report["overall_pass"] = all(bool(value.get("pass")) for value in report["gates"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["overall_pass"] else 1)


if __name__ == "__main__":
    main()
