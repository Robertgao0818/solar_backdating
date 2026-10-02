"""Rules for extending a published install bracket onto catalog dates."""

import unittest

from scripts.temporal.build_jhb_run3_install_panel import (
    bracket_conflict,
    classify_panel_date,
    decoder_installed,
)


class InstallPanelTest(unittest.TestCase):
    def test_outside_a_bracket_is_determined_and_the_interior_stays_blank(self):
        self.assertEqual(
            classify_panel_date(
                "2020-01-01",
                observed=None,
                has_bracket=True,
                left_censored=False,
                interval_start="2022-01-01",
                interval_end="2023-01-01",
            ),
            ("0", "implied_absent"),
        )
        self.assertEqual(
            classify_panel_date(
                "2022-06-01",
                observed=None,
                has_bracket=True,
                left_censored=False,
                interval_start="2022-01-01",
                interval_end="2023-01-01",
            ),
            ("", "unknown_in_interval"),
        )
        self.assertEqual(
            classify_panel_date(
                "2024-01-01",
                observed=None,
                has_bracket=True,
                left_censored=False,
                interval_start="2022-01-01",
                interval_end="2023-01-01",
            ),
            ("1", "implied_present"),
        )

    def test_observed_decoder_label_wins_and_dip_repair_counts_as_present(self):
        self.assertEqual(
            classify_panel_date(
                "2022-01-01",
                observed="0",
                has_bracket=True,
                left_censored=False,
                interval_start="2022-01-01",
                interval_end="2023-01-01",
            ),
            ("0", "observed"),
        )
        self.assertEqual(
            decoder_installed(
                {"in_evidence": "1", "usable": "1", "dip_flipped": "1", "pv_present": "0"}
            ),
            "1",
        )
        self.assertIsNone(
            decoder_installed(
                {"in_evidence": "1", "usable": "0", "dip_flipped": "0", "pv_present": "1"}
            )
        )

    def test_left_censor_does_not_invent_an_earlier_absence(self):
        self.assertEqual(
            classify_panel_date(
                "2019-06-01",
                observed=None,
                has_bracket=True,
                left_censored=True,
                interval_start="",
                interval_end="2019-08-01",
            ),
            ("", "unknown_before_left_censor"),
        )

    def test_undated_anchor_is_not_extended(self):
        self.assertEqual(
            classify_panel_date(
                "2021-01-01",
                observed=None,
                has_bracket=False,
                left_censored=False,
                interval_start="",
                interval_end="",
            ),
            ("", "unknown_undated"),
        )

    def test_trailing_post_census_absent_stops_the_present_fill(self):
        self.assertIsNone(
            bracket_conflict(
                "2025-01-25",
                "0",
                interval_start="",
                interval_end="2019-01-15",
                post_census=True,
                left_censored=True,
            )
        )
        self.assertEqual(
            classify_panel_date(
                "2024-06-01",
                observed=None,
                has_bracket=True,
                left_censored=True,
                interval_start="",
                interval_end="2019-01-15",
                trailing_absent_date="2025-01-25",
            ),
            ("1", "implied_present"),
        )
        self.assertEqual(
            classify_panel_date(
                "2025-06-01",
                observed=None,
                has_bracket=True,
                left_censored=True,
                interval_start="",
                interval_end="2019-01-15",
                trailing_absent_date="2025-01-25",
            ),
            ("", "unknown_after_trailing_absent"),
        )

    def test_open_interval_rejects_a_decoder_observation(self):
        self.assertEqual(
            bracket_conflict(
                "2022-06-01",
                "0",
                interval_start="2022-01-01",
                interval_end="2023-01-01",
            ),
            "decoder_observation_inside_open_interval",
        )


if __name__ == "__main__":
    unittest.main()
