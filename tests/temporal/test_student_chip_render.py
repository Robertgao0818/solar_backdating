"""Slice-4 — marker-free student-chip render + unusable-class supervision rounds.

Regresses the additive ``draw_marker`` knob on the scoring-crop render
(``gehi_common``) and the ``render-student-chips`` subcommand of
``build_distillation_set`` (see
``docs/dinov3_scorer/ISSUE-04-train-head-calibrate.md`` + PRD §D4).

PRD D4 is binding: the STUDENT's input chips must be marker-free (the drawn
marker was for the LLM to read; the frozen encoder pools the centre patch tokens
instead). This slice adds a marker-free render variant that coexists with the
slice-2 marked PNGs, and a student subcommand that additionally re-renders
quality-driven ``unusable`` rounds (haze/cloud on healthy anchors) while still
excluding ``done_ambiguous_*`` marker-misregistration series (D12.iii).

House conventions: function-style, ``tmp_path`` for all IO, stub the GEHI
download/render seams (never touch the network / GPU / a real model). Tests that
exercise the real PIL renderer fabricate a tiny in-memory image.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.temporal import build_distillation_set as bds
from scripts.temporal import gehi_common
from scripts.temporal.gehi_common import (
    ReviewTargetMarker,
    ensure_single_target_review_png,
    target_crop_review_png_path,
)
from scripts.temporal.gehi_download import DownloadResult

# ---------------------------------------------------------------------------
# fabrication helpers
# ---------------------------------------------------------------------------


def _marker() -> ReviewTargetMarker:
    return ReviewTargetMarker("target_1", "A", 0.0, 0.0, 5.0)


def _real_tif(path: Path, size: int = 200) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), (30, 30, 30)).save(path, format="TIFF")
    return path


def _write_chip_targets(path: Path, rows: list[dict[str, object]]) -> None:
    cols = [
        "anchor_id", "chip_id", "region_key", "grid_id", "target_label",
        "target_index", "centroid_lon", "centroid_lat", "source_width_m",
        "source_height_m", "target_offset_x_m", "target_offset_y_m",
        "search_radius_m", "chip_size_m", "chip_lon_min", "chip_lat_min",
        "chip_lon_max", "chip_lat_max",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _chip_target_row(anchor_id: str, chip_id: str, grid_id: str = "JNB0373") -> dict[str, object]:
    return {
        "anchor_id": anchor_id, "chip_id": chip_id, "region_key": "johannesburg",
        "grid_id": grid_id, "target_label": "A", "target_index": 0,
        "centroid_lon": 28.05, "centroid_lat": -26.20,
        "source_width_m": 8.0, "source_height_m": 8.0,
        "target_offset_x_m": 0.0, "target_offset_y_m": 0.0,
        "search_radius_m": 5.0, "chip_size_m": 96.0,
        "chip_lon_min": 28.049, "chip_lat_min": -26.201,
        "chip_lon_max": 28.051, "chip_lat_max": -26.199,
    }


def _manifest_row(**over: object) -> dict[str, object]:
    base = {c: "" for c in bds.MANIFEST_COLUMNS}
    base.update(
        anchor_id="corpus_t00000001", capture_date="2020-06-15", version=100,
        actual_zoom=20, label_3class="present", terminal_status="done_appears",
        sub_domain="non_cbd_satellite", corpus="chip_group", split="train",
    )
    base.update(over)
    return base


def _ok_download(path: Path, zoom: int = 20, status: str = "ok") -> DownloadResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"TIF")
    return DownloadResult(
        anchor_id="a", capture_date="2020-06-15", version="100",
        requested_zoom_ladder=(zoom,), actual_zoom=zoom, path=path,
        sha256="deadbeef", status=status, error=None, gehi_command="",
        download_stdout_sha256="",
    )


# ---------------------------------------------------------------------------
# A. gehi_common draw_marker: distinct filename + marker-draw gating
# ---------------------------------------------------------------------------


def test_nomarker_path_inserts_nomarker_before_png(tmp_path: Path) -> None:
    tif = tmp_path / "chip.tif"
    marked = target_crop_review_png_path(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256,
    )
    nomarker = target_crop_review_png_path(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=False,
    )
    assert marked != nomarker
    assert nomarker.name.endswith(".nomarker.png")
    # same stem/token — only the ".nomarker" segment is inserted before ".png"
    assert nomarker.name == marked.name[: -len(".png")] + ".nomarker.png"


def test_draw_marker_true_path_byte_identical_to_default(tmp_path: Path) -> None:
    # explicit draw_marker=True MUST reproduce the pre-slice-4 (no-kwarg) path so
    # existing slice-2 PNGs are not invalidated.
    tif = tmp_path / "chip.tif"
    default_path = target_crop_review_png_path(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256,
    )
    explicit_true = target_crop_review_png_path(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=True,
    )
    assert explicit_true == default_path
    assert not explicit_true.name.endswith(".nomarker.png")


def test_draw_marker_false_does_not_invoke_marker_draw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tif = _real_tif(tmp_path / "chip.tif")
    calls: list[int] = []
    monkeypatch.setattr(
        gehi_common, "_draw_single_target_marker_at",
        lambda *a, **k: calls.append(1),
    )
    png = ensure_single_target_review_png(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=False,
    )
    assert png.exists()
    assert png.name.endswith(".nomarker.png")
    assert calls == []  # marker draw skipped entirely


def test_draw_marker_true_still_invokes_marker_draw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tif = _real_tif(tmp_path / "chip.tif")
    calls: list[int] = []
    monkeypatch.setattr(
        gehi_common, "_draw_single_target_marker_at",
        lambda *a, **k: calls.append(1),
    )
    png = ensure_single_target_review_png(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=True,
    )
    assert png.exists()
    assert not png.name.endswith(".nomarker.png")
    assert calls == [1]  # marker draw invoked for the marked (default) variant


def test_marker_free_and_marked_variants_coexist(tmp_path: Path) -> None:
    tif = _real_tif(tmp_path / "chip.tif")
    marked = ensure_single_target_review_png(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=True,
    )
    nomarker = ensure_single_target_review_png(
        tif, _marker(), chip_size_m=96.0, crop_context_multiplier=0.5,
        min_crop_size_m=12.0, min_output_px=256, draw_marker=False,
    )
    assert marked.exists() and nomarker.exists()
    assert marked != nomarker


# ---------------------------------------------------------------------------
# B. row-selection rule (each branch)
# ---------------------------------------------------------------------------


def test_select_student_rows_covers_all_branches() -> None:
    subset = ["corpus_t00000001", "corpus_t00000002", "corpus_t00000003", "corpus_t00000004"]
    manifest = [
        # present (subset, renderable) -> IN
        _manifest_row(anchor_id="corpus_t00000001", label_3class="present"),
        # absent (subset, renderable) -> IN
        _manifest_row(anchor_id="corpus_t00000002", label_3class="absent"),
        # quality-driven unusable on a healthy anchor -> IN
        _manifest_row(anchor_id="corpus_t00000003", label_3class="unusable",
                      terminal_status="done_appears"),
        # done_ambiguous_* unusable (marker-misregistration) -> OUT
        _manifest_row(anchor_id="corpus_t00000004", label_3class="unusable",
                      terminal_status="done_ambiguous_nonmonotonic"),
        # version="" (census / label-only) -> OUT even though present + in subset
        _manifest_row(anchor_id="corpus_t00000001", version="", capture_date="2023-01-01",
                      label_3class="present"),
        # non-subset anchor -> OUT
        _manifest_row(anchor_id="corpus_t09999999", label_3class="present"),
    ]
    selected = bds.select_student_rows(manifest, subset)
    got = {(str(r["anchor_id"]), str(r["capture_date"])) for r in selected}
    assert got == {
        ("corpus_t00000001", "2020-06-15"),
        ("corpus_t00000002", "2020-06-15"),
        ("corpus_t00000003", "2020-06-15"),
    }
    # explicit branch assertions
    labels = {str(r["anchor_id"]): str(r["label_3class"]) for r in selected}
    assert labels["corpus_t00000003"] == "unusable"  # quality-unusable kept
    assert "corpus_t00000004" not in labels  # done_ambiguous_* excluded
    assert all(str(r["version"]) != "" for r in selected)  # no label-only rows


# ---------------------------------------------------------------------------
# C. render_subset: draw_marker propagation + marker/label_arm provenance
# ---------------------------------------------------------------------------


def test_render_subset_threads_draw_marker_to_renderer(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [_manifest_row(anchor_id="corpus_t00000001", label_3class="present")]

    seen: dict[str, object] = {}

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        tif = Path(output_root) / f"{anchor['anchor_id']}.tif"
        return _ok_download(tif)

    def fake_renderer(image_path, target_marker, *, draw_marker=True, **kw):
        seen["draw_marker"] = draw_marker
        png = Path(str(image_path)).with_suffix(".nomarker.png" if not draw_marker else ".png")
        png.write_bytes(b"PNG")
        return png

    bds.render_subset(
        subset, chip_targets_path=targets, geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips", draw_marker=False,
        downloader=fake_downloader, renderer=fake_renderer,
    )
    assert seen["draw_marker"] is False


def test_render_subset_records_marker_and_label_arm(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(
        targets,
        [
            _chip_target_row("corpus_t00000001", "corpus_c0000001"),
            _chip_target_row("corpus_t00000002", "corpus_c0000002"),
        ],
    )
    subset = [
        _manifest_row(anchor_id="corpus_t00000001", label_3class="present"),
        _manifest_row(anchor_id="corpus_t00000002", label_3class="unusable",
                      terminal_status="done_appears", capture_date="2019-01-01"),
    ]

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        tif = Path(output_root) / f"{anchor['anchor_id']}_{capture_date}.tif"
        return _ok_download(tif)

    def fake_renderer(image_path, target_marker, *, draw_marker=True, **kw):
        png = Path(str(image_path)).with_suffix(".nomarker.png")
        png.write_bytes(b"PNG")
        return png

    stats = bds.render_subset(
        subset, chip_targets_path=targets, geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips", draw_marker=False,
        downloader=fake_downloader, renderer=fake_renderer,
    )
    by_anchor = {str(p["anchor_id"]): p for p in stats.provenance_rows}
    assert by_anchor["corpus_t00000001"]["marker"] == "none"
    assert by_anchor["corpus_t00000001"]["label_arm"] == "supervision"
    assert by_anchor["corpus_t00000002"]["label_arm"] == "unusable"
    assert all(p["marker"] == "none" for p in stats.provenance_rows)


def test_render_subset_default_draw_marker_true_unchanged(tmp_path: Path) -> None:
    # backward-compat: the default (slice-2) call path still draws the marker.
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [_manifest_row(anchor_id="corpus_t00000001", label_3class="present")]

    seen: dict[str, object] = {}

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        tif = Path(output_root) / f"{anchor['anchor_id']}.tif"
        return _ok_download(tif)

    def fake_renderer(image_path, target_marker, *, draw_marker=True, **kw):
        seen["draw_marker"] = draw_marker
        png = Path(str(image_path)).with_suffix(".png")
        png.write_bytes(b"PNG")
        return png

    bds.render_subset(
        subset, chip_targets_path=targets, geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips",
        downloader=fake_downloader, renderer=fake_renderer,
    )
    assert seen["draw_marker"] is True


# ---------------------------------------------------------------------------
# D. render-student-chips CLI: provenance schema + drops + slice-2 untouched
# ---------------------------------------------------------------------------


def _seed_distill_dir(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest_rows = [
        _manifest_row(anchor_id="corpus_t00000001", label_3class="present"),
        _manifest_row(anchor_id="corpus_t00000002", label_3class="unusable",
                      terminal_status="done_appears", capture_date="2019-01-01"),
        # excluded: done_ambiguous_* unusable + census label-only + non-subset
        _manifest_row(anchor_id="corpus_t00000003", label_3class="unusable",
                      terminal_status="done_ambiguous_nonmonotonic"),
        _manifest_row(anchor_id="corpus_t00000001", version="", capture_date="2023-01-01",
                      label_3class="present", corpus="census2023"),
    ]
    bds.write_manifest(manifest_rows, out / "label_manifest.csv")
    (out / "chip_subset_anchors.json").write_text(
        json.dumps(["corpus_t00000001", "corpus_t00000002", "corpus_t00000003"]),
        encoding="utf-8",
    )


def test_render_student_chips_cli_writes_provenance_and_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "distill_out"
    _seed_distill_dir(out)

    captured: dict[str, object] = {}

    def fake_render_subset(subset_rows, **kw):
        captured["subset_rows"] = list(subset_rows)
        captured["draw_marker"] = kw.get("draw_marker")
        captured["output_root"] = kw.get("output_root")
        return bds.RenderStats(
            rendered=2, dropped_unrecoverable=0, dropped_unresolved=0,
            drop_log=[],
            provenance_rows=[
                {"anchor_id": "corpus_t00000001", "chip_id": "corpus_c0000001",
                 "capture_date": "2020-06-15", "version": 100, "actual_zoom": 20,
                 "download_status": "skipped_existing", "chip_sha256": "abc",
                 "tif_path": "/x.tif", "png_path": "/x.nomarker.png",
                 "geometry_version": "chip_geom_v2_tight12",
                 "marker": "none", "label_arm": "supervision"},
                {"anchor_id": "corpus_t00000002", "chip_id": "corpus_c0000002",
                 "capture_date": "2019-01-01", "version": 100, "actual_zoom": 20,
                 "download_status": "ok", "chip_sha256": "def",
                 "tif_path": "/y.tif", "png_path": "/y.nomarker.png",
                 "geometry_version": "chip_geom_v2_tight12",
                 "marker": "none", "label_arm": "unusable"},
            ],
        )

    monkeypatch.setattr(bds, "render_subset", fake_render_subset)

    args = SimpleNamespace(
        output_root=out, chip_targets=out / "chip_targets.csv",
        chip_geometry="chip_geom_v2_tight12",
    )
    bds._cmd_render_student_chips(args)

    # student subcommand renders MARKER-FREE (PRD D4)
    assert captured["draw_marker"] is False
    # only the three subset rows that pass the student rule (t1 present, t2
    # unusable-quality); done_ambiguous_* + census label-only excluded
    sel = {str(r["anchor_id"]) for r in captured["subset_rows"]}  # type: ignore[union-attr]
    assert sel == {"corpus_t00000001", "corpus_t00000002"}
    # tifs are shared with the slice-2 marked render -> download into chips/
    assert Path(str(captured["output_root"])).name == "chips"

    # provenance CSV carries the marker + label_arm columns
    prov_path = out / "student_chip_render_provenance.csv"
    assert prov_path.exists()
    with prov_path.open() as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        prov_rows = list(reader)
    assert "marker" in header and "label_arm" in header
    assert {r["label_arm"] for r in prov_rows} == {"supervision", "unusable"}
    assert all(r["marker"] == "none" for r in prov_rows)

    # drops json written with summary counts
    drops = json.loads((out / "student_chip_render_drops.json").read_text())
    assert drops["rendered"] == 2
    assert drops["dropped_unrecoverable"] == 0
    assert drops["dropped_unresolved"] == 0
    assert isinstance(drops["drop_log"], list)


def test_render_student_chips_cli_does_not_touch_slice2_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "distill_out"
    _seed_distill_dir(out)
    # slice-2 frozen artifacts pre-exist with sentinel content
    (out / "chip_render_provenance.csv").write_text("SLICE2-PROV", encoding="utf-8")
    (out / "chip_render_drops.json").write_text("SLICE2-DROPS", encoding="utf-8")
    (out / "README.md").write_text("SLICE2-README", encoding="utf-8")

    monkeypatch.setattr(
        bds, "render_subset",
        lambda *a, **k: bds.RenderStats(rendered=0, dropped_unrecoverable=0,
                                        dropped_unresolved=0, drop_log=[], provenance_rows=[]),
    )
    args = SimpleNamespace(
        output_root=out, chip_targets=out / "chip_targets.csv",
        chip_geometry="chip_geom_v2_tight12",
    )
    bds._cmd_render_student_chips(args)

    # the slice-2 outputs are untouched by the new subcommand
    assert (out / "chip_render_provenance.csv").read_text() == "SLICE2-PROV"
    assert (out / "chip_render_drops.json").read_text() == "SLICE2-DROPS"
    assert (out / "README.md").read_text() == "SLICE2-README"
    # the student subcommand writes its OWN provenance/drops files
    assert (out / "student_chip_render_provenance.csv").exists()
    assert (out / "student_chip_render_drops.json").exists()


def test_render_student_chips_subcommand_parses(tmp_path: Path) -> None:
    args = bds.parse_args(
        ["render-student-chips", "--output-root", str(tmp_path),
         "--chip-geometry", "chip_geom_v2_tight12"]
    )
    assert args.command == "render-student-chips"
    assert args.func is bds._cmd_render_student_chips
    assert args.chip_geometry == "chip_geom_v2_tight12"


# ---------------------------------------------------------------------------
# E. idempotency: second render_subset run with an existing marker-free PNG
#    does not re-encode (real PIL renderer)
# ---------------------------------------------------------------------------


def test_render_subset_marker_free_idempotent_second_run(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [_manifest_row(anchor_id="corpus_t00000001", label_3class="present")]
    chips = tmp_path / "chips"

    tif = _real_tif(chips / "corpus_c0000001.tif")

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        # idempotent download: the tif already exists on disk -> skipped_existing
        return _ok_download(tif, status="skipped_existing")

    stats1 = bds.render_subset(
        subset, chip_targets_path=targets, geometry_version="chip_geom_v2_tight12",
        output_root=chips, draw_marker=False, downloader=fake_downloader,
    )
    assert stats1.rendered == 1
    png = Path(str(stats1.provenance_rows[0]["png_path"]))
    assert png.exists() and png.name.endswith(".nomarker.png")
    first_mtime_ns = png.stat().st_mtime_ns
    first_bytes = png.read_bytes()

    # second run: the real renderer sees the cached PNG and returns it untouched
    stats2 = bds.render_subset(
        subset, chip_targets_path=targets, geometry_version="chip_geom_v2_tight12",
        output_root=chips, draw_marker=False, downloader=fake_downloader,
    )
    assert stats2.rendered == 1
    assert png.stat().st_mtime_ns == first_mtime_ns  # not re-encoded
    assert png.read_bytes() == first_bytes
