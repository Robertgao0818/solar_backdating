from pathlib import Path

from scripts.temporal.prepare_ct11_corrected_review import state_preference


def test_state_preference_favors_release_lineage():
    paths = [
        Path("/run/pilot_retry_v2/control/scan_states/A24/a.json"),
        Path("/run/canary/gate_retry2/scan_states/A24/a.json"),
        Path("/run/primary/scan_states/a24/a.json"),
    ]

    assert min(paths, key=state_preference) == paths[2]
