import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.temporal.gehi_common import (
    DEFAULT_TILE_CACHE_DIR,
    GEHI_NATIVE_CACHE_ENV,
    TILE_CACHE_DIR_ENV,
    GehiRateLimiter,
    GehiRunResult,
    ReviewTargetMarker,
    anchor_bbox_args,
    anchor_location_arg,
    assert_gehi_success,
    decode_gehi_output,
    dedupe_info_rows_by_date,
    dedupe_info_rows_by_version,  # backward-compat alias
    default_tile_cache_dir,
    ensure_single_target_review_png,
    ensure_target_review_png,
    is_blocked_result,
    make_throttled_runner,
    parse_availability_output,
    parse_info_output,
    run_gehi,
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

    def test_dedupe_info_rows_by_date_preserves_each_capture_date(self):
        """Distinct capture_dates that share a version must each survive — the previous
        version-based dedupe collapsed 74 GEHI vintages to 15 in JHB CBD and broke
        progressive walk_back / bisection."""
        rows = [
            {"anchor_id": "a1", "capture_date": "2015-08-30", "version": 277, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2015-11-30", "version": 277, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2017-09-30", "version": 296, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2018-04-30", "version": 296, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2024-02-29", "version": 1010, "zoom": 19},
        ]
        deduped = dedupe_info_rows_by_date(rows)
        self.assertEqual(len(deduped), 5)
        dates = [r["capture_date"] for r in deduped]
        self.assertEqual(dates, ["2015-08-30", "2015-11-30", "2017-09-30", "2018-04-30", "2024-02-29"])
        first = deduped[0]
        self.assertEqual(first["version"], 277)
        self.assertEqual(first["n_date_labels"], 1)
        self.assertEqual(first["version_dedupe_key"], "a1:2015-08-30")

    def test_dedupe_info_rows_collapses_exact_duplicates(self):
        """Same anchor + same capture_date → keep one row; capture lowest version for stability."""
        rows = [
            {"anchor_id": "a1", "capture_date": "2015-08-30", "version": 277, "zoom": 19},
            {"anchor_id": "a1", "capture_date": "2015-08-30", "version": 296, "zoom": 19},
        ]
        deduped = dedupe_info_rows_by_date(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["version"], 277)
        self.assertEqual(deduped[0]["all_versions"], "277;296")
        self.assertEqual(deduped[0]["n_versions_at_date"], 2)

    def test_dedupe_alias_backward_compat(self):
        """dedupe_info_rows_by_version is preserved as an alias to avoid breaking imports."""
        self.assertIs(dedupe_info_rows_by_version, dedupe_info_rows_by_date)

    def test_parse_availability_output_from_utf16_chooser_output(self):
        raw = "[0]  2015/11/30  [1]  2015/08/30  [Esc]  Exit".encode("utf-16le")
        text = decode_gehi_output(raw)
        self.assertEqual(parse_availability_output(text), ["2015-08-30", "2015-11-30"])

    def test_parse_availability_output_letter_selector_keys(self):
        """Chooser keys continue [a], [b], ... past the tenth date; those dates must not be dropped."""
        text = (
            "[0]  2025/05/30  [1]  2025/02/28  [2]  2024/02/29  [3]  2023/01/30  "
            "[4]  2022/03/30  [5]  2021/08/30  [6]  2021/07/30  [7]  2021/06/30  "
            "[8]  2021/04/30  [9]  2020/05/31  [a]  2020/03/31  [b]  2019/06/30  "
            "[Esc]  Exit"
        )
        dates = parse_availability_output(text)
        self.assertEqual(len(dates), 12)
        self.assertIn("2020-03-31", dates)
        self.assertIn("2019-06-30", dates)

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

    def test_ensure_target_review_png_draws_t_markers(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tif = Path(td) / "chip.tif"
            Image.new("RGB", (100, 100), (30, 30, 30)).save(tif, format="TIFF")
            markers = [
                ReviewTargetMarker("target_1", "T01", 0.0, 0.0, 8.0),
                ReviewTargetMarker("target_2", "T02", 20.0, 10.0, 8.0),
            ]

            png = ensure_target_review_png(tif, markers, chip_size_m=100.0)

            self.assertEqual(png.suffix, ".png")
            self.assertIn(".targets-", png.name)
            self.assertTrue(png.exists())
            with Image.open(png) as img:
                self.assertNotEqual(img.getpixel((50, 50)), (30, 30, 30))
                self.assertNotEqual(img.getpixel((70, 40)), (30, 30, 30))

    def test_ensure_single_target_review_png_crops_and_marks_target(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tif = Path(td) / "chip.tif"
            Image.new("RGB", (200, 200), (30, 30, 30)).save(tif, format="TIFF")
            marker = ReviewTargetMarker("target_1", "T01", 20.0, 10.0, 8.0)

            png = ensure_single_target_review_png(
                tif,
                marker,
                chip_size_m=100.0,
                min_output_px=128,
            )

            self.assertEqual(png.suffix, ".png")
            self.assertIn(".target-T01-", png.name)
            self.assertTrue(png.exists())
            with Image.open(png) as img:
                self.assertGreaterEqual(min(img.size), 128)
                center = (img.size[0] // 2, img.size[1] // 2)
                self.assertNotEqual(img.getpixel(center), (30, 30, 30))

    def test_ensure_single_target_review_png_draws_footprint_bbox(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tif = Path(td) / "chip.tif"
            Image.new("RGB", (200, 200), (30, 30, 30)).save(tif, format="TIFF")
            marker = ReviewTargetMarker("target_1", "T01", 0.0, 0.0, 8.0)

            png = ensure_single_target_review_png(
                tif,
                marker,
                chip_size_m=100.0,
                min_crop_size_m=100.0,
                min_output_px=128,
                bbox_width_m=20.0,
                bbox_height_m=10.0,
            )

            with Image.open(png) as img:
                # 20m x 10m on a 100m, 200px chip => bbox corners near
                # centre +/-20px horizontally and +/-10px vertically.
                self.assertNotEqual(img.getpixel((80, 90)), (30, 30, 30))
                self.assertNotEqual(img.getpixel((120, 110)), (30, 30, 30))

    @staticmethod
    def _run_result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> GehiRunResult:
        return GehiRunResult(args=("availability",), returncode=returncode, stdout=stdout, stderr=stderr)

    def test_assert_gehi_success_chooser_exit_with_dates(self):
        """Non-zero chooser exit with parsed dates is suppressed when allowed."""
        result = self._run_result(
            returncode=1,
            stdout="[0]  2020/01/01  [Esc]  Exit\nCannot read keys",
        )
        # Must not raise.
        assert_gehi_success(result, allow_availability_chooser_exit=True)

    def test_assert_gehi_success_chooser_exit_without_dates(self):
        """Non-zero chooser exit with NO parsed dates (bbox with no coverage) must
        still be suppressed when allowed — the AND-logic bug crashed here."""
        # Message on stdout.
        result_stdout = self._run_result(returncode=1, stdout="Cannot read keys")
        assert_gehi_success(result_stdout, allow_availability_chooser_exit=True)
        # Message on stderr.
        result_stderr = self._run_result(returncode=1, stderr="Cannot read keys")
        assert_gehi_success(result_stderr, allow_availability_chooser_exit=True)

    def test_assert_gehi_success_genuine_failure_raises(self):
        """Non-zero exit without the chooser message must raise even when allowed."""
        with self.assertRaisesRegex(RuntimeError, "GEHistoricalImagery failed"):
            assert_gehi_success(
                self._run_result(returncode=1, stderr="boom"),
                allow_availability_chooser_exit=True,
            )

    def test_assert_gehi_success_chooser_message_but_flag_false_raises(self):
        """'Cannot read keys' present but suppression disabled must raise."""
        with self.assertRaisesRegex(RuntimeError, "GEHistoricalImagery failed"):
            assert_gehi_success(
                self._run_result(returncode=1, stdout="Cannot read keys"),
                allow_availability_chooser_exit=False,
            )


class _ZeroRng:
    def uniform(self, a: float, b: float) -> float:
        return 0.0


class TileCacheDirTests(unittest.TestCase):
    def test_repo_env_override_wins(self):
        with mock.patch.dict(
            os.environ,
            {TILE_CACHE_DIR_ENV: "/tmp/repo_cache", GEHI_NATIVE_CACHE_ENV: "/tmp/native_cache"},
        ):
            self.assertEqual(default_tile_cache_dir(), Path("/tmp/repo_cache"))

    def test_native_env_respected_when_repo_env_unset(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(TILE_CACHE_DIR_ENV, None)
            os.environ[GEHI_NATIVE_CACHE_ENV] = "/tmp/native_cache"
            self.assertEqual(default_tile_cache_dir(), Path("/tmp/native_cache"))

    def test_default_when_no_env(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(TILE_CACHE_DIR_ENV, None)
            os.environ.pop(GEHI_NATIVE_CACHE_ENV, None)
            self.assertEqual(default_tile_cache_dir(), DEFAULT_TILE_CACHE_DIR)

    def test_run_gehi_pins_tile_cache_env_and_creates_dir(self):
        """run_gehi must pass the resolved cache dir to the child env explicitly —
        relying on the caller's shell having exported it is exactly the gap that
        scattered tile caches across private directories."""
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td) / "tiles"
            with mock.patch.dict(os.environ, {TILE_CACHE_DIR_ENV: str(cache_dir)}):
                with mock.patch("scripts.temporal.gehi_common.subprocess.run", side_effect=fake_run):
                    run_gehi(["info", "--location", "0,0"])
            env = captured["env"]
            self.assertIsNotNone(env)
            self.assertEqual(env[GEHI_NATIVE_CACHE_ENV], str(cache_dir))
            self.assertTrue(cache_dir.is_dir())


class GehiRateLimiterTests(unittest.TestCase):
    def _limiter(self, **kwargs) -> GehiRateLimiter:
        self.sleeps: list[float] = []
        clock = {"t": 0.0}

        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            clock["t"] += seconds

        return GehiRateLimiter(
            sleep_fn=sleep, monotonic_fn=lambda: clock["t"], rng=_ZeroRng(), **kwargs
        )

    def test_first_call_free_then_paced(self):
        limiter = self._limiter(min_interval_s=2.0)
        self.assertEqual(limiter.wait(), 0.0)
        self.assertEqual(limiter.wait(), 2.0)
        self.assertEqual(self.sleeps, [2.0])

    def test_block_escalates_to_hard_backoff_then_resets(self):
        limiter = self._limiter(soft_backoff_s=60.0, hard_backoff_s=1800.0, hard_after=3)
        self.assertEqual(limiter.record_block(), 60.0)
        self.assertEqual(limiter.record_block(), 60.0)
        self.assertEqual(limiter.record_block(), 1800.0)
        # Counter reset after the hard backoff: next block is soft again.
        self.assertEqual(limiter.record_block(), 60.0)

    def test_success_resets_block_counter(self):
        limiter = self._limiter(soft_backoff_s=60.0, hard_backoff_s=1800.0, hard_after=2)
        limiter.record_block()
        limiter.record_success()
        # Without the reset this second block would already be the hard backoff.
        self.assertEqual(limiter.record_block(), 60.0)


class ThrottledRunnerTests(unittest.TestCase):
    @staticmethod
    def _blocked(stderr: str = "Response status code does not indicate success: 403 (Forbidden)") -> GehiRunResult:
        return GehiRunResult(args=("download",), returncode=1, stdout="", stderr=stderr)

    @staticmethod
    def _ok() -> GehiRunResult:
        return GehiRunResult(args=("download",), returncode=0, stdout="done", stderr="")

    def _limiter(self) -> GehiRateLimiter:
        self.sleeps: list[float] = []
        clock = {"t": 0.0}

        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            clock["t"] += seconds

        return GehiRateLimiter(
            min_interval_s=0.0,
            soft_backoff_s=60.0,
            hard_backoff_s=1800.0,
            sleep_fn=sleep,
            monotonic_fn=lambda: clock["t"],
            rng=_ZeroRng(),
        )

    def test_is_blocked_result(self):
        self.assertTrue(is_blocked_result(self._blocked()))
        self.assertTrue(is_blocked_result(self._blocked("HTTP 429 Too Many Requests")))
        self.assertFalse(is_blocked_result(self._ok()))
        # Generic failures are NOT block signals — the ladder handles those.
        self.assertFalse(is_blocked_result(GehiRunResult(args=("x",), returncode=2, stdout="", stderr="boom")))

    def test_retries_blocked_then_returns_success(self):
        outcomes = [self._blocked(), self._blocked(), self._ok()]
        calls: list[object] = []

        def base(args, *, executable, timeout):
            calls.append(args)
            return outcomes[len(calls) - 1]

        runner = make_throttled_runner(limiter=self._limiter(), max_attempts=3, base_runner=base)
        result = runner(["download"], executable=Path("gehi"), timeout=1.0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual([s for s in self.sleeps if s >= 60.0], [60.0, 60.0])

    def test_exhaustion_returns_last_blocked_result(self):
        calls: list[object] = []

        def base(args, *, executable, timeout):
            calls.append(args)
            return self._blocked()

        runner = make_throttled_runner(limiter=self._limiter(), max_attempts=2, base_runner=base)
        result = runner(["download"], executable=Path("gehi"), timeout=1.0)
        self.assertEqual(len(calls), 2)
        self.assertTrue(is_blocked_result(result))


if __name__ == "__main__":
    unittest.main()
