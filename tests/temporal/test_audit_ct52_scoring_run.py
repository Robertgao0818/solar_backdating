"""Focused contract tests for the Cape Town recursive scoring audit."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.temporal.audit_ct52_scoring_run import (
    audit_attempts,
    audit_ledger,
    audit_states,
)


ALIAS = "gemini-3.1-flash-lite"
SLOT = ("wave_02", "anchor-1", "1", "0")


def _attempt(logical_id: str, *, transport_retries: int = 0, alias: str = ALIAS) -> dict:
    return {
        "stage": "batch",
        "requested_alias": alias,
        "returned_model_version": ALIAS,
        "transport_retries": transport_retries,
        "attempt_context": {
            "logical_call_id": logical_id,
            "wave_id": SLOT[0],
            "anchor_id": SLOT[1],
            "round_id": SLOT[2],
            "chunk_index": SLOT[3],
        },
    }


def _ledger_row(
    logical_id: str,
    *,
    attempt_kind: str = "batch",
    transport_attempt: int = 0,
    wave_id: str = SLOT[0],
    anchor_id: str = SLOT[1],
    round_id: str = SLOT[2],
    chunk_index: str = SLOT[3],
) -> dict:
    return {
        "window_id": "window-1",
        "logical_call_id": logical_id,
        "attempt_kind": attempt_kind,
        "transport_attempt": transport_attempt,
        "requested_alias": ALIAS,
        "returned_model_version": ALIAS,
        "http_status": 200,
        "counted": True,
        "model_tier": "primary",
        "wave_id": wave_id,
        "anchor_id": anchor_id,
        "round_id": round_id,
        "chunk_index": chunk_index,
    }


def test_attempt_audit_deduplicates_replayed_logical_calls(tmp_path: Path) -> None:
    first = tmp_path / "first"
    replay = tmp_path / "replay"
    first.mkdir()
    replay.mkdir()
    payload = _attempt("call-1", transport_retries=1)
    first.joinpath("attempts.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    replay.joinpath("attempts.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    gate, records, http_attempts, logical_ids, slots = audit_attempts(
        [first, replay], expected_alias=ALIAS, expected_version=ALIAS
    )

    assert gate["pass"] is True
    assert records == 1
    assert http_attempts == 2
    assert gate["duplicate_logical_call_count"] == 1
    assert logical_ids == {"call-1"}
    assert slots == {SLOT}


def test_ledger_reports_canary_and_superseded_pause_lineage(tmp_path: Path) -> None:
    ledger = tmp_path / "quota_ledger.jsonl"
    rows = [
        _ledger_row("call-1", transport_attempt=0),
        _ledger_row("call-1", transport_attempt=1),
        _ledger_row("fresh-canary", attempt_kind="canary", wave_id="canary", anchor_id="__canary__"),
        _ledger_row("old-call", wave_id=SLOT[0], anchor_id=SLOT[1], round_id=SLOT[2], chunk_index=SLOT[3]),
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    gate = audit_ledger(
        ledger,
        expected_alias=ALIAS,
        expected_version=ALIAS,
        audit_record_count=1,
        expected_http_attempts=2,
        audit_dirs_present=True,
        audit_logical_ids={"call-1"},
        audit_slot_keys={SLOT},
    )

    assert gate["pass"] is True
    assert gate["matched_ledger_attempts"] == 2
    assert gate["standalone_canary_count"] == 1
    assert gate["superseded_ledger_count"] == 1


def test_state_audit_scopes_duplicate_anchor_by_arm(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/goldset_strip/scan_states/c0000542.json"
    for arm in ("a24", "a48"):
        arm_dir = tmp_path / arm
        arm_dir.mkdir()
        arm_dir.joinpath("c0000542.json").write_bytes(fixture.read_bytes())

    gate = audit_states(
        [tmp_path / "a24", tmp_path / "a48"],
        expected_anchor_ids={"c0000542"},
        census_date="2025-01-31",
        require_terminal=True,
    )

    assert gate["pass"] is True
    assert gate["file_count"] == 2
    assert gate["state_scope_count"] == 2
    assert gate["duplicate_state_ids"] == []
