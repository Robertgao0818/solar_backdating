import json
from pathlib import Path

from scripts.temporal.run_ct11_reference_anchored_diagnostic import (
    choose_balanced_reference_eligible,
    resolve_reference_result,
)


def test_resolve_reference_result_uses_newest_explicit_reference() -> None:
    state = {
        "rounds": [
            {
                "results": [
                    {"capture_date": "2025-02-01", "chip_path": "/a.tif", "reference_only": True},
                    {"capture_date": "2024-01-01", "chip_path": "/b.tif"},
                ]
            },
            {
                "results": [
                    {"capture_date": "2025-10-30", "chip_path": "/c.tif", "reference_only": True}
                ]
            },
        ]
    }
    assert resolve_reference_result(state)["chip_path"] == "/c.tif"


def test_resolve_reference_result_fails_closed_without_path() -> None:
    state = {"rounds": [{"results": [{"capture_date": "2025-10-30", "reference_only": True}]}]}
    assert resolve_reference_result(state) is None


def test_choose_balanced_reference_eligible_is_deterministic(tmp_path: Path) -> None:
    rows = []
    states = {}
    for label in ("ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE"):
        for index in range(3):
            anchor_id = f"{label}-{index}"
            rows.append(
                {
                    "anchor_id": anchor_id,
                    "blind_index": str(len(rows) + 1),
                    "independent_class": label,
                }
            )
            state_path = tmp_path / f"{anchor_id}.json"
            state = {
                "rounds": [
                    {
                        "results": [
                            {
                                "capture_date": "2025-10-30",
                                "chip_path": f"/{anchor_id}.tif",
                                "reference_only": True,
                            }
                        ]
                    }
                ]
            }
            state_path.write_text(json.dumps(state))
            states[anchor_id] = (state_path, state)
    first = choose_balanced_reference_eligible(rows, states, n_per_class=2, seed=17)
    second = choose_balanced_reference_eligible(rows, states, n_per_class=2, seed=17)
    assert first == second
    assert len(first) == 8
    assert {row["corrected_class"] for row in first} == {
        "ALREADY_PRESENT",
        "ALL_ABSENT",
        "TRANSITION",
        "UNDATABLE",
    }
