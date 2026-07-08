"""CPU-only unit tests for the ISSUE-06 fidelity gate harness.

Two genuinely-new pieces get TDD coverage (the rest of ``fidelity_gate.py`` is
reuse of the already-tested decode / agg machinery):

1. ``posterior_to_agree_key`` — the posterior -> canonical key adapter. Every
   branch is asserted byte-for-byte against the production key semantics
   (``llm_endtoend_build_reference.agree_key`` :91 / ``agree_key_raw`` :56).
2. rep-vs-rep + student-vs-teacher agreement on a tiny synthetic fixture driving
   the full walk -> decode -> key -> agg path with an injected fake scorer
   (no GPU, no real weights), plus the ``--self-repro`` determinism check.

The gate itself is an offline benchmark: no real gate numbers are asserted here
(ISSUE-06 AC: reported in ``docs/``, not asserted in CI).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation import fidelity_gate as fg  # noqa: E402
from solar_backdating.estimators.seam import InstallDatePosterior  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. posterior -> agree_key adapter (highest-risk piece; every branch)
# --------------------------------------------------------------------------- #
def _mkpost(**kw) -> InstallDatePosterior:
    """A fully-populated InstallDatePosterior with only the key-relevant fields
    overridden — the adapter reads map_date / map_interval_start / map_interval_end."""
    from datetime import date

    base = dict(
        estimator="changepoint",
        epochs=(),
        posterior=(1.0,),
        map_index=0,
        map_interval_start=None,
        map_interval_end=None,
        p_undated=1.0,
        credible_low_date=None,
        credible_high_date=None,
        credible_mass=1.0,
        map_date="",
    )
    base.update(kw)
    # silence unused import when no date override supplied
    _ = date
    return InstallDatePosterior(**base)


def test_adapter_undated_empty_mapdate_no_bounds():
    # t==0 undated verdict: no observations / all-abstain -> map_date="" + both None.
    post = _mkpost(map_date="", map_interval_start=None, map_interval_end=None, p_undated=1.0)
    assert fg.posterior_to_agree_key(post) == "UNDATED"


def test_adapter_undated_beyond_window_last_absent_set():
    # map_index==t beyond-window: map_interval_start=last_absent (non-None),
    # map_interval_end=None, map_date="". Empty map_date still means UNDATED
    # (mirrors estimator_endtoend_decode: year = map_date[:4] if map_date else "").
    from datetime import date

    post = _mkpost(
        map_date="",
        map_interval_start=date(2020, 1, 1),
        map_interval_end=None,
        p_undated=0.72,
    )
    assert fg.posterior_to_agree_key(post) == "UNDATED"


def test_adapter_ap_bound_open_left():
    # Dated + open-left (map_index==0, start None) == production date_is_bound==1.
    from datetime import date

    post = _mkpost(
        map_date="2019-05-01",
        map_interval_start=None,
        map_interval_end=date(2019, 5, 1),
        p_undated=0.05,
    )
    assert fg.posterior_to_agree_key(post) == "AP_BOUND<=|2019-05-01"


def test_adapter_interval_both_bounds():
    from datetime import date

    post = _mkpost(
        map_date="2021-08-30",
        map_interval_start=date(2020, 3, 10),
        map_interval_end=date(2021, 8, 30),
        p_undated=0.0,
    )
    assert fg.posterior_to_agree_key(post) == "INTERVAL|2020-03-10|2021-08-30"


def test_adapter_interval_pundated_boundary_does_not_flip():
    # p_undated is NOT consumed by the key; a dated interval stays INTERVAL even at 0.0.
    from datetime import date

    post = _mkpost(
        map_date="2022-01-01",
        map_interval_start=date(2021, 1, 1),
        map_interval_end=date(2022, 1, 1),
        p_undated=0.0,
    )
    assert fg.posterior_to_agree_key(post) == "INTERVAL|2021-01-01|2022-01-01"


def test_adapter_clamp_inverted_blank_mapdate_is_undated():
    # clamp_inverted degenerate: map_date blanked to "" while bounds are non-None.
    # map_date is the discriminator (== year-extraction contract) -> UNDATED.
    from datetime import date

    post = _mkpost(
        map_date="",
        map_interval_start=date(2025, 1, 1),
        map_interval_end=date(2024, 1, 1),
        p_undated=0.3,
    )
    assert fg.posterior_to_agree_key(post) == "UNDATED"


# --------------------------------------------------------------------------- #
# 2. rep-vs-rep + student-vs-teacher on a synthetic fixture
# --------------------------------------------------------------------------- #
@dataclass
class _FakeObs:
    pv_present: object
    pv_score: float
    quality_flag: str


class _MirrorScorer:
    """Deterministic fake: decides present/absent from a tag in the chip filename,
    so it reproduces the teacher's per-frame verdict exactly (student == teacher)."""

    name = "fake_mirror"

    def score(self, picks, *, config=None, **_):  # noqa: ARG002
        out = []
        for p in picks:
            if "_P_" in p.chip_path:
                out.append(_FakeObs(True, 0.95, "usable"))
            elif "_A_" in p.chip_path:
                out.append(_FakeObs(False, 0.05, "usable"))
            else:
                out.append(_FakeObs(None, 0.5, "ambiguous"))
        return out


