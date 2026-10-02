"""Token rules for the JHB RUN 3 presence-sequence export."""

import unittest
from datetime import date

from scripts.temporal.export_jhb_run3_presence_sequences import sequence_from_state
from scripts.temporal.scan_state import Round, RoundResult, ScanState


def _result(capture_date, present, quality="usable", confidence=0.9, chip_index=1, provider="TM"):
    return RoundResult(
        chip_index=chip_index,
        capture_date=capture_date,
        version=1,
        pv_present=present,
        confidence=confidence,
        quality_flag=quality,
        decision_source="test",
        provider=provider,
    )


def _state(rounds, census_date="2024-04-19"):
    return ScanState(
        anchor_id="a1",
        region_key="johannesburg",
        grid_id="G1",
        status="done_appears",
        rounds=rounds,
        census_date=census_date,
    )


class SequenceExportTest(unittest.TestCase):
    def test_raw_tokens_keep_unscored_and_mark_decoder_masks(self):
        state = _state(
            [
                Round(
                    round_id=2,
                    round_type="bisection",
                    window_start_date=None,
                    window_end_date=None,
                    results=[_result("2021-01-01", False)],
                ),
                Round(
                    round_id=1,
                    round_type="initial",
                    window_start_date=None,
                    window_end_date=None,
                    results=[
                        _result("2019-01-01", None, quality="unusable", confidence=None),
                        _result("2019-06-01", True, quality="ambiguous"),
                        _result("2020-01-01", True, provider="Wayback"),
                        _result("2022-01-01", True),
                        _result("2025-03-30", True),
                    ],
                ),
            ]
        )
        exported = sequence_from_state(state, census_mid_date=date(2024, 6, 30))
        summary = exported.summary
        self.assertEqual(
            summary["seq_dates"],
            "2019-01-01|2019-06-01|2020-01-01|2021-01-01|2022-01-01|2025-03-30",
        )
        self.assertEqual(summary["seq_present"], ".|1|1|0|1|1")
        self.assertEqual(
            summary["seq_quality"],
            "unusable|ambiguous|usable|usable|usable|usable",
        )
        self.assertEqual(summary["seq_provider"].split("|")[2], "Wayback")
        # July 18 counted the post-census frame. The later fence is a separate mask.
        self.assertEqual(summary["seq_evidence"], "1|1|1|1|1|1")
        self.assertEqual(summary["seq_post_census"], "0|0|0|0|0|1")
        self.assertEqual(summary["seq_usable"], "0|0|1|1|1|1")
        self.assertEqual(summary["seq_dip_flipped"], "0|0|0|1|0|0")
        self.assertEqual(summary["seq_n"], 6)
        self.assertEqual(summary["seq_n_decoder"], 6)
        self.assertEqual(summary["seq_n_unusable_decoder"], 2)
        self.assertEqual(summary["seq_n_present_decoder"], 4)
        self.assertEqual(summary["seq_n_absent_decoder"], 0)
        self.assertEqual(summary["seq_n_dip_flipped"], 1)
        self.assertEqual([row["obs_order"] for row in exported.observations], list(range(6)))
        self.assertEqual(exported.observations[3]["present_token"], "0")
        self.assertEqual(exported.observations[3]["dip_flipped"], 1)


if __name__ == "__main__":
    unittest.main()
