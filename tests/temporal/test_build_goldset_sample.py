"""ISSUE-10 WP-B — stratified sampler for the gold-set jump-point adjudication.

Covers the acceptance criteria + test plan of
docs/replan_v2/ISSUE-10-design-2026-07-05.md:

* AC "Sampler seeded/reproducible; uses contradiction flags when present,
  degrades gracefully when absent": two runs, same `--seed --n`, identical
  `sample_assignments.csv`; different seed -> different (but valid) draw;
  degrade path with the cohort CSV absent (no crash, `any_contradiction=""`,
  stratum falls back to `status_x_confidence`).
* AC "Double-annotation overlap emitted with annotator assignment": overlap
  fraction of the quota is ~= `--overlap-frac`, both A and B rows per overlap
  anchor.
* "Dispute force-include": every owning c-anchor present with
  `is_dispute_forced=True` (both A and B) exactly once despite the
  many-to-one collapse (t1001+t1002 -> c0003); the union of
  `dispute_target_ids` equals the input dispute set; an unlocatable dispute
  (t2999) lands in `dispute_resolution_report.csv` without crashing the draw;
  forced anchors sit outside the stratified quota.
* Stratum proportional allocation sums to exactly `n`.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.temporal import build_goldset_sample as bgs
from scripts.temporal import goldset_schema

FIXTURES = Path(__file__).parent / "fixtures" / "goldset_sample"
INTERVALS_CSV = FIXTURES / "intervals.csv"
COHORT_CSV = FIXTURES / "cohort_audit.csv"
DISPUTES_CSV = FIXTURES / "disputes.csv"
CHIPGROUPS_CSV = FIXTURES / "chipgroups.csv"


def _run_cli(monkeypatch, tmp_path: Path, *, n: int, seed: int, tag: str,
             overlap_frac: float = 0.20, use_cohort: bool = True,
             use_disputes: bool = True) -> Path:
    argv = [
        "build_goldset_sample.py",
        "--intervals-csv", str(INTERVALS_CSV),
        "--n", str(n),
        "--seed", str(seed),
        "--overlap-frac", str(overlap_frac),
        "--tag", tag,
        "--data-root", str(tmp_path),
    ]
    if use_cohort:
        argv += ["--cohort-audit-csv", str(COHORT_CSV)]
    if use_disputes:
        argv += ["--disputes-csv", str(DISPUTES_CSV), "--chipgroups-csv", str(CHIPGROUPS_CSV)]
    monkeypatch.setattr("sys.argv", argv)
    bgs.main()
    return goldset_schema.goldset_root(tag, base=tmp_path)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_assignments(monkeypatch, tmp_path: Path) -> None:
    root_a = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="batch_a")
    root_b = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="batch_b")

    text_a = goldset_schema.sample_assignment_path(root_a).read_text()
    text_b = goldset_schema.sample_assignment_path(root_b).read_text()
    # Only the batch tag column differs by construction; strip it before compare.
    rows_a = [{k: v for k, v in r.items() if k != "sample_batch_id"}
              for r in csv.DictReader(text_a.splitlines())]
    rows_b = [{k: v for k, v in r.items() if k != "sample_batch_id"}
              for r in csv.DictReader(text_b.splitlines())]
    assert rows_a == rows_b


def test_different_seed_produces_different_draw(monkeypatch, tmp_path: Path) -> None:
    root_a = _run_cli(monkeypatch, tmp_path, n=10, seed=1, tag="seed1")
    root_b = _run_cli(monkeypatch, tmp_path, n=10, seed=2, tag="seed2")

    rows_a = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root_a))
    rows_b = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root_b))
    anchors_a = {r["anchor_id"] for r in rows_a if r["is_dispute_forced"] != "True"}
    anchors_b = {r["anchor_id"] for r in rows_b if r["is_dispute_forced"] != "True"}
    assert anchors_a != anchors_b


# ---------------------------------------------------------------------------
# Stratum allocation + overlap fraction
# ---------------------------------------------------------------------------


def test_stratum_allocation_sums_to_n(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=7, tag="stratsum")
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    quota_rows = [r for r in rows if r["is_dispute_forced"] != "True"]
    quota_anchors = {r["anchor_id"] for r in quota_rows}
    assert len(quota_anchors) == 10


def test_overlap_fraction_and_both_annotators_present(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=7, tag="overlap", overlap_frac=0.20)
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    quota_rows = [r for r in rows if r["is_dispute_forced"] != "True"]

    by_anchor: dict[str, list[dict]] = {}
    for r in quota_rows:
        by_anchor.setdefault(r["anchor_id"], []).append(r)

    overlap_anchors = [a for a, rs in by_anchor.items() if rs[0]["is_overlap"] == "True"]
    # 20% of 10 quota anchors = 2.
    assert len(overlap_anchors) == 2
    for a in overlap_anchors:
        annotators = {r["annotator_id"] for r in by_anchor[a]}
        assert annotators == {"A", "B"}

    single_anchors = [a for a, rs in by_anchor.items() if rs[0]["is_overlap"] == "False"]
    for a in single_anchors:
        assert len(by_anchor[a]) == 1
    # Round-robin split is present across singles (not all dumped on one annotator).
    single_annotators = {by_anchor[a][0]["annotator_id"] for a in single_anchors}
    assert single_annotators == {"A", "B"}


# ---------------------------------------------------------------------------
# Graceful degradation (no cohort file)
# ---------------------------------------------------------------------------


def test_degrade_path_no_cohort_file_falls_back_to_two_way_stratum(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="nocohort", use_cohort=False, use_disputes=False)
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    assert rows
    for r in rows:
        assert r["any_contradiction"] == ""
        assert r["stratum"] == f"{r['terminal_status']}_x_{r['confidence']}"
        assert "_x_True" not in r["stratum"] and "_x_False" not in r["stratum"]


def test_degrade_is_per_anchor_when_cohort_partially_covers_population(monkeypatch, tmp_path: Path) -> None:
    """Fixture cohort covers c0001-c0030 only; c0031-c0040 are outside it (mirrors
    the ~3,669 production anchors outside the cohort). Those anchors individually
    degrade even though the cohort file itself is present."""
    root = _run_cli(monkeypatch, tmp_path, n=40, seed=42, tag="partial", use_disputes=False)
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    out_of_cohort = [r for r in rows if r["anchor_id"] >= "c0031"]
    assert out_of_cohort, "expected at least one anchor outside the cohort in a full-population draw"
    for r in out_of_cohort:
        assert r["any_contradiction"] == ""
        assert r["stratum"] == f"{r['terminal_status']}_x_{r['confidence']}"


# ---------------------------------------------------------------------------
# Dispute force-include
# ---------------------------------------------------------------------------


def test_dispute_many_to_one_collapse_and_dedup(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="disputes")
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    forced = [r for r in rows if r["is_dispute_forced"] == "True"]

    forced_anchor_ids = {r["anchor_id"] for r in forced}
    assert forced_anchor_ids == {"c0003", "c0010"}, "t1001+t1002 collapse to c0003; t1003 -> c0010"

    # Exactly one A row and one B row per forced anchor -- no duplicate/conflicting rows.
    by_anchor: dict[str, list[dict]] = {}
    for r in forced:
        by_anchor.setdefault(r["anchor_id"], []).append(r)
    for anchor_id, rs in by_anchor.items():
        assert len(rs) == 2
        assert {r["annotator_id"] for r in rs} == {"A", "B"}

    # c0003 owns both t1001 and t1002 (many-to-one); dispute_target_ids carries both.
    c0003_targets = set(goldset_schema.decode_target_ids(by_anchor["c0003"][0]["dispute_target_ids"]))
    assert c0003_targets == {"t1001", "t1002"}
    c0010_targets = set(goldset_schema.decode_target_ids(by_anchor["c0010"][0]["dispute_target_ids"]))
    assert c0010_targets == {"t1003"}

    # Union of dispute_target_ids across forced rows == input dispute set (minus unresolved t2999).
    union = c0003_targets | c0010_targets
    assert union == {"t1001", "t1002", "t1003"}

    # Forced anchors sit outside the quota: never double-counted in the stratified draw.
    quota_anchor_ids = {r["anchor_id"] for r in rows if r["is_dispute_forced"] != "True"}
    assert quota_anchor_ids.isdisjoint(forced_anchor_ids)


def test_unresolved_dispute_written_to_report_without_crashing(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="unresolved")
    report_path = root / "dispute_resolution_report.csv"
    assert report_path.exists()
    rows = list(csv.DictReader(report_path.open()))
    assert {"target_id": "t2999", "reason": "no_owning_chipgroup"} in rows


def test_no_dispute_inputs_skips_force_include_without_crashing(monkeypatch, tmp_path: Path) -> None:
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="nodisputes", use_disputes=False)
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    assert all(r["is_dispute_forced"] != "True" for r in rows)
    assert not (root / "dispute_resolution_report.csv").exists()


# ---------------------------------------------------------------------------
# --n bounds + --dry-run
# ---------------------------------------------------------------------------


def test_n_out_of_range_raises(monkeypatch, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, tmp_path, n=5, seed=1, tag="toolow")
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, tmp_path, n=501, seed=1, tag="toohigh")


def test_dry_run_writes_nothing(monkeypatch, tmp_path: Path, capsys) -> None:
    tag = "dryrun"
    argv = [
        "build_goldset_sample.py",
        "--intervals-csv", str(INTERVALS_CSV),
        "--cohort-audit-csv", str(COHORT_CSV),
        "--disputes-csv", str(DISPUTES_CSV),
        "--chipgroups-csv", str(CHIPGROUPS_CSV),
        "--n", "10",
        "--seed", "42",
        "--tag", tag,
        "--data-root", str(tmp_path),
        "--dry-run",
    ]
    monkeypatch.setattr("sys.argv", argv)
    bgs.main()

    root = goldset_schema.goldset_root(tag, base=tmp_path)
    assert not root.exists()
    out = capsys.readouterr().out
    assert "Stratum allocation" in out
    assert "c0003" in out and "c0010" in out


# ---------------------------------------------------------------------------
# Unit-level: allocation math (isolated from CLI/IO)
# ---------------------------------------------------------------------------


def test_allocate_quota_proportional_with_largest_remainder() -> None:
    records = bgs.build_anchor_records(INTERVALS_CSV, {})
    counts = bgs.allocate_quota(records, 10)
    assert sum(counts.values()) == 10
    assert set(counts) == {
        "done_appears_x_high", "done_appears_x_medium", "done_appears_x_low",
        "done_ambiguous_no_recent_anchor_x_high", "done_ambiguous_no_recent_anchor_x_medium",
        "done_ambiguous_no_recent_anchor_x_low",
        "done_already_present_before_geid_history_x_high",
        "done_already_present_before_geid_history_x_medium",
        "done_already_present_before_geid_history_x_low",
    }


def test_draw_quota_is_deterministic_for_fixed_seed() -> None:
    records = bgs.build_anchor_records(INTERVALS_CSV, {})
    counts = bgs.allocate_quota(records, 10)
    draw1 = bgs.draw_quota(records, counts, seed=99)
    draw2 = bgs.draw_quota(records, counts, seed=99)
    assert [r.anchor_id for r in draw1] == [r.anchor_id for r in draw2]