class _FlakyScorer:
    """Nondeterministic fake: flips ALL verdicts in a call each time ``score`` is
    invoked, so the same anchor decodes to all-present on one pass and all-absent
    on the next -> different decoded keys across the two self-repro passes."""

    name = "fake_flaky"

    def __init__(self):
        self.flip = False

    def score(self, picks, *, config=None, **_):  # noqa: ARG002
        self.flip = not self.flip
        pv = self.flip
        return [_FakeObs(pv, 0.9 if pv else 0.1, "usable") for _p in picks]


def _write_scan_state(path: Path, anchor_id: str, grid_id: str, frames: list[tuple[str, bool]]):
    """frames: list of (capture_date, pv_present). Chip filename encodes the tag
    (_P_/_A_) so the mirror scorer reproduces pv_present. Chip files are created
    (empty) so the existence check passes; the fake scorer never reads bytes."""
    chip_dir = path.parent.parent / "chips" / anchor_id
    chip_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (cdate, present) in enumerate(frames):
        tag = "_P_" if present else "_A_"
        chip = chip_dir / f"{anchor_id}{tag}{cdate.replace('-', '')}_{i}.tif"
        chip.write_bytes(b"")
        results.append(
            {
                "chip_index": i + 1,
                "capture_date": cdate,
                "pv_present": present,
                "confidence": 0.9 if present else 0.1,
                "quality_flag": "usable",
                "chip_path": str(chip),
            }
        )
    doc = {
        "anchor_id": anchor_id,
        "grid_id": grid_id,
        "rounds": [
            {
                "round_id": "r1",
                "results": results,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _write_reference(path: Path, rows: list[dict]):
    cols = [
        "group_anchor_id",
        "target_anchor_id",
        "source_feature_id",
        "grid_id",
        "status_stratum",
        "inv_weight",
        "prod_date_provider",
        "prod_agree_key",
    ]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r[c]) for c in cols))
    path.write_text("\n".join(lines) + "\n")


def _write_delivery(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "source_feature_id",
        "date_provider",
        "date_is_bound",
        "install_date",
        "install_interval_start",
        "install_interval_end",
        "earliest_present_date",
        "undated_reason",
    ]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    path.write_text("\n".join(lines) + "\n")


def _clean_absent_present_frames():
    # A clean absent -> present transition so the decoder yields a dated bracket.
    return [
        ("2018-06-01", False),
        ("2019-06-01", False),
        ("2020-06-01", True),
        ("2021-06-01", True),
    ]


@pytest.fixture()
def fixture(tmp_path):
    """3 anchors (sf 1,2,3), all gehi_main (L0). rep1 has scan states for A,B,C;
    rep2 has scan states for A,B only (C's L0 file missing -> splice-through)."""
    grid = "GRID01"
    anchors = {1: "anchA", 2: "anchB", 3: "anchC"}
    ref_rows = [
        {
            "group_anchor_id": anchors[sf],
            "target_anchor_id": f"t_{anchors[sf]}",
            "source_feature_id": sf,
            "grid_id": grid,
            "status_stratum": "done_appears",
            "inv_weight": 0.7,
            "prod_date_provider": "gehi_main",
            "prod_agree_key": "INTERVAL|x|y",
        }
        for sf in (1, 2, 3)
    ]
    ref_csv = tmp_path / "reference.csv"
    _write_reference(ref_csv, ref_rows)

    delivery_rows = [
        {"source_feature_id": sf, "date_provider": "gehi_main", "date_is_bound": 0}
        for sf in (1, 2, 3)
    ]

    rep1 = tmp_path / "rep1"
    rep2 = tmp_path / "rep2"
    for rep in (rep1, rep2):
        _write_delivery(rep / "delivery.csv", delivery_rows)

    frames = _clean_absent_present_frames()
    # rep1: A, B, C all present as L0 scan states
    for sf, anch in anchors.items():
        _write_scan_state(rep1 / "L0" / "scan_states" / f"{anch}.json", anch, grid, frames)
    # rep2: only A, B (C missing on disk -> splice-through fallback)
    for sf in (1, 2):
        anch = anchors[sf]
        _write_scan_state(rep2 / "L0" / "scan_states" / f"{anch}.json", anch, grid, frames)

    return {
        "tmp": tmp_path,
        "ref_csv": ref_csv,
        "reps": [rep1, rep2],
        "anchors": anchors,
    }


