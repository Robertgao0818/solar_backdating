from __future__ import annotations

from pathlib import Path

from scripts.temporal import build_anchors_v2_preflight_qa as qa


def _rows() -> list[dict[str, str]]:
    return [
        {
            "anchor_id": f"{arm}-{index:02d}",
            "chip_arm": arm,
            "geometry_version": f"geom-{arm}",
            "grid_id": "JNB0001",
            "source_area_m2": str(index + 1),
        }
        for arm in ("A24", "A48")
        for index in range(20)
    ]


def test_sample_is_deterministic_and_balances_both_arms() -> None:
    first = qa.sample_anchors(_rows(), count=30, seed=123)
    second = qa.sample_anchors(list(reversed(_rows())), count=30, seed=123)

    assert first == second
    assert sum(row["chip_arm"] == "A24" for row in first) == 15
    assert sum(row["chip_arm"] == "A48" for row in first) == 15
    assert len({row["anchor_id"] for row in first}) == 30


def test_render_preflight_uses_production_renderer_for_each_arm(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[float, str, Path]] = []

    def fake_factory(extent_m: float):
        def render(path: Path, row: dict[str, str]) -> Path:
            calls.append((extent_m, row["anchor_id"], path))
            out = tmp_path / f"{row['anchor_id']}.review.png"
            out.write_bytes(b"PNG")
            return out

        return render

    monkeypatch.setattr(qa, "make_fixed_extent_review_renderer", fake_factory)
    monkeypatch.setattr(
        qa,
        "vexcel_tif_to_reference_png",
        lambda path: path.with_suffix(".png"),
    )
    rows = [
        {
            "anchor_id": "a24",
            "chip_arm": "A24",
            "geometry_version": "fullscan_target96_review24_v2",
            "grid_id": "JNB0001",
            "source_area_m2": "10",
        },
        {
            "anchor_id": "a48",
            "chip_arm": "A48",
            "geometry_version": "fullscan_target96_review48_v2",
            "grid_id": "JNB0001",
            "source_area_m2": "80",
        },
    ]
    references = {}
    for row in rows:
        path = tmp_path / f"{row['anchor_id']}.tif"
        path.write_bytes(b"II*\x00payload")
        references[row["anchor_id"]] = path

    rendered = qa.render_preflight_rows(rows, references)

    assert [(extent, anchor_id) for extent, anchor_id, _path in calls] == [
        (24.0, "a24"),
        (48.0, "a48"),
    ]
    assert [row["anchor_id"] for row in rendered] == ["a24", "a48"]
