"""WI-3 (ISSUE-11 prep) — full-stack recovered-window rendering + naming guard.

Covers the WP-A-facing acceptance criteria of
``docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md`` §WI-3:

* AC-3.2 — ``resolve_scan_chip`` never mis-picks an ``fsarm_`` full-stack chip as a
  production scan frame (distinct namespace), on both the exact and loose paths;
* AC-3.3 — a dispute anchor's strip renders a DISTINCT second row (own
  ``.chip-strip.fullstack`` container) with the exact row label, the exact scale-caveat
  banner, and a ``FPD=<date>`` caption token per owned target (incl. the tie target);
* AC-3.4 — a dispute anchor WITH full-stack chips is rescued (datable, no banner) while a
  dispute anchor absent from the frozen inventory stays UNDATABLE (banner + builder
  report row with the ``_no_fullstack`` reason).

The WP-C copy is exercised with an injected runner that must NEVER fire (the frozen tifs
are alive on disk) — no GEHI network / .NET.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.temporal.build_goldset_strip_html import (
    FULLSTACK_ROW_LABEL,
    SCALE_CAVEAT_TEXT,
    UNDATABLE_BANNER_TEXT,
    _anchor_chip_dir,
    anchor_is_undatable,
    build_group_html,
    collect_undatable_anchors,
    resolve_scan_chip,
)
from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.geid_temporal_common import read_csv_rows
from scripts.temporal.goldset_schema import read_sample_assignments
from scripts.temporal.rerender_goldset_windows import rerender_from_assignments
from scripts.temporal.scan_state import Round, ScanState, save_scan_state, state_path_for

FIX = Path(__file__).parent / "fixtures" / "goldset_fullstack"
ASSIGNMENTS = FIX / "sample_assignments.csv"
CHIPGROUPS = FIX / "chip_groups_as_anchors.csv"
UNITS = FIX / "per_unit_fullstack.csv"

DISPUTE_ANCHORS = ("fs_c0000542", "fs_c0009873")


# ---------------------------------------------------------------------------
# fixture materialization (frozen tifs live in tmp; the CSV carries {FROZEN})


def _degenerate_state(anchor_id: str) -> ScanState:
    st = ScanState(anchor_id=anchor_id, region_key="johannesburg", grid_id="JNB01")
    st.status = "done_ambiguous_no_recent_anchor"
    st.rounds = [Round(round_id=1, round_type="initial", window_start_date=None,
                       window_end_date=None, results=[], completed=True)]
    return st


def _materialize_artifacts(tmp_path: Path, *, missing_dates: set[str] = frozenset()) -> Path:
    """Resolve ``{FROZEN}`` -> a tmp frozen dir and create the source tifs on disk."""
    from PIL import Image

    frozen = tmp_path / "frozen"
    art_txt = (FIX / "artifacts_fullstack.csv").read_text(encoding="utf-8").replace("{FROZEN}", str(frozen))
    art_csv = tmp_path / "artifacts_fullstack.csv"
    art_csv.write_text(art_txt, encoding="utf-8")
    for row in read_csv_rows(art_csv):
        if row["capture_date"] in missing_dates:
            continue
        p = Path(row["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (10, 90, 160)).save(p, format="TIFF")
    return art_csv


def _fail_runner(cmd_args, *, executable, timeout):
    return GehiRunResult(args=tuple(str(a) for a in cmd_args), returncode=1, stdout="", stderr="no-run")


def _run_wpc(tmp_path: Path, *, missing_dates: set[str] = frozenset(), runner=None):
    art = _materialize_artifacts(tmp_path, missing_dates=missing_dates)
    scan_dir = tmp_path / "scan_states"
    for aid in DISPUTE_ANCHORS:
        save_scan_state(_degenerate_state(aid), state_path_for(aid, scan_dir))
    root = tmp_path / "out"
    rows = rerender_from_assignments(
        ASSIGNMENTS, scan_states_dir=scan_dir, chipgroups_csv=CHIPGROUPS, root=root,
        coj_chips_dir=None, fullstack_artifacts_csv=art, fullstack_units_csv=UNITS,
        fullstack_flank=2, runner=runner or _fail_runner,
    )
    return root, rows, scan_dir


def _rows_a() -> list[dict[str, str]]:
    return [r for r in read_sample_assignments(ASSIGNMENTS) if r["annotator_id"] == "A"]


# ---------------------------------------------------------------------------
# AC-3.2 — production scan naming never collides with fsarm_


def test_ac3_2_resolve_scan_chip_excludes_fsarm(tmp_path: Path) -> None:
    d = tmp_path / "chips"
    d.mkdir()
    (d / "scan_2015-11-30_v1.png").write_bytes(b"scan")
    (d / "fsarm_2015-11-30_v9.png").write_bytes(b"fsarm")
    # exact (versioned) path resolves the production scan chip.
    assert resolve_scan_chip(d, "2015-11-30", 1).name == "scan_2015-11-30_v1.png"
    # loose (no version) path still returns the scan chip, never the fsarm one.
    assert resolve_scan_chip(d, "2015-11-30", None).name == "scan_2015-11-30_v1.png"
    # a date that has ONLY an fsarm chip resolves to nothing (never mis-picked).
    (d / "fsarm_2019-01-01_v3.png").write_bytes(b"fsarm2")
    assert resolve_scan_chip(d, "2019-01-01", None) is None
    assert resolve_scan_chip(d, "2019-01-01", 3) is None


# ---------------------------------------------------------------------------
# AC-3.3 — distinct second row + captions + scale caveat


def test_ac3_3_distinct_row_captions_and_caveat(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    root, _rows, scan_dir = _run_wpc(tmp_path)
    html = build_group_html(_rows_a(), scan_states_dir=scan_dir,
                            rerender_dir=root / "rerender", thumbnail_size=32)
    assert FULLSTACK_ROW_LABEL in html                    # exact row label
    assert SCALE_CAVEAT_TEXT in html                      # exact scale-caveat banner
    assert "class='chip-strip fullstack'" in html         # its own container
    assert "FPD=2015-01-30" in html                       # t00000001 / t00000002 modal
    assert "FPD=2018-01-30/2020-01-30" in html            # t00000003 tie -> both dates
    assert "◀ claimed FPD" in html                        # modal-frame marker


# ---------------------------------------------------------------------------
# AC-3.4 — c0000542-shaped rescued datable; c0009873-shaped banner-only


def test_ac3_4_datable_rescue_vs_banner_only(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    root, _rows, scan_dir = _run_wpc(tmp_path)
    cd542 = _anchor_chip_dir(root / "rerender", "fs_c0000542")
    cd873 = _anchor_chip_dir(root / "rerender", "fs_c0009873")
    # present in the frozen inventory -> fsarm chips -> DATABLE (no banner).
    assert anchor_is_undatable(_degenerate_state("fs_c0000542"), cd542) is False
    # absent from the frozen inventory -> no fsarm chips -> UNDATABLE (banner-only).
    assert anchor_is_undatable(_degenerate_state("fs_c0009873"), cd873) is True

    rows_a = _rows_a()
    html = build_group_html(rows_a, scan_states_dir=scan_dir,
                            rerender_dir=root / "rerender", thumbnail_size=32)
    sec542 = html.split("data-anchor-id='fs_c0000542'")[1].split("</section>")[0]
    sec873 = html.split("data-anchor-id='fs_c0009873'")[1].split("</section>")[0]
    assert UNDATABLE_BANNER_TEXT not in sec542
    assert UNDATABLE_BANNER_TEXT in sec873

    undatable = collect_undatable_anchors(rows_a, scan_dir, root / "rerender")
    ids = [u["anchor_id"] for u in undatable]
    assert "fs_c0009873" in ids and "fs_c0000542" not in ids
    row873 = next(u for u in undatable if u["anchor_id"] == "fs_c0009873")
    assert row873["reason"] == "no_usable_dated_rounds_no_fullstack"
