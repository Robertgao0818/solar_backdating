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

Also covers WI-2 (docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md "WI-2" —
villa-suspect grid oversample knob):

* AC-2.1 oversample reweights: same `--seed --n`, with vs without
  `--oversample-grid-ids` (F=3), the count of drawn quota anchors whose
  `grid_id` is in the oversample set is strictly greater with the knob on.
* AC-2.2 reproducible: two runs, identical `--seed --n --oversample-grid-ids
  --oversample-factor` -> identical `sample_assignments.csv` rows.
* AC-2.3 no-op default (golden): without the flags, `sample_assignments.csv`
  rows are byte-identical to a golden captured from the pre-WI-2 sampler.
* AC-2.4 degrade untouched: oversample on + `--cohort-audit-csv` absent -> no
  crash, stratum keys are `status_x_confidence[_os]`; an oversample file whose
  grid_ids match zero population anchors -> stderr warning, no crash, output
  identical to off.
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
OVERSAMPLE_GRIDS_TXT = FIXTURES / "oversample_grids.txt"
OVERSAMPLE_GRIDS_ZERO_MATCH_TXT = FIXTURES / "oversample_grids_zero_match.txt"
GOLDEN_NOOP_CSV = FIXTURES / "golden_noop_sample_assignments.csv"


def _run_cli(monkeypatch, tmp_path: Path, *, n: int, seed: int, tag: str,
             overlap_frac: float = 0.20, use_cohort: bool = True,
             use_disputes: bool = True, oversample_grid_ids: Path | None = None,
             oversample_factor: float | None = None) -> Path:
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
    if oversample_grid_ids is not None:
        argv += ["--oversample-grid-ids", str(oversample_grid_ids)]
    if oversample_factor is not None:
        argv += ["--oversample-factor", str(oversample_factor)]
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


# ---------------------------------------------------------------------------
# WI-2 — villa-suspect grid oversample knob
# (docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md "WI-2", ACs 2.1-2.4)
# ---------------------------------------------------------------------------


def _oversample_grid_id_set(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def _rows_without_batch_id(path: Path) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "sample_batch_id"} for r in csv.DictReader(path.open())]


def test_ac2_3_no_op_default_matches_golden(monkeypatch, tmp_path: Path) -> None:
    """AC-2.3: without --oversample-grid-ids/--oversample-factor, the sampler
    is byte-identical to the pre-WI-2 sampler (golden captured before the
    oversample knob existed, same --seed/--n/--tag/--overlap-frac)."""
    root = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="golden_noop")
    got = list(csv.DictReader(goldset_schema.sample_assignment_path(root).open()))
    golden = list(csv.DictReader(GOLDEN_NOOP_CSV.open()))
    assert got == golden


def test_ac2_1_oversample_reweights_pulls_strictly_more_matching_anchors(monkeypatch, tmp_path: Path) -> None:
    """AC-2.1: same --seed/--n, with (F=3) vs without --oversample-grid-ids,
    the count of drawn quota anchors whose grid_id is in the oversample set is
    strictly greater with the knob on."""
    oversample_ids = _oversample_grid_id_set(OVERSAMPLE_GRIDS_TXT)

    root_off = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="os_off")
    root_on = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_on",
        oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=3.0,
    )

    def _match_count(root: Path) -> int:
        rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
        quota = [r for r in rows if r["is_dispute_forced"] != "True"]
        return sum(1 for r in quota if r["grid_id"] in oversample_ids)

    off_count = _match_count(root_off)
    on_count = _match_count(root_on)
    assert on_count > off_count, f"expected oversample knob to pull more matches: off={off_count} on={on_count}"


def test_ac2_2_oversample_is_reproducible_for_fixed_seed(monkeypatch, tmp_path: Path) -> None:
    """AC-2.2: two runs, identical --seed/--n/--oversample-grid-ids/
    --oversample-factor -> identical sample_assignments.csv rows."""
    root_a = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_rep_a",
        oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=3.0,
    )
    root_b = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_rep_b",
        oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=3.0,
    )
    rows_a = _rows_without_batch_id(goldset_schema.sample_assignment_path(root_a))
    rows_b = _rows_without_batch_id(goldset_schema.sample_assignment_path(root_b))
    assert rows_a == rows_b


