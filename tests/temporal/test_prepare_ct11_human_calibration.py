from scripts.temporal.prepare_ct11_human_calibration import select_calibration


def test_selection_includes_all_repeat_conflicts_and_is_deterministic() -> None:
    manifest = []
    pass1 = {}
    repeats = {}
    classes = ["ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE"]
    for index in range(1, 41):
        anchor = f"a{index:03d}"
        cls = classes[index % len(classes)]
        manifest.append(
            {
                "blind_index": index,
                "anchor_id": anchor,
                "grid_id": f"g{index % 4}",
                "frame_dates": ["2020-01-01", "2021-01-01", "2022-01-01"][: 2 + index % 2],
            }
        )
        pass1[index] = {"independent_class": cls}
        if index <= 7:
            repeats[anchor] = {"independent_class": classes[(index + 1) % len(classes)]}
    first = select_calibration(manifest, pass1, repeats, n=20, seed=42)
    second = select_calibration(manifest, pass1, repeats, n=20, seed=42)
    assert first == second
    assert len({row["anchor_id"] for row in first}) == 20
    conflicts = {row["anchor_id"] for row in first if row["selection_reason"] == "repeat_state_conflict"}
    assert conflicts == set(repeats)
