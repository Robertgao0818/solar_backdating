from __future__ import annotations

import csv
import json

from PIL import Image

from scripts.temporal.render_ct11_native_review import render_native_review


def test_native_review_preserves_source_pixels_and_blindness(tmp_path):
    chips = tmp_path / "chips"
    chips.mkdir()
    rows = []
    dates = ["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01"]
    for index, date in enumerate(dates):
        path = chips / f"frame_{index}.png"
        Image.new("RGB", (398, 314), (index * 20, 40, 80)).save(path)
        rows.append(
            {
                "anchor_id": "secret-anchor",
                "capture_date": date,
                "source": "scan_tm",
                "status": "ok",
                "chip_path": str(path),
            }
        )
    report = tmp_path / "frame_report.csv"
    with report.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "blind_index": 1,
                    "anchor_id": "secret-anchor",
                    "grid_id": "CPT0001",
                    "frame_dates": dates,
                }
            ]
        )
    )

    output = tmp_path / "native"
    package = render_native_review(manifest, report, output, rows_per_sheet=1)

    assert package["n_anchors"] == 1
    assert package["contains_anchor_ids"] is False
    assert package["contains_production_outcomes"] is False
    rendered = json.loads((output / "native_manifest.json").read_text())
    assert rendered[0]["frames"][0]["source_size"] == [398, 314]
    assert rendered[0]["frames"][0]["rendered_size"] == [398, 314]
    assert "secret-anchor" not in (output / "native_manifest.json").read_text()
    assert Image.open(output / "native_001.png").size[0] >= 398 * 2


def test_native_review_rejects_more_than_four_frames(tmp_path):
    report = tmp_path / "frame_report.csv"
    report.write_text("anchor_id,capture_date,source,status,chip_path\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "blind_index": 1,
                    "anchor_id": "a",
                    "grid_id": "CPT0001",
                    "frame_dates": [str(i) for i in range(5)],
                }
            ]
        )
    )

    try:
        render_native_review(manifest, report, tmp_path / "out")
    except ValueError as error:
        assert "at most four" in str(error)
    else:
        raise AssertionError("expected ValueError")
