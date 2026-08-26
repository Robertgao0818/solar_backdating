"""Citywide scoring ramps at QPS 6; steady-state ceiling stays 40."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIPELINE = REPO / "scripts/temporal/run_ct_citywide_disk_pipeline.sh"


def test_score_wave_ramps_at_qps6_with_in_flight_ceiling_40() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    assert "--anchor-workers 40 --qps 6 --gemini-max-in-flight 40" in text
    assert "--workers 40 --qps 6 --max-in-flight 40" in text
    assert "--gemini-max-in-flight 6" not in text
    assert "--max-in-flight 6" not in text
    assert "--max-in-flight 0" not in text
    assert "--gemini-max-in-flight 0" not in text
