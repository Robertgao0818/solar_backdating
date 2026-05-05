import unittest

from scripts.temporal.gehi_common import (
    anchor_bbox_args,
    anchor_location_arg,
    decode_gehi_output,
    dedupe_info_rows_by_version,
    parse_availability_output,
    parse_info_output,
)
from scripts.temporal.gehi_download import expand_candidate_dates


class GehiCommonTests(unittest.TestCase):
    def test_anchor_location_uses_gehi_lat_lon_order(self):
        anchor = {"centroid_lon": "28.0176431943", "centroid_lat": "-26.1802762222"}
        self.assertEqual(anchor_location_arg(anchor), "-26.1802762222,28.0176431943")

    def test_anchor_bbox_uses_lower_left_upper_right_lat_lon_order(self):
        anchor = {
            "chip_lon_min": "28.0174129928",
            "chip_lon_max": "28.0178733966",
            "chip_lat_min": "-26.1804839289",
            "chip_lat_max": "-26.1800685152",
        }
        lower_left, upper_right = anchor_bbox_args(anchor)
        self.assertEqual(lower_left, "-26.1804839289,28.0174129928")
        self.assertEqual(upper_right, "-26.1800685152,28.0178733966")

    def test_parse_info_output(self):
        text = """Dated Imagery at -26.180276°, 28.017643°
  Level = 19, Path = 01331331212201130011
    date = 2015/08/30, version = 277
    date = 2015/11/30, version = 277
    date = 2024/02/29, version = 1010
"""
        rows = parse_info_output(text)
        self.assertEqual(
            rows,
            [
                {"zoom": 19, "path": "01331331212201130011", "capture_date": "2015-08-30", "version": 277},
                {"zoom": 19, "path": "01331331212201130011", "capture_date": "2015-11-30", "version": 277},
                {"zoom": 19, "path": "01331331212201130011", "capture_date": "2024-02-29", "version": 1010},
            ],
        )

    def test_dedupe_info_rows_by_anchor_version(self):
        rows = [
            {"anchor_id": "a1", "capture_date": "2015-08-30", "version": 277, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2015-11-30", "version": 277, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2024-02-29", "version": 1010, "zoom": 19},
        ]
        deduped = dedupe_info_rows_by_version(rows)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["version"], 277)
        self.assertEqual(deduped[0]["capture_date"], "2015-08-30")
        self.assertEqual(deduped[0]["capture_date_max"], "2015-11-30")
        self.assertEqual(deduped[0]["all_capture_dates"], "2015-08-30;2015-11-30")
        self.assertEqual(deduped[0]["version_dedupe_key"], "a1:277")

    def test_parse_availability_output_from_utf16_chooser_output(self):
        raw = "[0]  2015/11/30  [1]  2015/08/30  [Esc]  Exit".encode("utf-16le")
        text = decode_gehi_output(raw)
        self.assertEqual(parse_availability_output(text), ["2015-08-30", "2015-11-30"])

    def test_expand_candidate_dates_uses_all_labels_for_download(self):
        row = {
            "anchor_id": "a1",
            "capture_date": "2009-08-26",
            "version": "277",
            "all_capture_dates": "2015-08-30;2015-11-30",
        }
        expanded = expand_candidate_dates(row)
        self.assertEqual([item["capture_date"] for item in expanded], ["2015-08-30", "2015-11-30"])
        self.assertEqual({item["version"] for item in expanded}, {"277"})


if __name__ == "__main__":
    unittest.main()
