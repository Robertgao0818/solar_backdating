"""Tests for monotonic dip repair + its integration into the scan->interval path."""
import unittest
from datetime import date

from scripts.temporal.geid_temporal_common import (
    PresenceObservation,
    infer_install_interval,
    repair_isolated_dips,
)
from scripts.temporal.infer_install_dates import apply_dip_repair
from scripts.temporal.scan_state import Round, RoundResult, ScanState


def _round_result(capture_date, present, confidence=0.9):
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=1,
        pv_present=present,
        confidence=confidence,
        quality_flag="usable",
        decision_source="test",
    )


def _state(results, status="done_ambiguous_nonmonotonic"):
    rnd = Round(round_id=1, round_type="initial",
                window_start_date=None, window_end_date=None, results=results)
    return ScanState(anchor_id="a1", region_key="johannesburg", grid_id="G1",
                     status=status, rounds=[rnd])


def _obs(year, present, score=None, anchor="a1", row=None):
    return PresenceObservation(
        anchor_id=anchor,
        capture_date=date(year, 6, 15),
        pv_present=present,
        pv_score=score,
        source_row=row,
    )


class RepairIsolatedDipsTests(unittest.TestCase):
    def test_interior_dip_flipped(self):
        obs = [_obs(2018, True, 0.95), _obs(2019, False, 0.0), _obs(2021, True, 0.98)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(dates, [date(2019, 6, 15)])
        self.assertTrue(all(o.pv_present for o in repaired))

    def test_confident_absent_flanked_by_confident_present_flips(self):
        # The a000008 case: absent reads very confidently (score 0.0) but is
        # bracketed by confident presents -> still repaired. Gate is on flanks.
        obs = [_obs(2018, True, 1.0), _obs(2019, False, 0.0), _obs(2021, True, 1.0)]
        repaired, dates = repair_isolated_dips(obs, flank_min_confidence=0.5)
        self.assertEqual(len(dates), 1)
        self.assertTrue(repaired[1].pv_present)

    def test_leading_absents_preserved(self):
        # Real install edge: absent then present, no present before the absents.
        obs = [_obs(2017, False, 0.9), _obs(2018, False, 0.9), _obs(2020, True, 0.95)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(dates, [])
        self.assertFalse(repaired[0].pv_present)
        self.assertFalse(repaired[1].pv_present)

    def test_trailing_absent_preserved(self):
        # Possible real removal: present then absent at the end -> not interior.
        obs = [_obs(2018, False), _obs(2019, True), _obs(2020, False)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(dates, [])
        self.assertFalse(repaired[2].pv_present)

    def test_multi_frame_interior_dip_all_flipped(self):
        obs = [_obs(2018, True, 0.9), _obs(2019, False), _obs(2020, False), _obs(2021, True, 0.9)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(len(dates), 2)
        self.assertTrue(all(o.pv_present for o in repaired))

    def test_low_confidence_flank_not_flipped(self):
        obs = [_obs(2018, True, 0.3), _obs(2019, False, 0.0), _obs(2021, True, 0.3)]
        repaired, dates = repair_isolated_dips(obs, flank_min_confidence=0.5)
        self.assertEqual(dates, [])
        self.assertFalse(repaired[1].pv_present)

    def test_all_present_unchanged(self):
        obs = [_obs(2018, True, 0.9), _obs(2019, True, 0.9)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(dates, [])
        self.assertEqual([o.pv_present for o in repaired], [True, True])

    def test_unscored_chip_skipped_when_locating_flanks(self):
        # None observation between present and absent should not act as a flank,
        # but should pass through untouched.
        obs = [_obs(2018, True, 0.9), _obs(2019, None), _obs(2020, False, 0.0), _obs(2021, True, 0.9)]
        repaired, dates = repair_isolated_dips(obs)
        self.assertEqual(dates, [date(2020, 6, 15)])
        self.assertIsNone(repaired[1].pv_present)  # untouched
        self.assertTrue(repaired[2].pv_present)  # repaired

    def test_repair_then_infer_is_monotonic(self):
        obs = [_obs(2018, True, 1.0), _obs(2019, False, 0.0), _obs(2021, True, 1.0)]
        repaired, _ = repair_isolated_dips(obs)
        interval = infer_install_interval("a1", repaired)
        self.assertEqual(interval.status, "already_present")
        self.assertEqual(interval.confidence, "high")

    def test_baseline_without_repair_is_nonmonotonic(self):
        obs = [_obs(2018, True, 1.0), _obs(2019, False, 0.0), _obs(2021, True, 1.0)]
        interval = infer_install_interval("a1", obs)
        self.assertEqual(interval.status, "ambiguous_nonmonotonic")


class ApplyDipRepairTests(unittest.TestCase):
    """apply_dip_repair integrates monotonic repair into the scan_state->interval
    path without mutating the scan itself."""

    def test_interior_dip_resolves_ambiguous_to_already_present(self):
        # The a000008 pattern: confident present, confident absent (washed), present.
        state = _state([
            _round_result("2018-03-30", True, 1.0),
            _round_result("2018-12-30", False, 0.0),
            _round_result("2021-04-30", True, 1.0),
        ])
        repaired_state, repaired = apply_dip_repair(state)
        self.assertEqual(repaired, ["2018-12-30"])
        self.assertEqual(repaired_state.status, "done_already_present_before_geid_history")

    def test_original_state_not_mutated(self):
        results = [
            _round_result("2018-03-30", True, 1.0),
            _round_result("2018-12-30", False, 0.0),
            _round_result("2021-04-30", True, 1.0),
        ]
        state = _state(results)
        apply_dip_repair(state)
        # the input state's middle observation is still absent (deep-copy isolation)
        self.assertIs(state.rounds[0].results[1].pv_present, False)
        self.assertEqual(state.status, "done_ambiguous_nonmonotonic")

    def test_real_install_edge_preserved(self):
        # absent -> present -> present: a genuine install, no interior dip to repair.
        state = _state([
            _round_result("2016-09-30", False, 0.9),
            _round_result("2020-05-31", True, 0.95),
            _round_result("2024-02-29", True, 1.0),
        ], status="done_appears")
        repaired_state, repaired = apply_dip_repair(state)
        self.assertEqual(repaired, [])
        self.assertEqual(repaired_state.status, "done_appears")

    def test_dip_after_transition_becomes_appears(self):
        # absent -> present -> absent(interior) -> present: repair the interior dip,
        # keep the real leading install edge -> done_appears with a transition.
        state = _state([
            _round_result("2015-01-30", False, 0.9),
            _round_result("2018-03-30", True, 1.0),
            _round_result("2018-12-30", False, 0.0),
            _round_result("2021-04-30", True, 1.0),
        ])
        repaired_state, repaired = apply_dip_repair(state)
        self.assertEqual(repaired, ["2018-12-30"])
        self.assertEqual(repaired_state.status, "done_appears")


if __name__ == "__main__":
    unittest.main()
