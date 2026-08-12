from dataclasses import dataclass

from scripts.temporal.run_ct11_recovery_pilot import classify_observations, write_hash_manifest


@dataclass
class Obs:
    pv_present: bool | None
    quality_flag: str = "usable"


def test_classify_recovery_sequences():
    dates = ["2020-01-01", "2021-01-01", "2022-01-01"]
    assert classify_observations([Obs(True), Obs(True), Obs(True)], dates) == (
        "ALREADY_PRESENT", "", "2020-01-01"
    )
    assert classify_observations([Obs(False), Obs(False), Obs(False)], dates) == (
        "ALL_ABSENT", "2022-01-01", ""
    )
    assert classify_observations([Obs(False), Obs(False), Obs(True)], dates) == (
        "TRANSITION", "2021-01-01", "2022-01-01"
    )
    assert classify_observations([Obs(True), Obs(False), Obs(True)], dates) == (
        "UNDATABLE", "", ""
    )


def test_classify_ignores_unusable_but_not_all_evidence():
    dates = ["2020-01-01", "2021-01-01"]
    assert classify_observations([Obs(None, "unusable"), Obs(True)], dates) == (
        "ALREADY_PRESENT", "", "2021-01-01"
    )
    assert classify_observations([Obs(None, "unusable"), Obs(None, "ambiguous")], dates) == (
        "UNDATABLE", "", ""
    )


def test_hash_manifest_covers_results_and_audits(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "audit").mkdir()
    for name in ("selection.json", "summary.json", "quota_ledger.jsonl"):
        (tmp_path / name).write_text(name)
    (tmp_path / "results" / "001.json").write_text("result")
    (tmp_path / "audit" / "001.jsonl").write_text("audit")

    manifest = write_hash_manifest(tmp_path)

    text = manifest.read_text()
    assert "selection.json" in text
    assert "results/001.json" in text
    assert "audit/001.jsonl" in text
    assert "pilot_outputs.sha256" not in text
