"""Unit tests for the D3/D8 metric mechanics (parity-critical, do not relax)."""
from __future__ import annotations

from collections import Counter
from datetime import date

from solar_backdating.estimators import get_estimator
from solar_backdating.eval.metrics import (
    hpd_contains_rate,
    install_tokens,
    mode_hit,
    per_unit_metrics,
    tier,
    tvd,
    weighted_headline,
)
from solar_backdating.eval.panel_io import INVENTORY_WEIGHT_FALLBACK


def test_mode_hit_denominator_counts_missing():
    # 3 of 5 reps agree on "A"; denom is all 5 (blanks count against).
    modal, rate = mode_hit(["A", "A", "A", "B", ""])
    assert modal == "A"
    assert rate == 3 / 5


def test_mode_hit_all_blank():
    assert mode_hit(["", "", ""]) == ("", 0.0)


def test_mode_hit_undated_sentinel_eligible():
    modal, rate = mode_hit(["UNDATED", "UNDATED", "MAP|2020-01-01"])
    assert modal == "UNDATED"
    assert rate == 2 / 3


def test_mode_hit_tiebreak_insertion_order():
    # Counter.most_common breaks ties by first-seen order.
    modal, _ = mode_hit(["B", "A", "B", "A"])
    assert modal == "B"


def test_tier_boundaries():
    assert tier(0.8) == "rock_solid"
    assert tier(0.79) == "wobbly"
    assert tier(0.4) == "wobbly"
    assert tier(0.39) == "chaotic"


def test_tvd_symmetry_and_range():
    a = Counter({"2020": 2, "2021": 1})
    b = Counter({"2020": 1, "2021": 2})
    assert abs(tvd(a, b) - tvd(b, a)) < 1e-12
    assert 0.0 <= tvd(a, b) <= 1.0
    assert tvd(a, a) == 0.0
    # disjoint supports -> TVD 1.0
    assert tvd(Counter({"x": 1}), Counter({"y": 1})) == 1.0


def test_install_tokens_encoding():
    class P:
        map_date = "2021-06-30"

    map_tok, year_tok, cand, undated = install_tokens(P())
    assert map_tok == "MAP|2021-06-30"
    assert year_tok == "2021"
    assert cand == "MAP|2021-06-30"
    assert undated == 0

    class U:
        map_date = ""

    map_tok, year_tok, cand, undated = install_tokens(U())
    assert map_tok == "UNDATED"
    assert year_tok == ""  # undated -> excluded candidate but counted in denom
    assert cand == "UNDATED"
    assert undated == 1


def test_per_unit_metrics_dated_only_denominator():
    # 4 reps: 3 dated agree, 1 undated. all-denom hit = 3/4; dated-only = 3/3.
    toks = [
        ("MAP|2020-01-01", "2020", "MAP|2020-01-01", 0),
        ("MAP|2020-01-01", "2020", "MAP|2020-01-01", 0),
        ("MAP|2020-01-01", "2020", "MAP|2020-01-01", 0),
        ("UNDATED", "", "UNDATED", 1),
    ]
    m = per_unit_metrics(toks)
    assert m["map_mode_hit_all"] == 0.75
    assert m["map_mode_hit_dated"] == 1.0
    assert m["year_mode_hit"] == 0.75
    assert m["undated_flip"] == 0.25
    assert m["tier"] == "wobbly"


def test_per_unit_metrics_all_undated_dated_zero():
    toks = [("UNDATED", "", "UNDATED", 1), ("UNDATED", "", "UNDATED", 1)]
    m = per_unit_metrics(toks)
    assert m["map_mode_hit_all"] == 1.0  # UNDATED is a candidate
    assert m["map_mode_hit_dated"] == 0.0  # no dated reps
    assert m["undated_flip"] == 1.0


def test_weighted_headline_dated_excludes_fully_undated():
    # D8(3): the dated-only headline denominator excludes exactly the units with
    # zero dated reps. Unit b is fully undated (n_dated_reps=0, sentinel dated
    # 0.0); it must NOT dilute the dated mean. Correct dated mean = 1.0 (unit a
    # only), not (1.0 + 0.0)/2 = 0.5.
    per_unit = [
        {"unit": "a", "chip_id": "a", "stratum": "s", "n_reps": 3,
         "map_mode_hit_all": 1.0, "map_mode_hit_dated": 1.0, "year_mode_hit": 1.0,
         "undated_flip": 0.0, "tier": "rock_solid", "modal_map": "x",
         "n_dated_reps": 3, "hpd_contains_rate": None},
        {"unit": "b", "chip_id": "b", "stratum": "s", "n_reps": 3,
         "map_mode_hit_all": 1.0, "map_mode_hit_dated": 0.0, "year_mode_hit": 0.0,
         "undated_flip": 1.0, "tier": "rock_solid", "modal_map": "UNDATED",
         "n_dated_reps": 0, "hpd_contains_rate": None},
    ]
    hl = weighted_headline(per_unit, {"a": "s", "b": "s"}, {"s": 1.0})
    assert hl["overall_unweighted"]["map_mode_hit_dated"] == 1.0
    assert hl["by_stratum"]["s"]["map_mode_hit_dated"] == 1.0
    # non-dated metrics still average over ALL units
    assert hl["overall_unweighted"]["undated_flip"] == 0.5
    assert hl["overall_unweighted"]["map_mode_hit_all"] == 1.0


def test_hpd_contains_point_posteriors():
    fpd = get_estimator("fpd")
    from solar_backdating.estimators import VintageObservation

    def unit(pv_dates):
        return [
            VintageObservation(capture_date=d, pv_present=pv, source_row=i)
            for i, (d, pv) in enumerate(pv_dates)
        ]

    # two reps agreeing on the same first-present date -> point interval contains.
    obs = [(date(2020, 1, 1), "0"), (date(2021, 1, 1), "1")]
    posteriors = [fpd(unit(obs)), fpd(unit(obs))]
    assert hpd_contains_rate(posteriors) == 1.0


def test_weighted_headline_hand_check_sustained():
    """Applying INVENTORY_WEIGHT_FALLBACK to the sustained by_stratum means
    reproduces the ~0.7975 / ~0.4692 hand-check (design §4)."""
    # Published summary.json by_stratum sustained means (map=sustained_mode_hit,
    # year=sustained_year_mode_hit), one synthetic unit per stratum.
    st_means = {
        "done_already_present_before_geid_history": (1.0, 1.0),
        "done_ambiguous_gemini_failed": (0.938, 0.838),
        "done_ambiguous_no_recent_anchor": (0.733, 0.833),
        "done_ambiguous_nonmonotonic": (0.84, 0.84),
        "done_appears": (0.8, 0.3),
        "done_installed_during_census": (1.0, 1.0),
    }
    per_unit = []
    strata = {}
    for i, (st, (mh, yh)) in enumerate(st_means.items()):
        chip = f"c{i}"
        strata[chip] = st
        per_unit.append({
            "unit": f"({chip},)", "chip_id": chip, "stratum": st, "n_reps": 10,
            "map_mode_hit_all": mh, "map_mode_hit_dated": mh,
            "year_mode_hit": yh, "undated_flip": 0.0,
            "tier": "rock_solid" if mh >= 0.8 else "wobbly",
            "modal_map": "x", "hpd_contains_rate": None,
        })
    headline = weighted_headline(per_unit, strata, INVENTORY_WEIGHT_FALLBACK)
    inv = headline["overall_inventory_weighted"]
    assert abs(inv["map_mode_hit_all"] - 0.7975) < 0.003
    assert abs(inv["year_mode_hit"] - 0.4692) < 0.003