def test_ac2_4_oversample_degrade_no_cohort_file_stratum_keys(monkeypatch, tmp_path: Path) -> None:
    """AC-2.4 (part 1): oversample on + --cohort-audit-csv absent -> no crash,
    stratum keys are `status_x_confidence[_os]` (contradiction axis still
    collapses to '', `_os` still composes onto whatever base key exists)."""
    root = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_nocohort",
        use_cohort=False, use_disputes=False,
        oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=3.0,
    )
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    assert rows
    for r in rows:
        assert r["any_contradiction"] == ""
        base = f"{r['terminal_status']}_x_{r['confidence']}"
        assert r["stratum"] in (base, f"{base}_os")
        assert "_x_True" not in r["stratum"] and "_x_False" not in r["stratum"]
    # The oversample fixture matches several JNB0001 anchors in this population,
    # so at least one drawn row should actually land in an `_os` sub-stratum.
    assert any(r["stratum"].endswith("_os") for r in rows)


def test_ac2_4_oversample_zero_match_warns_and_is_noop(monkeypatch, tmp_path: Path, capsys) -> None:
    """AC-2.4 (part 2): an oversample file whose grid_ids match zero anchors in
    the population -> stderr warning, no crash, output identical to off."""
    root_off = _run_cli(monkeypatch, tmp_path, n=10, seed=42, tag="os_zero_off")
    root_zero = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_zero_on",
        oversample_grid_ids=OVERSAMPLE_GRIDS_ZERO_MATCH_TXT, oversample_factor=3.0,
    )
    err = capsys.readouterr().err
    assert "matched zero anchors" in err

    rows_off = _rows_without_batch_id(goldset_schema.sample_assignment_path(root_off))
    rows_zero = _rows_without_batch_id(goldset_schema.sample_assignment_path(root_zero))
    assert rows_off == rows_zero


def test_oversample_factor_below_one_raises(monkeypatch, tmp_path: Path) -> None:
    """Supplementary (not a numbered AC): --oversample-factor must be >= 1.0."""
    with pytest.raises(SystemExit):
        _run_cli(
            monkeypatch, tmp_path, n=10, seed=42, tag="os_badfactor",
            oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=0.5,
        )


def test_forced_dispute_rows_are_never_oversampled(monkeypatch, tmp_path: Path) -> None:
    """Supplementary: c0010 (JNB0001, in the oversample set) is a forced dispute
    anchor -- `_make_stratum` still tags its stratum with `_os` (grid_id match
    is computed per-anchor, independent of dispute status), but the knob's
    *reweighting mechanism* (`allocate_quota`'s mass multiplier + the seeded
    per-stratum draw) never touches it: it sits outside `eligible`
    (`build_forced_rows`), so it is force-included exactly once per annotator
    regardless of `--oversample-factor`, never additionally drawn via the
    quota."""
    root = _run_cli(
        monkeypatch, tmp_path, n=10, seed=42, tag="os_forced",
        oversample_grid_ids=OVERSAMPLE_GRIDS_TXT, oversample_factor=3.0,
    )
    rows = goldset_schema.read_sample_assignments(goldset_schema.sample_assignment_path(root))
    forced = [r for r in rows if r["is_dispute_forced"] == "True"]
    forced_c0010 = [r for r in forced if r["anchor_id"] == "c0010"]
    assert len(forced_c0010) == 2  # exactly A + B, never re-drawn via the quota
    quota_anchor_ids = {r["anchor_id"] for r in rows if r["is_dispute_forced"] != "True"}
    assert "c0010" not in quota_anchor_ids


# ---------------------------------------------------------------------------
# WI-2 unit-level: allocate_quota mass reweighting (isolated from CLI/IO)
# ---------------------------------------------------------------------------


def test_allocate_quota_oversample_factor_boosts_os_stratum_share() -> None:
    oversample_ids = frozenset({"JNB0001"})
    records = bgs.build_anchor_records(INTERVALS_CSV, {}, oversample_ids)
    counts_off = bgs.allocate_quota(records, 10)
    counts_on = bgs.allocate_quota(records, 10, oversample_factor=3.0)

    os_total_off = sum(c for s, c in counts_off.items() if s.endswith("_os"))
    os_total_on = sum(c for s, c in counts_on.items() if s.endswith("_os"))
    assert os_total_on > os_total_off
    assert sum(counts_on.values()) == 10


def test_allocate_quota_no_os_stratum_unaffected_by_factor() -> None:
    """When no record is flagged for oversample (grid_ids don't match), the
    factor is inert -- identical counts regardless of its value."""
    records = bgs.build_anchor_records(INTERVALS_CSV, {})  # no oversample_grid_ids at all
    counts_default = bgs.allocate_quota(records, 10)
    counts_with_factor = bgs.allocate_quota(records, 10, oversample_factor=5.0)
    assert counts_default == counts_with_factor
