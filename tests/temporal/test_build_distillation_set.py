"""ISSUE-02 — distillation training-set builder (label harvest + GEHI re-render).

Regresses ``scripts/temporal/build_distillation_set.py``. Acceptance criteria
covered (see docs/dinov3_scorer/ISSUE-02-distillation-training-set.md):

- a. label rule: usable + high-confidence -> present/absent; ambiguous/unusable
     observation -> unusable; done_ambiguous_* anchor vetoes all its rounds.
- b. done_ambiguous_* anchors excluded (emitted as label_3class=unusable, never
     with a guessed present/absent label).
- c. train / held-out split is ANCHOR-DISJOINT (real disjointness assertion).
- d. chip_targets.csv missing -> loud failure naming the expected path.
- e. chip re-render drop-count: download stubbed to all_zooms_failed on some
     rounds -> those rounds dropped + counted; renderer stubbed; NO network.
- f. geometry_version (chip_geom_v2_tight12) recorded in the render provenance.

House conventions (tests/temporal): function-style, ``tmp_path`` for all IO,
fabricate scan_state via ``scripts.temporal.scan_state`` dataclasses +
``save_scan_state``, stub the GEHI seam (never touch the network / GPU).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.temporal import build_distillation_set as bds
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.scan_state import (
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)

# ---------------------------------------------------------------------------
# fabrication helpers
# ---------------------------------------------------------------------------


def _round(results: list[RoundResult], round_id: int = 1) -> Round:
    return Round(
        round_id=round_id,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        results=results,
        completed=True,
    )


def _result(
    *,
    pv_present: bool | None,
    confidence: float | None,
    quality_flag: str = "usable",
    capture_date: str = "2020-06-15",
    version: int = 100,
    zoom: int | None = 20,
) -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=version,
        pv_present=pv_present,
        confidence=confidence,
        quality_flag=quality_flag,
        decision_source="gemini_batch",
        actual_zoom=zoom,
    )


def _state(
    anchor_id: str,
    *,
    status: str,
    results: list[RoundResult],
    grid_id: str = "JNB0373",
) -> ScanState:
    return ScanState(
        anchor_id=anchor_id,
        region_key="johannesburg",
        grid_id=grid_id,
        status=status,
        rounds=[_round(results)],
    )


def _write_states(scan_dir: Path, states: list[ScanState]) -> None:
    for st in states:
        save_scan_state(st, state_path_for(st.anchor_id, scan_dir))


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
        "anchor_id": anchor_id,
        "chip_id": chip_id,
        "region_key": "johannesburg",
        "grid_id": grid_id,
        "target_label": "A",
        "target_index": 0,
        "centroid_lon": 28.05,
        "centroid_lat": -26.20,
        "source_width_m": 8.0,
        "source_height_m": 8.0,
        "target_offset_x_m": 0.0,
        "target_offset_y_m": 0.0,
        "search_radius_m": 5.0,
        "chip_size_m": 96.0,
        "chip_lon_min": 28.049,
        "chip_lat_min": -26.201,
        "chip_lon_max": 28.051,
        "chip_lat_max": -26.199,
    }


# ---------------------------------------------------------------------------
# a. label rule
# ---------------------------------------------------------------------------


def test_label_rule_usable_highconf_present() -> None:
    assert bds.label_3class(
        pv_present=True, confidence=0.95, quality_flag="usable",
        terminal_status="done_appears",
    ) == "present"


def test_label_rule_usable_highconf_absent() -> None:
    assert bds.label_3class(
        pv_present=False, confidence=0.98, quality_flag="usable",
        terminal_status="done_appears",
    ) == "absent"


def test_label_rule_ambiguous_flag_is_unusable() -> None:
    assert bds.label_3class(
        pv_present=True, confidence=0.99, quality_flag="ambiguous",
        terminal_status="done_appears",
    ) == "unusable"


def test_label_rule_unusable_flag_is_unusable() -> None:
    assert bds.label_3class(
        pv_present=False, confidence=0.99, quality_flag="unusable",
        terminal_status="done_appears",
    ) == "unusable"


def test_label_rule_low_confidence_is_unusable() -> None:
    assert bds.label_3class(
        pv_present=True, confidence=0.80, quality_flag="usable",
        terminal_status="done_appears",
    ) == "unusable"


def test_label_rule_none_pv_present_is_unusable() -> None:
    # the 1,037 ('usable', None) observations on disk must fall through
    assert bds.label_3class(
        pv_present=None, confidence=0.95, quality_flag="usable",
        terminal_status="done_appears",
    ) == "unusable"


def test_label_rule_stray_confidence_above_one_is_unusable() -> None:
    # the single 1.5 verdict must be rejected, not clamped to present
    assert bds.label_3class(
        pv_present=True, confidence=1.5, quality_flag="usable",
        terminal_status="done_appears",
    ) == "unusable"


def test_label_rule_ambiguous_terminal_vetoes_present() -> None:
    # anchor-level veto: a usable high-conf round on a done_ambiguous_* anchor
    # is still unusable (marker-misregistration taints the whole series).
    assert bds.label_3class(
        pv_present=True, confidence=0.95, quality_flag="usable",
        terminal_status="done_ambiguous_nonmonotonic",
    ) == "unusable"


# ---------------------------------------------------------------------------
# b. done_ambiguous_* anchors excluded (rows retained as unusable)
# ---------------------------------------------------------------------------


def test_done_ambiguous_anchor_all_rows_unusable(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    _write_states(
        scan_dir,
        [
            _state(
                "corpus_c0000001",
                status="done_ambiguous_nonmonotonic",
                results=[
                    _result(pv_present=True, confidence=0.95),
                    _result(pv_present=False, confidence=0.98, capture_date="2019-01-01"),
                ],
            )
        ],
    )
    rows, stats = bds.harvest_corpus({"cg": scan_dir}, cbd_grids=frozenset())
    assert rows, "ambiguous anchor rows must be retained (as unusable), not dropped"
    assert {r["label_3class"] for r in rows} == {"unusable"}
    assert stats.ambiguous_anchors == 1


def test_harvest_labels_present_and_absent_for_clean_anchor(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    _write_states(
        scan_dir,
        [
            _state(
                "corpus_c0000002",
                status="done_appears",
                results=[
                    _result(pv_present=True, confidence=0.95, capture_date="2021-01-01"),
                    _result(pv_present=False, confidence=0.9, capture_date="2018-01-01"),
                ],
            )
        ],
    )
    rows, stats = bds.harvest_corpus({"cg": scan_dir}, cbd_grids=frozenset())
    labels = sorted(r["label_3class"] for r in rows)
    assert labels == ["absent", "present"]
    assert stats.unique_anchors == 1
    assert stats.total_rounds == 2


def test_manifest_has_required_columns(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    _write_states(
        scan_dir,
        [_state("corpus_t00000001", status="done_appears",
                results=[_result(pv_present=True, confidence=0.95)])],
    )
    rows, _ = bds.harvest_corpus({"pt": scan_dir}, cbd_grids=frozenset())
    required = {
        "anchor_id", "region", "grid_id", "capture_date", "version",
        "actual_zoom", "pv_present", "confidence", "quality_flag",
        "terminal_status", "label_3class", "sub_domain",
    }
    assert required <= set(rows[0].keys())
    # region flows from the anchor state, never inferred from grid_id
    assert rows[0]["region"] == "johannesburg"


def test_sub_domain_resolved_from_grid_membership(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    _write_states(
        scan_dir,
        [
            _state("corpus_c0000010", status="done_appears", grid_id="JNB0133",
                   results=[_result(pv_present=True, confidence=0.95)]),
            _state("corpus_c0000011", status="done_appears", grid_id="JNB0999",
                   results=[_result(pv_present=True, confidence=0.95)]),
        ],
    )
    rows, _ = bds.harvest_corpus({"cg": scan_dir}, cbd_grids=frozenset({"JNB0133"}))
    by_anchor = {r["anchor_id"]: r["sub_domain"] for r in rows}
    assert by_anchor["corpus_c0000010"] == "cbd_aerial_mosaic"
    assert by_anchor["corpus_c0000011"] == "non_cbd_satellite"


# ---------------------------------------------------------------------------
# c. anchor-disjoint split
# ---------------------------------------------------------------------------


def test_split_is_anchor_disjoint() -> None:
    anchors = [f"corpus_c{i:07d}" for i in range(500)]
    assignment = bds.assign_splits(anchors, heldout_frac=0.2)
    train = {a for a, s in assignment.items() if s == "train"}
    heldout = {a for a, s in assignment.items() if s == "heldout"}
    assert train and heldout
    assert train.isdisjoint(heldout)
    assert train | heldout == set(anchors)


def test_split_is_deterministic() -> None:
    anchors = [f"corpus_t{i:08d}" for i in range(300)]
    a1 = bds.assign_splits(anchors, heldout_frac=0.25, salt="fixed")
    a2 = bds.assign_splits(list(reversed(anchors)), heldout_frac=0.25, salt="fixed")
    assert a1 == a2


def test_harvest_then_split_no_anchor_in_both(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_states"
    _write_states(
        scan_dir,
        [
            _state(f"corpus_c{i:07d}", status="done_appears",
                   results=[_result(pv_present=bool(i % 2), confidence=0.95)])
            for i in range(40)
        ],
    )
    rows, _ = bds.harvest_corpus({"cg": scan_dir}, cbd_grids=frozenset())
    anchors = sorted({r["anchor_id"] for r in rows})
    assignment = bds.assign_splits(anchors, heldout_frac=0.3)
    for r in rows:
        r["split"] = assignment[r["anchor_id"]]
    train = {r["anchor_id"] for r in rows if r["split"] == "train"}
    heldout = {r["anchor_id"] for r in rows if r["split"] == "heldout"}
    assert train.isdisjoint(heldout)


# ---------------------------------------------------------------------------
# d. chip_targets.csv hard dependency
# ---------------------------------------------------------------------------


def test_missing_chip_targets_csv_fails_loudly(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist" / "chip_targets.csv"
    with pytest.raises((FileNotFoundError, SystemExit)) as exc:
        bds.load_chip_targets_lookup(missing)
    assert str(missing) in str(exc.value)


def test_empty_chip_targets_csv_fails_loudly(tmp_path: Path) -> None:
    empty = tmp_path / "chip_targets.csv"
    _write_chip_targets(empty, [])
    with pytest.raises((ValueError, SystemExit)) as exc:
        bds.load_chip_targets_lookup(empty)
    assert "chip_targets" in str(exc.value).lower() or "empty" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# e. chip re-render drop-count (GEHI stubbed, no network)
# ---------------------------------------------------------------------------


def _ok_download(path: Path, zoom: int = 20) -> DownloadResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"TIF")
    return DownloadResult(
        anchor_id="a", capture_date="2020-06-15", version="100",
        requested_zoom_ladder=(zoom,), actual_zoom=zoom, path=path,
        sha256="deadbeef", status="ok", error=None, gehi_command="",
        download_stdout_sha256="",
    )


def _failed_download() -> DownloadResult:
    return DownloadResult(
        anchor_id="a", capture_date="2020-06-15", version="100",
        requested_zoom_ladder=(20,), actual_zoom=None, path=None,
        sha256="", status="all_zooms_failed", error="no vintage",
        gehi_command="", download_stdout_sha256="",
    )


def test_render_drop_count_with_stubbed_gehi(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(
        targets,
        [
            _chip_target_row("corpus_t00000001", "corpus_c0000001"),
            _chip_target_row("corpus_t00000002", "corpus_c0000001"),
        ],
    )
    subset = [
        {"anchor_id": "corpus_t00000001", "capture_date": "2020-06-15",
         "version": 100, "actual_zoom": 20},
        {"anchor_id": "corpus_t00000002", "capture_date": "2019-01-01",
         "version": 90, "actual_zoom": 20},
    ]

    rendered_calls: list[Path] = []

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        # first survives, second is unrecoverable
        if capture_date == "2020-06-15":
            tif = Path(output_root) / f"{anchor['anchor_id']}_{capture_date}.tif"
            return _ok_download(tif)
        return _failed_download()

    def fake_renderer(image_path, target_marker, **kw):
        png = Path(str(image_path)).with_suffix(".png")
        png.write_bytes(b"PNG")
        rendered_calls.append(png)
        return png

    stats = bds.render_subset(
        subset,
        chip_targets_path=targets,
        geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips",
        downloader=fake_downloader,
        renderer=fake_renderer,
    )
    assert stats.rendered == 1
    assert stats.dropped_unrecoverable == 1
    assert len(rendered_calls) == 1
    assert len(stats.drop_log) == 1


def test_render_unresolvable_join_is_counted_separately(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [{"anchor_id": "corpus_t99999999", "capture_date": "2020-06-15",
               "version": 100, "actual_zoom": 20}]

    def fake_downloader(anchor, **kw):  # pragma: no cover - must not be called
        raise AssertionError("download must not run for an unresolvable anchor")

    def fake_renderer(*a, **k):  # pragma: no cover
        raise AssertionError("render must not run for an unresolvable anchor")

    stats = bds.render_subset(
        subset, chip_targets_path=targets,
        geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips",
        downloader=fake_downloader, renderer=fake_renderer,
    )
    assert stats.rendered == 0
    assert stats.dropped_unresolved == 1


# ---------------------------------------------------------------------------
# f. geometry_version recorded in provenance
# ---------------------------------------------------------------------------


def test_geometry_version_recorded_in_provenance(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [{"anchor_id": "corpus_t00000001", "capture_date": "2020-06-15",
               "version": 100, "actual_zoom": 20}]

    def fake_downloader(anchor, *, capture_date, version, zoom_ladder, output_root, **kw):
        tif = Path(output_root) / f"{anchor['anchor_id']}.tif"
        return _ok_download(tif)

    def fake_renderer(image_path, target_marker, **kw):
        png = Path(str(image_path)).with_suffix(".png")
        png.write_bytes(b"PNG")
        return png

    stats = bds.render_subset(
        subset, chip_targets_path=targets,
        geometry_version="chip_geom_v2_tight12",
        output_root=tmp_path / "chips",
        downloader=fake_downloader, renderer=fake_renderer,
    )
    assert stats.provenance_rows
    assert all(p["geometry_version"] == "chip_geom_v2_tight12" for p in stats.provenance_rows)


def test_render_unknown_geometry_version_fails_loudly(tmp_path: Path) -> None:
    targets = tmp_path / "chip_targets.csv"
    _write_chip_targets(targets, [_chip_target_row("corpus_t00000001", "corpus_c0000001")])
    subset = [{"anchor_id": "corpus_t00000001", "capture_date": "2020-06-15",
               "version": 100, "actual_zoom": 20}]
    with pytest.raises(KeyError, match="unknown chip geometry_version"):
        bds.render_subset(
            subset, chip_targets_path=targets,
            geometry_version="chip_geom_v99_missing",
            output_root=tmp_path / "chips",
            downloader=lambda *a, **k: None, renderer=lambda *a, **k: None,
        )


# ---------------------------------------------------------------------------
# g. harvest_corpus fails loudly on a missing corpus dir (silent-drop bug)
# ---------------------------------------------------------------------------


def test_harvest_corpus_missing_dir_fails_loudly(tmp_path: Path) -> None:
    # a renamed / moved corpus root must NOT be silently skipped from the harvest
    missing = tmp_path / "renamed_away" / "scan_states"
    with pytest.raises(FileNotFoundError) as exc:
        bds.harvest_corpus({"wayback": missing}, cbd_grids=frozenset())
    assert str(missing) in str(exc.value)


# ---------------------------------------------------------------------------
# h. census2023 harvest + full 4-corpus reconcile (criteria 2 + 7)
# ---------------------------------------------------------------------------


def _write_census(
    audit_dir: Path,
    intervals_csv: Path,
    anchors: dict[str, dict[str, object]],
) -> None:
    """Fabricate a census2023 corpus: one jsonl audit file + interval-join CSV.

    ``anchors`` maps anchor_id -> {"grid_id", "status", "quality_flag",
    "confidence", "observations": [{"capture_date", "pv_present"}, ...]}.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    intervals_csv.parent.mkdir(parents=True, exist_ok=True)
    with intervals_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "region_key", "grid_id", "status"])
        w.writeheader()
        for anchor_id, meta in anchors.items():
            w.writerow(
                {
                    "anchor_id": anchor_id,
                    "region_key": "johannesburg",
                    "grid_id": meta.get("grid_id", "JNB0373"),
                    "status": meta.get("status", "done_appears"),
                }
            )
    for anchor_id, meta in anchors.items():
        line = {
            "ok": True,
            "anchor_id": anchor_id,
            "parsed": {
                "quality_flag": meta.get("quality_flag", "usable"),
                "confidence": meta.get("confidence", 0.95),
                "observations": meta.get("observations", []),
            },
        }
        (audit_dir / f"{anchor_id}.jsonl").write_text(
            json.dumps(line) + "\n", encoding="utf-8"
        )


