from pathlib import Path

from scripts.validation.issue29_coj_reference_audit import (
    _capture_date,
    _parse_response,
    choose_2025_reference,
    reference_candidates,
)


def test_choose_2025_reference_is_repeatable_and_date_deduplicated():
    paths = [
        Path("/x/a_20250125_v1.tif"),
        Path("/x/a_20250125_v2.tif"),
        Path("/x/a_20250330_v1.tif"),
    ]
    first = choose_2025_reference("anchor-1", paths)
    second = choose_2025_reference("anchor-1", list(reversed(paths)))
    assert first == second
    assert _capture_date(first).startswith("2025-")


def test_parse_response_requires_complete_unique_indices():
    text = """{
      "review_notes": "",
      "observations": [
        {"date_index": 1, "pv_present": true, "confidence": 1,
         "quality_flag": "usable", "evidence": "grid", "notes": ""},
        {"date_index": 2, "pv_present": false, "confidence": 0.9,
         "quality_flag": "usable", "evidence": "clean roof", "notes": ""}
      ]
    }"""
    observations, notes = _parse_response(text, 2)
    assert [row["date_index"] for row in observations] == [1, 2]
    assert notes == ""


def test_reference_candidates_ignore_zero_byte_placeholders(tmp_path):
    anchor = tmp_path / "a1" / "z19"
    anchor.mkdir(parents=True)
    bad = anchor / "a1_20251009_v1.tif"
    good = anchor / "a1_20250330_v1.tif"
    bad.write_bytes(b"")
    good.write_bytes(b"TIFF")
    _, candidates_2025 = reference_candidates("a1", tmp_path)
    assert candidates_2025 == [good]
