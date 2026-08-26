"""Tests for scripts/temporal/chip_lifecycle.py (ISSUE-13 chip release tool).

All offline: tmp_path fixtures for the run-dir tree, never touches real
zasolar_data or the GEHI binary.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.temporal.chip_lifecycle import (
    build_backfill_record,
    check_refusal_window,
    cmd_release,
    discover_provenance_sidecars,
    discover_scan_state_files,
    discover_tif_files,
    execute_release,
    nearest_sidecar,
    parse_chip_filename,
    plan_release,
)
from scripts.temporal.gehi_download import sha256_file


def _write_chip(path, content=b"fake-tif-bytes"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_provenance(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# filename / path parsing helpers


def test_parse_chip_filename_extracts_anchor_date_version_zoom(tmp_path):
    path = tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif"
    parsed = parse_chip_filename(path)
    assert parsed["anchor_id"] == "anchorA"
    assert parsed["capture_date"] == "2020-06-15"
    assert parsed["version"] == 296
    assert parsed["achieved_zoom"] == 19


def test_parse_chip_filename_unmatched_returns_empty_dict(tmp_path):
    path = tmp_path / "weird_name.tif"
    assert parse_chip_filename(path) == {}


def test_nearest_sidecar_picks_deepest_ancestor(tmp_path):
    chip = tmp_path / "rep0" / "L0" / "anchorA" / "z19" / "anchorA_20200615_v296.tif"
    outer = tmp_path / "chip_provenance.jsonl"
    inner = tmp_path / "rep0" / "L0" / "chip_provenance.jsonl"
    result = nearest_sidecar(chip, [outer, inner])
    assert result == inner


def test_nearest_sidecar_returns_none_when_no_ancestor(tmp_path):
    chip = tmp_path / "elsewhere" / "chip.tif"
    unrelated = tmp_path / "otherdir" / "chip_provenance.jsonl"
    assert nearest_sidecar(chip, [unrelated]) is None


# ---------------------------------------------------------------------------
# discovery


def test_discover_tif_files_finds_nested(tmp_path):
    _write_chip(tmp_path / "a" / "z19" / "a_20200101_v1.tif")
    _write_chip(tmp_path / "b" / "z20" / "b_20200102_v2.tif")
    (tmp_path / "note.txt").write_text("not a chip")
    found = discover_tif_files(tmp_path)
    assert len(found) == 2
    assert all(p.suffix == ".tif" for p in found)


def test_discover_scan_state_files_matches_scan_states_dir_only(tmp_path):
    (tmp_path / "scan_states").mkdir()
    (tmp_path / "scan_states" / "a1.json").write_text("{}")
    (tmp_path / "scan_states_failed_backup_2026").mkdir()
    (tmp_path / "scan_states_failed_backup_2026" / "a2.json").write_text("{}")
    found = discover_scan_state_files(tmp_path)
    assert len(found) == 1
    assert found[0].name == "a1.json"


# ---------------------------------------------------------------------------
# refusal window


def test_refusal_window_blocks_recent_scan_state(tmp_path):
    (tmp_path / "scan_states").mkdir()
    state = tmp_path / "scan_states" / "a1.json"
    state.write_text("{}")
    now = time.time()
    offending = check_refusal_window(tmp_path, force=False, now=now)
    assert offending == state


def test_refusal_window_allows_old_scan_state(tmp_path):
    (tmp_path / "scan_states").mkdir()
    state = tmp_path / "scan_states" / "a1.json"
    state.write_text("{}")
    far_future = time.time() + 3600  # simulate "now" being an hour after mtime
    assert check_refusal_window(tmp_path, force=False, now=far_future) is None


def test_refusal_window_force_bypasses_recent_scan_state(tmp_path):
    (tmp_path / "scan_states").mkdir()
    (tmp_path / "scan_states" / "a1.json").write_text("{}")
    assert check_refusal_window(tmp_path, force=True, now=time.time()) is None


def test_refusal_window_no_scan_states_is_fine(tmp_path):
    assert check_refusal_window(tmp_path, force=False, now=time.time()) is None


# ---------------------------------------------------------------------------
# plan_release: backfill detection


def test_plan_flags_chip_without_provenance_for_backfill(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    plan = plan_release(tmp_path)
    assert len(plan.to_delete) == 1
    assert len(plan.to_backfill) == 1
    assert plan.to_delete[0].path == chip


def test_plan_skips_backfill_when_provenance_already_covers_chip(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    sidecar = tmp_path / "chip_provenance.jsonl"
    _write_provenance(sidecar, [{"chip_path": str(chip.resolve()), "chip_sha256": sha256_file(chip)}])
    plan = plan_release(tmp_path)
    assert len(plan.to_delete) == 1
    assert len(plan.to_backfill) == 0


def test_plan_uses_nearest_sidecar_for_nested_runs(tmp_path):
    chip_inner = _write_chip(tmp_path / "rep0" / "anchorA" / "z19" / "anchorA_20200101_v1.tif")
    inner_sidecar = tmp_path / "rep0" / "chip_provenance.jsonl"
    _write_provenance(
        inner_sidecar, [{"chip_path": str(chip_inner.resolve()), "chip_sha256": sha256_file(chip_inner)}]
    )
    outer_sidecar = tmp_path / "chip_provenance.jsonl"
    _write_provenance(outer_sidecar, [])  # exists but doesn't cover chip_inner

    plan = plan_release(tmp_path)
    assert len(plan.to_backfill) == 0  # covered by the nearer (inner) sidecar
    entry = plan.to_delete[0]
    assert entry.sidecar == inner_sidecar


# ---------------------------------------------------------------------------
# execute_release: backfill hashing + deletion manifest + actual deletion


def test_execute_release_backfills_hashes_writes_manifest_and_deletes(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    expected_sha = sha256_file(chip)
    plan = plan_release(tmp_path)

    result = execute_release(plan, dry_run=False)

    assert result.deleted == [chip]
    assert not chip.exists()

    sidecar = tmp_path / "chip_provenance.jsonl"
    assert sidecar.exists()
    lines = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["chip_sha256"] == expected_sha
    assert lines[0]["status"] == "backfilled_by_chip_lifecycle"
    assert lines[0]["anchor_id"] == "anchorA"
    assert lines[0]["capture_date"] == "2020-06-15"
    assert lines[0]["version"] == 296

    manifest = tmp_path / "deletion_manifest.jsonl"
    assert manifest.exists()
    manifest_lines = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(manifest_lines) == 1
    assert manifest_lines[0]["chip_sha256"] == expected_sha
    assert manifest_lines[0]["path"] == str(chip)
    assert manifest_lines[0]["size_bytes"] == len(b"fake-tif-bytes")
    assert "deleted_at" in manifest_lines[0]


def test_execute_release_does_not_duplicate_backfill_when_already_provenanced(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    sha = sha256_file(chip)
    sidecar = tmp_path / "chip_provenance.jsonl"
    _write_provenance(sidecar, [{"chip_path": str(chip.resolve()), "chip_sha256": sha, "status": "ok"}])

    plan = plan_release(tmp_path)
    result = execute_release(plan, dry_run=False)

    assert result.backfilled == []
    assert not chip.exists()
    lines = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(lines) == 1  # unchanged: the pre-existing record, no backfill appended

    manifest = tmp_path / "deletion_manifest.jsonl"
    manifest_lines = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert manifest_lines[0]["chip_sha256"] == sha


def test_execute_release_dry_run_touches_nothing(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    plan = plan_release(tmp_path)
    result = execute_release(plan, dry_run=True)
    assert result.deleted == []
    assert result.backfilled == []
    assert chip.exists()
    assert not (tmp_path / "chip_provenance.jsonl").exists()
    assert not (tmp_path / "deletion_manifest.jsonl").exists()


def test_build_backfill_record_shape_matches_chip_provenance_fields():
    from scripts.temporal.gehi_download import CHIP_PROVENANCE_FIELDS

    record = build_backfill_record(Path("x/z19/x_20200101_v1.tif"), "deadbeef")
    assert set(record.keys()) == set(CHIP_PROVENANCE_FIELDS)
    assert record["chip_sha256"] == "deadbeef"


# ---------------------------------------------------------------------------
# CLI-level: cmd_release wiring (refusal, dry-run, force)


def _write_backup_ok(tmp_path):
    import json as _json

    payload = {
        "schema_version": "ct_citywide_backup_ok_v1",
        "archive_root": "dropbox:test/archive",
        "volumes": [{"volume": "part01.tar", "gnu_sha256": "0" * 64}],
    }
    path = tmp_path / "BACKUP_OK.json"
    path.write_text(_json.dumps(payload))
    return path


def test_cmd_release_refuses_without_backup_ok(tmp_path, capsys):
    _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    exit_code = cmd_release(tmp_path, dry_run=False, force=True)
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "backup-ok" in captured.err.lower()
    assert (tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif").exists()


def test_cmd_release_anchor_filter_restricts_deletion(tmp_path):
    chip_a = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    chip_b = _write_chip(tmp_path / "anchorB" / "z19" / "anchorB_20200615_v296.tif")
    ids = tmp_path / "ids.txt"
    ids.write_text("anchor_id\nanchorA\n")

    exit_code = cmd_release(
        tmp_path,
        dry_run=False,
        force=True,
        backup_ok=_write_backup_ok(tmp_path),
        anchor_ids_file=ids,
    )
    assert exit_code == 0
    assert not chip_a.exists()
    assert chip_b.exists()


def test_cmd_release_refuses_on_recent_scan_state(tmp_path, capsys):
    _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    (tmp_path / "scan_states").mkdir()
    (tmp_path / "scan_states" / "a1.json").write_text("{}")

    exit_code = cmd_release(
        tmp_path, dry_run=False, force=False, backup_ok=_write_backup_ok(tmp_path)
    )
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "refusing" in captured.err.lower()
    # chip must still exist: refused before any deletion
    assert (tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif").exists()


def test_cmd_release_force_overrides_refusal(tmp_path):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    (tmp_path / "scan_states").mkdir()
    (tmp_path / "scan_states" / "a1.json").write_text("{}")

    exit_code = cmd_release(
        tmp_path, dry_run=False, force=True, backup_ok=_write_backup_ok(tmp_path)
    )
    assert exit_code == 0
    assert not chip.exists()


def test_cmd_release_dry_run_reports_plan_without_deleting(tmp_path, capsys):
    chip = _write_chip(tmp_path / "anchorA" / "z19" / "anchorA_20200615_v296.tif")
    exit_code = cmd_release(tmp_path, dry_run=True, force=False)
    assert exit_code == 0
    assert chip.exists()
    captured = capsys.readouterr()
    assert "dry-run" in captured.out.lower()


def test_cmd_release_missing_run_dir_errors(tmp_path, capsys):
    exit_code = cmd_release(tmp_path / "does_not_exist", dry_run=False, force=False)
    assert exit_code != 0