def test_harvest_all_dedups_and_includes_census(tmp_path: Path) -> None:
    # chip_group owns anchor c1; wayback re-scans it (one EXACT-vintage duplicate
    # round + one new vintage); census contributes a NEW label-only c-anchor.
    cg = tmp_path / "cg"
    wb = tmp_path / "wb"
    _write_states(
        cg,
        [_state("corpus_c0000001", status="done_appears",
                results=[_result(pv_present=True, confidence=0.95,
                                 capture_date="2020-06-15", version=100)])],
    )
    _write_states(
        wb,
        [_state("corpus_c0000001", status="done_appears",
                results=[
                    _result(pv_present=True, confidence=0.95,
                            capture_date="2020-06-15", version=100),   # exact dup
                    _result(pv_present=False, confidence=0.92,
                            capture_date="2021-06-15", version=110),   # new vintage
                ])],
    )
    audit_dir = tmp_path / "census2023" / "census_audit"
    intervals = tmp_path / "census2023" / "census_install_intervals.csv"
    _write_census(
        audit_dir, intervals,
        {
            "corpus_c9999999": {
                "grid_id": "JNB0999", "status": "done_appears",
                "observations": [
                    {"capture_date": "2019-01-01", "pv_present": False},
                    {"capture_date": "2023-01-01", "pv_present": True},
                ],
            }
        },
    )

    rows, stats = bds.harvest_all(
        {"chip_group": cg, "wayback": wb},
        cbd_grids=frozenset(),
        census_audit_dir=audit_dir,
        census_intervals_csv=intervals,
        include_census=True,
    )

    # criterion 7: census contributes a new c-namespace anchor + label-only rows
    assert stats.census_anchors == 1
    assert stats.census_rows == 2
    anchor_ids = {str(r["anchor_id"]) for r in rows}
    assert "corpus_c9999999" in anchor_ids
    assert stats.unique_anchors == 2
    # criterion 2: exact-vintage re-scan collapsed post-dedup, all four corpora
    assert stats.duplicate_rounds_collapsed == 1
    # before dedup: cg(1) + wb(2) + census(2) = 5; one dup collapsed -> 4
    assert stats.total_rounds == 4
    # census rows are label-only (version="") -> excluded from the render subset
    census_rows = [r for r in rows if r["corpus"] == "census2023"]
    assert census_rows and all(r["version"] == "" for r in census_rows)