def test_student_mirrors_teacher_agreement_is_one(fixture):
    ref_rows = fg.load_reference_rows(fixture["ref_csv"])
    est, config = fg.build_decoder(emissions=None, cohort_prior=None)
    scorer = _MirrorScorer()
    stats = fg.new_stats()
    rep_dir = fixture["reps"][0]
    delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
    km = fg.rep_keys(
        rep_dir,
        ref_rows,
        delivery,
        est=est,
        config=config,
        vexcel_ceiling={},
        teacher_only=False,
        scorer=scorer,
        cache=None,
        scorer_key="fake_mirror|nohead",
        skip_missing=False,
        stats=stats,
    )
    # every decoded anchor's student key must equal the teacher key (mirror scorer)
    decoded = {sf: info for sf, info in km.items() if info["decoded"]}
    assert len(decoded) == 3
    for sf, info in decoded.items():
        assert info["student_key"] == info["teacher_key"], (sf, info)


def test_rep_vs_rep_intersection_and_denominators(fixture):
    ref_rows = fg.load_reference_rows(fixture["ref_csv"])
    est, config = fg.build_decoder(emissions=None, cohort_prior=None)
    strat_w = fg.stratum_weights(ref_rows)
    ref_by_sf = {r["sf"]: r for r in ref_rows}
    stats = fg.new_stats()

    maps = []
    for rep_dir in fixture["reps"]:
        delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
        maps.append(
            fg.rep_keys(
                rep_dir,
                ref_rows,
                delivery,
                est=est,
                config=config,
                vexcel_ceiling={},
                teacher_only=True,
                scorer=None,
                cache=None,
                scorer_key="teacher",
                skip_missing=False,
                stats=stats,
            )
        )

    ceiling = fg.pairwise_ceiling(maps, ref_by_sf, strat_w)
    assert len(ceiling["pairs"]) == 1
    pair = ceiling["pairs"][0]
    # rep1 decoded A,B,C; rep2 decoded A,B -> decoded intersection = {A,B} = 2 sf
    assert pair["n_intersection"] == 2
    assert pair["reps"] == [1, 2]
    # C (sf 3) decoded in rep1, not rep2
    assert pair["missing_in_rep_b"] == [3] or 3 in pair["missing_in_rep_b"]
    # identical scan states -> teacher keys agree on the intersection
    assert pair["agreement_inv_weighted"] == 1.0
    assert pair["agreement_unweighted"] == 1.0


def test_self_repro_deterministic_passes(fixture):
    ref_rows = fg.load_reference_rows(fixture["ref_csv"])
    est, config = fg.build_decoder(emissions=None, cohort_prior=None)
    rep_dir = fixture["reps"][0]
    delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
    ok, mism, n_decoded = fg.self_repro_rep(
        rep_dir,
        ref_rows,
        delivery,
        est=est,
        config=config,
        vexcel_ceiling={},
        scorer=_MirrorScorer(),
        skip_missing=False,
    )
    assert ok is True
    assert mism == []
    assert n_decoded > 0


def test_self_repro_nondeterministic_fails(fixture):
    ref_rows = fg.load_reference_rows(fixture["ref_csv"])
    est, config = fg.build_decoder(emissions=None, cohort_prior=None)
    rep_dir = fixture["reps"][0]
    delivery = fg.load_delivery_by_sf(rep_dir / "delivery.csv")
    ok, mism, n_decoded = fg.self_repro_rep(
        rep_dir,
        ref_rows,
        delivery,
        est=est,
        config=config,
        vexcel_ceiling={},
        scorer=_FlakyScorer(),
        skip_missing=False,
    )
    assert ok is False
    assert len(mism) >= 1
    assert n_decoded > 0


def test_out_guard_refuses_storebacked(tmp_path):
    bad = Path("/home/gaosh/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/x")
    with pytest.raises(SystemExit):
        fg.guard_out_dir(bad)
    # a fresh dir is fine
    fg.guard_out_dir(tmp_path / "fresh")
