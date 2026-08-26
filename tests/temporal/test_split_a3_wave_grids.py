"""Offline tests for A3 host grid split (no GEHI)."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.temporal.split_a3_wave_grids import assign_grids, main, parse_shares


def test_parse_shares_rejects_single_host() -> None:
    try:
        parse_shares("rog=1")
    except ValueError as exc:
        assert "two host" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_assign_grids_balances_dense_cell() -> None:
    counts = {
        "DENSE": 8000,
        "A": 100,
        "B": 100,
        "C": 100,
        "D": 100,
        "E": 100,
    }
    assigned = assign_grids(counts, {"cursor": 1, "rog": 1})
    cursor_n = sum(counts[g] for g in assigned["cursor"])
    rog_n = sum(counts[g] for g in assigned["rog"])
    assert assigned["cursor"] or assigned["rog"]
    # DENSE must sit alone-ish; the other host gets the five small grids.
    dense_host = "cursor" if "DENSE" in assigned["cursor"] else "rog"
    other = "rog" if dense_host == "cursor" else "cursor"
    assert "DENSE" in assigned[dense_host]
    assert "DENSE" not in assigned[other]
    assert abs(cursor_n - rog_n) <= counts["DENSE"]


def test_main_writes_files(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors.csv"
    cands = tmp_path / "candidates.csv"
    with anchors.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "source_grid"])
        w.writeheader()
        w.writerow({"anchor_id": "a1", "source_grid": "G1"})
        w.writerow({"anchor_id": "a2", "source_grid": "G2"})
        w.writerow({"anchor_id": "a3", "source_grid": "G3"})
    with cands.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "capture_date"])
        w.writeheader()
        for i in range(5):
            w.writerow({"anchor_id": "a1", "capture_date": f"2019-01-0{i+1}"})
        w.writerow({"anchor_id": "a2", "capture_date": "2019-01-01"})
        w.writerow({"anchor_id": "a3", "capture_date": "2019-01-01"})
    out = tmp_path / "split"
    main(
        [
            "--candidates-csv",
            str(cands),
            "--anchors-csv",
            str(anchors),
            "--out-dir",
            str(out),
            "--shares",
            "rog=1,cursor=1",
        ]
    )
    assert (out / "grids_rog.txt").is_file()
    assert (out / "grids_cursor.txt").is_file()
    summary = (out / "split_summary.json").read_text(encoding="utf-8")
    assert "ct_citywide_a3_grid_split_v1" in summary
    all_grids = set(
        (out / "grids_rog.txt").read_text().split()
        + (out / "grids_cursor.txt").read_text().split()
    )
    assert all_grids == {"G1", "G2", "G3"}