def test_harvest_all_missing_census_audit_dir_fails_loudly(tmp_path: Path) -> None:
    cg = tmp_path / "cg"
    _write_states(
        cg,
        [_state("corpus_c0000001", status="done_appears",
                results=[_result(pv_present=True, confidence=0.95)])],
    )
    missing_audit = tmp_path / "no_census" / "census_audit"
    with pytest.raises(FileNotFoundError) as exc:
        bds.harvest_all(
            {"chip_group": cg},
            cbd_grids=frozenset(),
            census_audit_dir=missing_audit,
            census_intervals_csv=tmp_path / "no_census" / "intervals.csv",
            include_census=True,
        )
    assert str(missing_audit) in str(exc.value)


def test_harvest_all_no_census_opt_out(tmp_path: Path) -> None:
    # scan-only sanity run: census skipped, no loud failure on a missing audit dir
    cg = tmp_path / "cg"
    _write_states(
        cg,
        [_state("corpus_c0000001", status="done_appears",
                results=[_result(pv_present=True, confidence=0.95)])],
    )
    rows, stats = bds.harvest_all(
        {"chip_group": cg},
        cbd_grids=frozenset(),
        census_audit_dir=tmp_path / "does_not_exist",
        census_intervals_csv=tmp_path / "does_not_exist.csv",
        include_census=False,
    )
    assert stats.census_rows == 0 and stats.census_anchors == 0
    assert stats.unique_anchors == 1


