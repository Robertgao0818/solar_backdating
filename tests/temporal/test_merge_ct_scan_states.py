"""Synthetic-fixture tests for the CT scan-state merge (Leg-R R1).

Fixtures are tiny tmp JSON files — never the 21k production states.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.temporal.merge_ct_scan_states import merge_scan_states, sha256_file
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)


def _result(capture_date: str, *, present: bool) -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=0,
        pv_present=present,
        confidence=0.9,
        quality_flag="usable",
        decision_source="gemini_batch",
        actual_zoom=20,
    )


def _write_state(
    directory: Path,
    anchor_id: str,
    *,
    present: bool = True,
    started_at: str = "2026-01-01T00:00:00Z",
    notes: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    state = ScanState(
        anchor_id=anchor_id,
        region_key="cape_town",
        grid_id="CPT0001",
        status="done_appears" if present else "done_installed_during_census",
        started_at=started_at,
        updated_at=started_at,
        notes=notes,
    )
    results = (
        [_result("2020-04-15", present=False), _result("2020-08-15", present=True)]
        if present
        else [_result("2024-03-01", present=False)]
    )
    state.rounds = [
        Round(
            round_id=1,
            round_type="initial",
            window_start_date=results[0].capture_date,
            window_end_date=results[-1].capture_date,
            picks=[
                Pick(chip_index=i + 1, capture_date=row.capture_date, version=0, requested_zoom=20)
                for i, row in enumerate(results)
            ],
            results=results,
            completed=True,
        )
    ]
    path = state_path_for(anchor_id, directory)
    save_scan_state(state, path)
    # save_scan_state overwrites updated_at; restamp so timestamp-only tests
    # control both volatile fields.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["started_at"] = started_at
    payload["updated_at"] = started_at
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_merge_dedupes_identical_bytes(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    canary = tmp_path / "canary"
    src = _write_state(primary, "anchor_a")
    (canary).mkdir()
    dest_dup = canary / "anchor_a.json"
    dest_dup.write_bytes(src.read_bytes())
    _write_state(canary, "anchor_b", present=False)

    out = tmp_path / "merged"
    result = merge_scan_states([primary, canary], out, expected_count=2)

    assert result.n_input_files == 3
    assert result.n_unique_anchors == 2
    assert result.n_copied == 2
    assert len(result.identical_dups) == 1
    assert result.identical_dups[0]["anchor_id"] == "anchor_a"
    assert sha256_file(out / "anchor_a.json") == sha256_file(src)
    assert (out / "anchor_b.json").is_file()


def test_merge_timestamp_only_collision_keeps_winner(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    canary = tmp_path / "canary"
    _write_state(primary, "anchor_a", started_at="2026-01-01T00:00:00Z")
    _write_state(canary, "anchor_a", started_at="2026-07-31T05:34:51Z")

    out = tmp_path / "merged"
    result = merge_scan_states([primary, canary], out, expected_count=1)

    assert result.n_copied == 1
    assert len(result.timestamp_only_dups) == 1
    payload = json.loads((out / "anchor_a.json").read_text(encoding="utf-8"))
    assert payload["started_at"] == "2026-01-01T00:00:00Z"


def test_merge_fails_loud_on_canonical_byte_conflict(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    retry = tmp_path / "retry"
    _write_state(primary, "anchor_a", notes="first")
    _write_state(retry, "anchor_a", notes="rescued")

    with pytest.raises(ValueError, match="canonical-byte collision"):
        merge_scan_states([primary, retry], tmp_path / "merged")


def test_merge_allow_supersede_keeps_earlier_scope(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    original = tmp_path / "original"
    _write_state(primary, "anchor_a", notes="retry_winner")
    _write_state(original, "anchor_a", notes="original_loser")

    out = tmp_path / "merged"
    result = merge_scan_states(
        [primary, original],
        out,
        allow_supersede=True,
        expected_count=1,
    )
    assert len(result.superseded) == 1
    payload = json.loads((out / "anchor_a.json").read_text(encoding="utf-8"))
    assert payload["notes"] == "retry_winner"


def test_merge_expected_count_mismatch(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    _write_state(primary, "anchor_a")
    with pytest.raises(ValueError, match="expected-count"):
        merge_scan_states([primary], tmp_path / "merged", expected_count=2)


def test_merge_refuses_nonempty_output(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    _write_state(primary, "anchor_a")
    out = tmp_path / "merged"
    out.mkdir()
    (out / "stale.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="non-empty"):
        merge_scan_states([primary], out)


def test_merge_cli_first_dir_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_state(left, "anchor_a", notes="left")
    _write_state(right, "anchor_a", notes="right")
    out = tmp_path / "merged"
    monkeypatch.setattr(
        "sys.argv",
        [
            "merge_ct_scan_states.py",
            "--scan-states-dir",
            str(left),
            "--scan-states-dir",
            str(right),
            "--output",
            str(out),
            "--allow-supersede",
            "--expected-count",
            "1",
        ],
    )
    from scripts.temporal.merge_ct_scan_states import main as merge_main

    merge_main()
    payload = json.loads((out / "anchor_a.json").read_text(encoding="utf-8"))
    assert payload["notes"] == "left"
    manifest = json.loads((tmp_path / "merge_manifest.json").read_text(encoding="utf-8"))
    assert manifest["n_unique_anchors"] == 1
    assert manifest["n_superseded"] == 1
