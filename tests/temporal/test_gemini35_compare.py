"""The 3.5 T3 comparison must not overwrite the finished 3.1 pack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.temporal.reference_fulltimeline import MODEL_ALIAS, admit_model_version
from scripts.temporal.run_reference_fulltimeline_scan import Scorer, requested_model_alias
from scripts.temporal.run_v2_t3_gemini35_compare import (
    COMPARE_ALIAS,
    baseline_stamp,
    compare_interval_rows,
    compare_status_rows,
    refuse_compare_output,
    remember_baseline,
    require_finished_baseline,
    write_model_comparison,
)
from scripts.temporal.score_v2_fulltimeline import write_products
from scripts.validation import gemini_solar_image_review as gemini_http


def test_compare_alias_is_exact_and_does_not_replace_lite() -> None:
    assert admit_model_version(COMPARE_ALIAS, COMPARE_ALIAS) is True
    assert admit_model_version(COMPARE_ALIAS, MODEL_ALIAS) is False
    assert admit_model_version(MODEL_ALIAS, COMPARE_ALIAS) is False
    assert admit_model_version("gemini-3.8-flash", COMPARE_ALIAS) is False
    assert admit_model_version("gemini-3.5-flash-lite-n", COMPARE_ALIAS) is False
    assert requested_model_alias({}) == MODEL_ALIAS
    assert requested_model_alias({"model_alias": COMPARE_ALIAS}) == COMPARE_ALIAS
    with pytest.raises(SystemExit):
        requested_model_alias({"model_alias": "gemini-3.8-flash"})
    with pytest.raises(SystemExit):
        requested_model_alias({"model_alias": "gemini-flash-3.5-lite"})


def test_output_root_cannot_overlap_a_protected_pack(tmp_path: Path) -> None:
    pack = tmp_path / "v2"
    lite = tmp_path / "lite"
    sibling = tmp_path / "v2_gemini35"
    child = pack / "results"
    for path in (pack, lite, sibling, child):
        path.mkdir(parents=True)
    refuse_compare_output(sibling, forbidden=(pack, lite))
    with pytest.raises(SystemExit):
        refuse_compare_output(pack, forbidden=(pack, lite))
    with pytest.raises(SystemExit):
        refuse_compare_output(child, forbidden=(pack, lite))
    with pytest.raises(SystemExit):
        refuse_compare_output(lite, forbidden=(pack, lite))
    with pytest.raises(SystemExit):
        refuse_compare_output(tmp_path, forbidden=(pack, lite))


def test_baseline_stamp_stops_when_the_finished_raw_changes(tmp_path: Path) -> None:
    baseline = tmp_path / "v2"
    output = tmp_path / "compare"
    for name in (
        "observations/raw.jsonl",
        "observations/SCORE_DONE.json",
        "results/t3_status.csv",
        "results/intervals_t3.csv",
    ):
        path = baseline / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("same\n", encoding="utf-8")
    require_finished_baseline(baseline, expected=baseline)
    remember_baseline(output, baseline)
    stamped = baseline_stamp(baseline)
    assert stamped["observations/raw.jsonl"]["size"] == 5
    with (baseline / "observations" / "raw.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("more\n")
    with pytest.raises(SystemExit, match="raw.jsonl"):
        remember_baseline(output, baseline)


def test_t3_only_products_skip_the_paired_file(tmp_path: Path) -> None:
    plan = {
        "anchors": [
            {
                "anchor_id": "roof",
                "grid_id": "CPT2304",
                "grid_role": "development",
                "paired": "1",
                "census_date": "2025-01-31",
                "census_date_precision": "policy_census_bound",
            }
        ],
        "candidates": [{"anchor_id": "roof", "capture_date": "2019-02-28", "callable": "1"}],
    }
    raw_rows = [
        {
            "arm": "t3",
            "stage": "first_pass",
            "call_status": "ok",
            "verdict": "absent",
            "anchor_id": "roof",
            "capture_date": "2019-02-28",
            "n_images": 3,
            "model_version": COMPARE_ALIAS,
            "candidate_sha256": "candidate",
            "aerial_sha256": "aerial",
            "satellite_sha256": "satellite",
            "satellite_date": "2025-03-30",
        }
    ]
    written = write_products(tmp_path, plan, raw_rows, write_paired=False)
    assert written["n_paired"] == 0
    assert (tmp_path / "results" / "t3_status.csv").is_file()
    assert not (tmp_path / "results" / "paired_comparison.csv").exists()


def test_status_agreement_excludes_hash_mismatches_and_is_not_accuracy() -> None:
    baseline = [
        _status("a", "2019-02-28", "absent", "development"),
        _status("b", "2019-03-30", "present", "evaluation", candidate="other"),
    ]
    compare = [
        _status("a", "2019-02-28", "present", "development", model=COMPARE_ALIAS),
        _status("b", "2019-03-30", "present", "evaluation", candidate="other-2", model=COMPARE_ALIAS),
    ]
    rows, summary = compare_status_rows(baseline, compare)
    assert summary["agreement_is_accuracy"] == 0
    assert summary["n_same_images"] == 1
    assert summary["n_hash_mismatch"] == 1
    assert summary["n_different_status_same_images"] == 1
    assert summary["n_same_status_same_images"] == 0
    assert summary["crosstab_same_images"] == {"absent|present": 1}
    assert all(row["agreement_is_accuracy"] == 0 for row in rows)
    intervals, interval_summary = compare_interval_rows(
        [{"anchor_id": "a", "grid_role": "development", "status": "bounded", "labels_modified": "0", "interval_start": "2019-02-28", "interval_end": "2019-03-30"}],
        [{"anchor_id": "a", "grid_role": "development", "status": "conflict_pending_review", "labels_modified": "0", "interval_start": "", "interval_end": ""}],
    )
    assert interval_summary["n_different_status"] == 1
    assert interval_summary["n_labels_modified"] == 0
    assert interval_summary["agreement_is_accuracy"] == 0
    assert intervals[0]["status_35"] == "conflict_pending_review"


def test_short_comparison_does_not_write_a_finished_summary(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    compare = tmp_path / "compare"
    for root in (baseline, compare):
        (root / "results").mkdir(parents=True)
        (root / "results" / "t3_status.csv").write_text("anchor_id,capture_date,t3_status\n", encoding="utf-8")
        (root / "results" / "intervals_t3.csv").write_text("anchor_id,status,labels_modified\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="callable dates"):
        write_model_comparison(baseline, compare)
    assert not (compare / "results" / "compare_summary.json").exists()


def _status(
    anchor_id: str,
    day: str,
    status: str,
    role: str,
    *,
    candidate: str = "candidate",
    model: str = MODEL_ALIAS,
) -> dict[str, str]:
    return {
        "anchor_id": anchor_id,
        "grid_id": "CPT2304",
        "grid_role": role,
        "capture_date": day,
        "t3_status": status,
        "model_version": model,
        "n_images": "3",
        "candidate_sha256": candidate,
        "aerial_sha256": "aerial",
        "satellite_sha256": "satellite",
    }


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self.reason = "OK"
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def _body(model: str) -> _Response:
    label = {
        "candidate_index": 1,
        "pv_present": False,
        "quality_flag": "usable",
        "target_match": True,
        "confidence": 0.4,
        "evidence": "bare roof",
        "unknown_reason": "",
        "object_change_flag": False,
    }
    return _Response(
        {
            "modelVersion": model,
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(label)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8},
        }
    )


def test_scorer_posts_the_budget_alias_without_a_thinking_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.temporal.run_reference_fulltimeline_scan import RawLog

    captured: list[tuple[str, dict[str, object]]] = []

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured.append((url, kwargs))
        model = COMPARE_ALIAS if COMPARE_ALIAS in url else MODEL_ALIAS
        return _body(model)

    monkeypatch.setattr(gemini_http.requests, "post", fake_post)
    image = tmp_path / "chip.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    request = {
        "arm": "t3",
        "anchor_id": "anchor-a",
        "capture_date": "2020-01-01",
        "stage": "first_pass",
        "degradation": "",
        "candidate_sha256": "abc",
        "aerial_sha256": "def",
        "satellite_sha256": "ghi",
        "prompt": "three images",
        "image_paths": [str(image), str(image), str(image)],
        "image_roles": ["candidate", "aerial_reference", "satellite_reference"],
        "grid_id": "CPT0000",
    }
    budget = {
        "gross_safe_budget": 100,
        "work_budget": 50,
        "warning_budget": 10,
        "max_output_tokens": 4096,
        "model_alias": COMPARE_ALIAS,
        "thinking_level": "",
    }
    scorer = Scorer(tmp_path, RawLog(tmp_path / "observations" / "raw.jsonl"), budget, window_id="compare-test")
    assert scorer.issue(request) == "ok"
    url, kwargs = captured[-1]
    assert url.endswith(f"/models/{COMPARE_ALIAS}:generateContent")
    posted = json.loads(str(kwargs["data"]))
    assert "thinkingConfig" not in posted["generationConfig"]
    assert posted["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" in posted["generationConfig"]
    assert scorer.raw.rows[-1]["model_version"] == COMPARE_ALIAS