# ---------------------------------------------------------------------------
# i. per-stratum subset counts + README drop-count documentation (criterion 10)
# ---------------------------------------------------------------------------


def test_summarize_subset_strata_counts_selected_and_eligible() -> None:
    rows = [
        {"anchor_id": "a1", "label_3class": "present", "version": 100,
         "sub_domain": "cbd_aerial_mosaic"},
        {"anchor_id": "a2", "label_3class": "absent", "version": 100,
         "sub_domain": "non_cbd_satellite"},
        {"anchor_id": "a3", "label_3class": "present", "version": 100,
         "sub_domain": "non_cbd_satellite"},
        # census / label-only row must be ineligible (version="")
        {"anchor_id": "a4", "label_3class": "present", "version": "",
         "sub_domain": "non_cbd_satellite"},
    ]
    strata = bds.summarize_subset_strata(rows, selected_ids=["a1", "a3"])
    assert strata["cbd_aerial_mosaic|present"] == {"eligible": 1, "selected": 1}
    assert strata["non_cbd_satellite|present"] == {"eligible": 1, "selected": 1}
    assert strata["non_cbd_satellite|absent"] == {"eligible": 1, "selected": 0}
    assert "a4" not in {k for k in strata}  # label-only excluded entirely


def test_render_chips_cli_updates_readme_and_drop_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "distill_out"
    out.mkdir()
    # a manifest with two selected present/absent rows + harvest_meta.json
    manifest_rows = [
        {c: "" for c in bds.MANIFEST_COLUMNS} | {
            "anchor_id": "corpus_t00000001", "capture_date": "2020-06-15",
            "version": 100, "actual_zoom": 20, "label_3class": "present",
            "sub_domain": "non_cbd_satellite", "corpus": "chip_group", "split": "train",
        },
        {c: "" for c in bds.MANIFEST_COLUMNS} | {
            "anchor_id": "corpus_t00000002", "capture_date": "2019-01-01",
            "version": 90, "actual_zoom": 20, "label_3class": "absent",
            "sub_domain": "cbd_aerial_mosaic", "corpus": "chip_group", "split": "heldout",
        },
    ]
    bds.write_manifest(manifest_rows, out / "label_manifest.csv")
    (out / "chip_subset_anchors.json").write_text(
        json.dumps(["corpus_t00000001", "corpus_t00000002"]), encoding="utf-8"
    )
    (out / "harvest_meta.json").write_text(
        json.dumps(
            {
                "stats": bds.HarvestStats(unique_anchors=2, total_rounds=2).to_dict(),
                "high_conf": 0.90,
                "cbd_resolved": True,
                "geometry_version": "chip_geom_v2_tight12",
                "subset_anchor_ids": ["corpus_t00000001", "corpus_t00000002"],
                "strata_counts": {
                    "cbd_aerial_mosaic|absent": {"eligible": 1, "selected": 1},
                    "non_cbd_satellite|present": {"eligible": 1, "selected": 1},
                },
            }
        ),
        encoding="utf-8",
    )

    fake = bds.RenderStats(
        rendered=1,
        dropped_unrecoverable=1,
        dropped_unresolved=0,
        drop_log=[{"anchor_id": "corpus_t00000002", "reason": "all_zooms_failed"}],
        provenance_rows=[],
    )
    monkeypatch.setattr(bds, "render_subset", lambda *a, **k: fake)

    args = SimpleNamespace(
        output_root=out,
        chip_targets=out / "chip_targets.csv",
        chip_geometry="chip_geom_v2_tight12",
    )
    bds._cmd_render_chips(args)

    # drops.json carries summary counts, not just the raw drop_log
    drops = json.loads((out / "chip_render_drops.json").read_text())
    assert drops["dropped_unrecoverable"] == 1
    assert drops["dropped_unresolved"] == 0
    assert drops["rendered"] == 1
    assert isinstance(drops["drop_log"], list)

    # README no longer shows the placeholder; drop counts + strata are documented
    readme = (out / "README.md").read_text()
    assert "chip re-render not run yet" not in readme
    assert "Chip re-render drop counts" in readme
    assert "dropped (unrecoverable / all_zooms_failed): 1" in readme
    assert "Per-stratum counts" in readme
    assert "cbd_aerial_mosaic|absent: 1 / 1" in readme
