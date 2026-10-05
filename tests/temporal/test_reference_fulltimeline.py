"""Contract tests for the full-timeline reference pilot.

These call the shipped planning and interval functions. They do not mock those
functions and they do not call Gemini.
"""

from __future__ import annotations

import random

import pytest

from scripts.temporal.reference_fulltimeline import (
    MODEL_ALIAS,
    RESPONSE_SCHEMA,
    WINDOW_START,
    RawOverwriteError,
    admit_model_version,
    allocate_band_quotas,
    apply_manifest_gap_states,
    assemble_image_roles,
    audit_tables,
    build_candidates,
    build_conclusion,
    build_plan,
    cache_key,
    choose_satellite_reference,
    commit_raw_record,
    compare_old_interval,
    infer_interval,
    interpret_model_payload,
    outstanding_requests,
    pending_requests,
    plan_model_requests,
    plan_rechecks,
    remaining_attempts,
    replay_adaptive,
    request_fingerprint,
    select_grids,
    timeline_observations,
    visual_verdict,
)


def _grids() -> list[dict[str, object]]:
    rows = []
    for index in range(16):
        rows.append(
            {
                "grid_id": f"G{index:02d}",
                "n_anchors": 10 + index * 3,
                "median_in_window_dates": float(4 + (index % 5)),
                "n_a48": 0,
                "n_geometry_exception": 0,
                "n_missing_xy": 0,
                "n_no_history": 0,
                "new_verdict": "present" if index % 2 == 0 else "absent",
            }
        )
    rows.append(
        {
            "grid_id": "RISK1",
            "n_anchors": 12,
            "median_in_window_dates": 8.0,
            "n_a48": 10,
            "n_geometry_exception": 0,
            "n_missing_xy": 0,
            "n_no_history": 0,
        }
    )
    rows.append(
        {
            "grid_id": "RISK2",
            "n_anchors": 9,
            "median_in_window_dates": 3.0,
            "n_a48": 0,
            "n_geometry_exception": 2,
            "n_missing_xy": 0,
            "n_no_history": 0,
        }
    )
    rows.append(
        {
            "grid_id": "NOCAT",
            "n_anchors": 4,
            "median_in_window_dates": 0.0,
            "n_a48": 0,
            "n_geometry_exception": 0,
            "n_missing_xy": 0,
            "n_no_history": 4,
        }
    )
    return rows


def test_grid_selection_is_frozen_and_ignores_new_labels() -> None:
    first = select_grids(_grids(), seed=20261004)
    mutated = _grids()
    for row in mutated:
        row["new_verdict"] = "absent" if row.get("new_verdict") == "present" else "present"
    second = select_grids(mutated, seed=20261004)
    assert [row["grid_id"] for row in first["grids"]] == [row["grid_id"] for row in second["grids"]]
    assert len(first["grids"]) == 10
    roles = [row["role"] for row in first["grids"]]
    assert roles.count("development") == 2
    assert roles.count("evaluation") == 8
    assert {row["grid_id"] for row in first["out_of_scope"]} == {"NOCAT"}
    risk_roles = [row["role"] for row in first["grids"] if row["grid_id"] in {"RISK1", "RISK2"}]
    assert risk_roles
    assert set(risk_roles) == {"evaluation"}


def test_short_grid_keeps_every_anchor_and_small_band() -> None:
    anchors = [
        {"anchor_id": "s", "area_band": "<20"},
        {"anchor_id": "m", "area_band": "20-100"},
        {"anchor_id": "l", "area_band": ">=100"},
    ]
    flags = __import__(
        "scripts.temporal.reference_fulltimeline", fromlist=["sample_nested_review"]
    ).sample_nested_review(anchors, random.Random(20261004))
    assert sum(item["paired"] for item in flags.values()) == 3
    assert sum(item["human_review"] for item in flags.values()) == 3
    quotas = allocate_band_quotas({"<20": 1, "20-100": 30, ">=100": 40}, 20)
    assert quotas["<20"] == 1
    assert sum(quotas.values()) == 20


def test_pre_2019_dates_are_not_candidates() -> None:
    catalog = [
        {
            "anchor_id": "a",
            "capture_date": "2018-12-31",
            "provider": "TM",
            "requested_zoom": 19,
            "bbox_complete": 1,
            "reference_only": 0,
            "version": 1,
            "census_date": "2025-01-31",
        },
        {
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "provider": "Wayback",
            "requested_zoom": 19,
            "bbox_complete": 1,
            "reference_only": 0,
            "version": 2,
            "census_date": "2025-01-31",
        },
        {
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "provider": "TM",
            "requested_zoom": 19,
            "bbox_complete": 1,
            "reference_only": 0,
            "version": 3,
            "census_date": "2025-01-31",
        },
        {
            "anchor_id": "a",
            "capture_date": "2025-01-31",
            "provider": "TM",
            "requested_zoom": 19,
            "bbox_complete": 1,
            "reference_only": 0,
            "version": 4,
            "census_date": "2025-01-31",
        },
    ]
    built = build_candidates(catalog, census_by_anchor={"a": "2025-01-31"})
    assert built["ignored_pre_2019"] == 1
    assert [row["capture_date"] for row in built["candidates"]] == ["2019-01-01"]
    assert built["candidates"][0]["provider"] == "TM"
    assert all(row["capture_date"] >= WINDOW_START for row in built["candidates"])


def test_candidate_index_must_be_one() -> None:
    # The gateway enum type is a string. A numeric enum value is HTTP 400.
    # The decoded candidate_index must still be the integer 1.
    prop = RESPONSE_SCHEMA["properties"]["candidate_index"]
    assert prop["type"] == "INTEGER"
    assert prop["enum"] == ["1"]
    rejected = interpret_model_payload(
        {
            "candidate_index": 0,
            "pv_present": True,
            "quality_flag": "usable",
            "target_match": True,
            "confidence": 0.9,
            "evidence": "dark grid on the marked roof",
            "unknown_reason": "",
            "object_change_flag": False,
        }
    )
    assert rejected["ok"] is False
    assert rejected["reason"] == "candidate_index"
    assert rejected["verdict"] is None
    string_index = interpret_model_payload(
        {
            "candidate_index": "1",
            "pv_present": True,
            "quality_flag": "usable",
            "target_match": True,
            "confidence": 0.9,
            "evidence": "dark grid on the marked roof",
            "unknown_reason": "",
            "object_change_flag": False,
        }
    )
    assert string_index["ok"] is False
    assert string_index["reason"] == "candidate_index"


def test_unknown_and_unusable_are_not_absent() -> None:
    unknown = interpret_model_payload(
        {
            "candidate_index": 1,
            "pv_present": None,
            "quality_flag": "unusable",
            "target_match": None,
            "confidence": None,
            "evidence": "",
            "unknown_reason": "shadow",
            "object_change_flag": None,
        }
    )
    assert unknown["ok"] is True
    assert unknown["verdict"] == "unknown"
    illegal = interpret_model_payload(
        {
            "candidate_index": 1,
            "pv_present": False,
            "quality_flag": "unusable",
            "target_match": True,
            "confidence": 0.2,
            "evidence": "blurred roof",
            "unknown_reason": "",
            "object_change_flag": False,
        }
    )
    assert illegal["ok"] is False
    assert illegal["verdict"] is None
    assert visual_verdict("transport_failed", {"ok": True, "verdict": "absent"}) is None
    assert admit_model_version("gemini-3.8-flash", MODEL_ALIAS) is False
    assert admit_model_version(MODEL_ALIAS, MODEL_ALIAS) is True


def test_interval_rules_do_not_repair_or_invent_a_day() -> None:
    bounded = infer_interval(
        [
            {"capture_date": "2019-06-01", "verdict": "absent", "call_status": "ok", "terminal": 1},
            {"capture_date": "2020-06-01", "verdict": "present", "call_status": "ok", "terminal": 1},
        ],
        expected_dates=["2019-06-01", "2020-06-01"],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert bounded["status"] == "bounded"
    assert bounded["interval_start"] == "2019-06-01"
    assert bounded["interval_end"] == "2020-06-01"
    assert bounded["install_date"] == ""

    left = infer_interval(
        [
            {"capture_date": "2021-04-01", "verdict": "present", "call_status": "ok", "terminal": 1},
            {"capture_date": "2022-04-01", "verdict": "present", "call_status": "ok", "terminal": 1},
        ],
        expected_dates=["2021-04-01", "2022-04-01"],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert left["status"] == "left_censored"
    assert left["install_date"] == ""
    assert left["interval_start"] != WINDOW_START
    assert WINDOW_START not in {left["install_date"], left["interval_start"], left["interval_end"]}
    assert left["earliest_present_date"] == "2021-04-01"

    censored = infer_interval(
        [
            {"capture_date": "2019-02-01", "verdict": "absent", "call_status": "ok", "terminal": 1},
        ],
        expected_dates=["2019-02-01"],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert censored["status"] == "aerial_upper_bound"
    assert censored["install_date"] == ""
    assert censored["upper_bound_precision"] == "policy_census_bound"

    original = [
        {"capture_date": "2019-01-15", "verdict": "present", "call_status": "ok", "terminal": 1},
        {"capture_date": "2019-06-01", "verdict": "absent", "call_status": "ok", "terminal": 1},
        {"capture_date": "2020-01-01", "verdict": "present", "call_status": "ok", "terminal": 1},
    ]
    before = [dict(row) for row in original]
    conflict = infer_interval(
        original,
        expected_dates=[row["capture_date"] for row in original],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert conflict["status"] == "conflict_pending_review"
    assert conflict["labels_modified"] == 0
    assert original == before
    assert plan_rechecks(conflict, original) == ["2019-01-15", "2019-06-01"]
    assert len(plan_rechecks(conflict, original)) <= 4


def test_v2_roles_refuse_one_image_and_pre_census_reference() -> None:
    """T3 is three images, T2 is the same candidate and aerial, and gaps are not successes."""

    t3 = assemble_image_roles(
        arm="t3",
        candidate_sha256="cand",
        aerial_sha256="air",
        satellite_sha256="sat",
        degradation="",
        candidate_date="2019-03-30",
        satellite_date="2025-02-28",
    )
    assert [row["role"] for row in t3] == [
        "candidate",
        "aerial_reference",
        "satellite_reference",
    ]
    t2 = assemble_image_roles(
        arm="t2",
        candidate_sha256="cand",
        aerial_sha256="air",
        satellite_sha256="sat",
        degradation="",
        candidate_date="2019-03-30",
        satellite_date="2025-02-28",
    )
    assert [row["role"] for row in t2] == ["candidate", "aerial_reference"]
    assert [row["sha256"] for row in t2] == ["cand", "air"]
    for kwargs in (
        {
            "arm": "t3",
            "candidate_sha256": "cand",
            "aerial_sha256": None,
            "satellite_sha256": "sat",
            "degradation": "",
            "candidate_date": "2019-03-30",
            "satellite_date": "2025-02-28",
        },
        {
            "arm": "t3",
            "candidate_sha256": "cand",
            "aerial_sha256": "air",
            "satellite_sha256": None,
            "degradation": "",
            "candidate_date": "2019-03-30",
            "satellite_date": "2025-02-28",
        },
        {
            "arm": "t2",
            "candidate_sha256": "cand",
            "aerial_sha256": None,
            "satellite_sha256": None,
            "degradation": "candidate_only_reference_unavailable",
            "candidate_date": "2019-03-30",
            "satellite_date": None,
        },
    ):
        with pytest.raises(ValueError):
            assemble_image_roles(**kwargs)
    choice = choose_satellite_reference(
        "2019-06-01",
        [
            {
                "kind": "satellite",
                "rank": 1,
                "capture_date": "2024-05-30",
                "source": "pre_census_fallback",
                "provider": "TM",
            },
            {
                "kind": "satellite",
                "rank": 2,
                "capture_date": "2025-02-28",
                "source": "post_census",
                "provider": "TM",
            },
        ],
    )
    assert choice["reference"]["source"] == "post_census"
    assert choice["reference"]["capture_date"] == "2025-02-28"
    only_fallback = choose_satellite_reference(
        "2019-06-01",
        [
            {
                "kind": "satellite",
                "rank": 1,
                "capture_date": "2024-05-30",
                "source": "pre_census_fallback",
                "provider": "TM",
            }
        ],
    )
    assert only_fallback["reference"] is None
    white_ids = (
        "ct_full_inventory_2026_06_21_merged_t00055288",
        "ct_full_inventory_2026_06_21_merged_t00055299",
        "ct_full_inventory_2026_06_21_merged_t00055301",
        "ct_full_inventory_2026_06_21_merged_t00055303",
    )
    for anchor_id in white_ids:
        planned = plan_model_requests(
            [
                {
                    "anchor_id": anchor_id,
                    "capture_date": "2022-12-30",
                    "callable": 1,
                    "terminal_state": "",
                    "candidate_sha256": "white",
                    "aerial_sha256": "air",
                    "satellite_sha256": "sat",
                }
            ],
            paired_anchor_ids={anchor_id},
        )
        assert planned == []
        assert visual_verdict("ok", {"ok": True, "verdict": "present"}) != "pixel_uninformative"
    missing_aerial = plan_model_requests(
        [
            {
                "anchor_id": "roof",
                "capture_date": "2019-01-30",
                "callable": 1,
                "terminal_state": "",
                "candidate_sha256": "cand",
                "aerial_sha256": "",
                "satellite_sha256": "sat",
            }
        ],
        paired_anchor_ids={"roof"},
    )
    assert missing_aerial == []
    missing_satellite = plan_model_requests(
        [
            {
                "anchor_id": "roof",
                "capture_date": "2019-02-28",
                "callable": 1,
                "terminal_state": "",
                "candidate_sha256": "cand",
                "aerial_sha256": "air",
                "satellite_sha256": "",
            }
        ],
        paired_anchor_ids={"roof"},
    )
    assert [row["arm"] for row in missing_satellite] == ["t2"]
    assert missing_satellite[0]["candidate_sha256"] == "cand"
    assert missing_satellite[0]["aerial_sha256"] == "air"
    assert missing_satellite[0]["satellite_sha256"] == ""
    paired = plan_model_requests(
        [
            {
                "anchor_id": "roof2",
                "capture_date": "2019-03-30",
                "callable": 1,
                "terminal_state": "",
                "candidate_sha256": "cand",
                "aerial_sha256": "air",
                "satellite_sha256": "sat",
            }
        ],
        paired_anchor_ids={"roof2"},
    )
    assert [row["arm"] for row in paired] == ["t3", "t2"]
    assert paired[0]["candidate_sha256"] == paired[1]["candidate_sha256"] == "cand"
    assert paired[0]["aerial_sha256"] == paired[1]["aerial_sha256"] == "air"
    assert paired[1]["satellite_sha256"] == ""


def test_request_plan_scores_every_date_and_refuses_self_reference() -> None:
    manifest = []
    for index, verdict in enumerate(["present", "absent", "unknown", "present"]):
        manifest.append(
            {
                "anchor_id": "a",
                "capture_date": f"2019-0{index + 1}-01",
                "callable": 1,
                "terminal_state": "",
                "verdict": verdict,
                "degradation": "",
                "candidate_sha256": "cand",
                "aerial_sha256": "air",
                "satellite_sha256": "sat",
            }
        )
    requests = plan_model_requests(manifest, paired_anchor_ids={"a"})
    assert len(requests) == 8
    assert [row["arm"] for row in requests[:4]] == ["t3", "t2", "t3", "t2"]
    self_ref = choose_satellite_reference(
        "2020-01-01",
        [
            {
                "kind": "satellite",
                "rank": 1,
                "capture_date": "2020-01-01",
                "provider": "TM",
            }
        ],
    )
    assert self_ref["degradation"] == "self_reference_omitted"
    assert self_ref["reference"] is None
    roles = assemble_image_roles(
        arm="t3",
        candidate_sha256="cand",
        aerial_sha256="air",
        satellite_sha256="sat",
        degradation="",
        candidate_date="2019-01-01",
        satellite_date="2024-01-01",
    )
    assert [row["role"] for row in roles] == [
        "candidate",
        "aerial_reference",
        "satellite_reference",
    ]
    with pytest.raises(ValueError):
        assemble_image_roles(
            arm="t3",
            candidate_sha256="same",
            aerial_sha256="air",
            satellite_sha256="same",
            degradation="",
            candidate_date="2019-01-01",
            satellite_date="2024-01-01",
        )


def test_resume_does_not_overwrite_raw_success() -> None:
    existing: list[dict[str, object]] = []
    record = {
        "arm": "t3",
        "anchor_id": "a",
        "capture_date": "2019-01-01",
        "stage": "first_pass",
        "call_status": "ok",
        "verdict": "present",
        "fingerprint": "fp",
        "model_version": MODEL_ALIAS,
        "candidate_sha256": "cand",
    }
    assert commit_raw_record(existing, record) == "appended"
    snapshot = [dict(row) for row in existing]
    assert commit_raw_record(existing, record) == "skipped"
    assert existing == snapshot
    with pytest.raises(RawOverwriteError):
        commit_raw_record(existing, {**record, "verdict": "absent", "fingerprint": "other"})
    assert existing == snapshot
    assert pending_requests([record], existing) == []


def test_replay_skips_dates_the_full_plan_keeps() -> None:
    labels = []
    for month in range(1, 13):
        verdict = "absent" if month <= 4 else "present"
        labels.append(
            {
                "capture_date": f"2020-{month:02d}-01",
                "verdict": verdict,
                "call_status": "ok",
                "version": 1,
                "provider": "TM",
            }
        )
    labels.append(
        {
            "capture_date": "2018-06-01",
            "verdict": "absent",
            "call_status": "ok",
            "version": 1,
            "provider": "TM",
        }
    )
    replay = replay_adaptive(
        anchor_id="a",
        region_key="cape_town",
        grid_id="G01",
        census_date="2025-01-31",
        labels=labels,
    )
    assert "2018-06-01" in replay["excluded_pre_2019"]
    assert "2018-06-01" not in replay["picked_dates"]
    skipped = [day for day in replay["skipped_dates"].split("|") if day]
    assert skipped
    assert replay["reference_frames_used_as_evidence"] == 0
    manifest = [
        {
            "anchor_id": "a",
            "capture_date": row["capture_date"],
            "callable": 1,
            "terminal_state": "",
            "candidate_sha256": "cand",
            "aerial_sha256": "air",
            "satellite_sha256": "sat",
        }
        for row in labels
        if row["capture_date"] >= WINDOW_START
    ]
    planned = plan_model_requests(manifest, paired_anchor_ids=set())
    assert len(planned) == 12


def test_old_comparison_does_not_invent_top52_raw_and_refuses_accuracy() -> None:
    new_interval = {
        "last_absent_date": "2020-01-01",
        "earliest_present_date": "2020-06-01",
        "status": "bounded",
    }
    comparison = compare_old_interval(
        new_interval,
        {
            "latest_absent_date": "2019-01-15",
            "earliest_present_date": "2021-01-01",
            "install_date": "",
            "cohort": "top52",
        },
        raw_sequence_recovered=False,
        raw_dates=None,
    )
    assert comparison["old_raw_sequence"] == "not_recovered"
    assert comparison["invented_raw_sequence"] == 0
    assert comparison["old_raw_n_dates"] == ""
    assert comparison["narrower_is_accuracy"] == 0
    summary = {
        "n_grids": 10,
        "n_development_grids": 2,
        "n_evaluation_grids": 8,
        "N": 40,
        "H": 100,
        "H_pair": 20,
        "valid_response_rate": 1.0,
        "candidate_byte_agreement": 1.0,
        "what_changed": "新区间来自窗口内全部第一遍标签。",
        "install_year_effect": "年份界只报告括号端点的年份变化。",
        "undatable_targets": "冲突和只有 unknown 的目标未定年。",
        "accuracy_pass": False,
    }
    text = build_conclusion(summary)
    assert "不构成准确度通过" in text
    assert "更窄不等于更准" in text
    assert "不能把全部差异归因于三帧" in text
    assert "accuracy_pass=false" in text


def test_audit_rejects_the_forbidden_failures() -> None:
    manifest = [
        {"anchor_id": "a", "capture_date": "2019-01-01", "terminal_state": "scored"},
    ]
    raw = [
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "stage": "first_pass",
            "call_status": "ok",
            "verdict": "present",
            "quality_flag": "usable",
            "target_match": True,
            "model_version": MODEL_ALIAS,
            "reference_only": 0,
        }
    ]
    requests = [
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "candidate_sha256": "same",
        }
    ]
    good = audit_tables(
        manifest=manifest,
        raw_records=raw,
        intervals=[{"labels_modified": 0}],
        requests=requests,
    )
    assert good["ok"] is True
    assert good["accuracy_pass"] is False
    bad_raw = [
        {
            **raw[0],
            "verdict": "absent",
            "quality_flag": "unusable",
            "call_status": "ok",
        }
    ]
    bad = audit_tables(
        manifest=manifest,
        raw_records=bad_raw,
        intervals=[{"labels_modified": 0}],
        requests=requests,
    )
    assert bad["ok"] is False


def test_recheck_success_does_not_inflate_the_valid_response_rate() -> None:
    manifest = [
        {"anchor_id": "a", "capture_date": "2019-01-01", "terminal_state": "scored"},
        {"anchor_id": "a", "capture_date": "2019-06-01", "terminal_state": "scored"},
    ]
    requests = [
        {"arm": "t3", "anchor_id": "a", "capture_date": "2019-01-01", "candidate_sha256": "same"},
        {"arm": "t3", "anchor_id": "a", "capture_date": "2019-06-01", "candidate_sha256": "same"},
    ]
    raw = [
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "stage": "first_pass",
            "call_status": "ok",
            "verdict": "present",
            "quality_flag": "usable",
            "target_match": True,
            "model_version": MODEL_ALIAS,
            "reference_only": 0,
        },
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-06-01",
            "stage": "first_pass",
            "call_status": "transport_failed",
            "verdict": None,
            "model_version": MODEL_ALIAS,
            "reference_only": 0,
        },
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-01-01",
            "stage": "boundary_recheck",
            "call_status": "ok",
            "verdict": "present",
            "quality_flag": "usable",
            "target_match": True,
            "model_version": MODEL_ALIAS,
            "reference_only": 0,
        },
    ]
    audit = audit_tables(
        manifest=manifest,
        raw_records=raw,
        intervals=[{"labels_modified": 0}],
        requests=requests,
    )
    assert audit["valid_response_rate"] == 0.5
    assert audit["ok"] is False


def test_manifest_gap_stays_terminal_and_is_not_absent() -> None:
    candidates = [
        {"anchor_id": "a", "capture_date": "2019-06-01", "terminal_state": ""},
        {"anchor_id": "a", "capture_date": "2020-01-01", "terminal_state": ""},
        {"anchor_id": "a", "capture_date": "2019-03-01", "terminal_state": "not_downloadable"},
    ]
    manifest = [
        {"anchor_id": "a", "capture_date": "2019-06-01", "terminal_state": "quality_failed"},
        {"anchor_id": "a", "capture_date": "2020-01-01", "terminal_state": "scored"},
        {"anchor_id": "a", "capture_date": "2019-03-01", "terminal_state": "not_downloadable"},
    ]
    raw = [
        {
            "anchor_id": "a",
            "capture_date": "2020-01-01",
            "stage": "first_pass",
            "arm": "t3",
            "call_status": "ok",
            "verdict": "absent",
            "quality_flag": "usable",
            "target_match": True,
        }
    ]
    annotated = apply_manifest_gap_states(candidates, manifest)
    assert annotated[0]["terminal_state"] == "quality_failed"
    assert annotated[1]["terminal_state"] == ""
    assert annotated[2]["terminal_state"] == "not_downloadable"
    observations = timeline_observations(annotated, raw, anchor_id="a")
    by_date = {row["capture_date"]: row for row in observations}
    assert by_date["2019-06-01"]["terminal"] == 1
    assert by_date["2019-06-01"]["call_status"] == "quality_failed"
    assert by_date["2019-06-01"]["verdict"] == ""
    assert by_date["2020-01-01"]["verdict"] == "absent"
    interval = infer_interval(
        observations,
        expected_dates=[row["capture_date"] for row in observations],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert interval["status"] == "aerial_upper_bound"
    assert interval["n_absent"] == 1
    assert interval["n_unknown"] == 2
    assert interval["labels_modified"] == 0


def test_build_plan_records_real_counts_not_the_illustrative_example() -> None:
    grids = _grids()
    anchors = []
    catalog = []
    for grid in grids:
        if grid["grid_id"] == "NOCAT":
            continue
        for offset in range(3):
            anchor_id = f"{grid['grid_id']}-a{offset}"
            band_area = [10, 40, 120][offset]
            anchors.append(
                {
                    "anchor_id": anchor_id,
                    "grid_id": grid["grid_id"],
                    "area_m2": band_area,
                    "chip_arm": "A48" if grid["grid_id"].startswith("RISK") else "A24",
                    "census_date": "2025-01-31",
                    "region_key": "cape_town",
                }
            )
            for month in (1, 6):
                catalog.append(
                    {
                        "anchor_id": anchor_id,
                        "capture_date": f"2019-{month:02d}-15",
                        "provider": "TM",
                        "requested_zoom": 19,
                        "bbox_complete": 1,
                        "reference_only": 0,
                        "version": 1,
                        "census_date": "2025-01-31",
                    }
                )
    plan = build_plan(grids, anchors, catalog, seed=20261004)
    again = build_plan(grids, anchors, catalog, seed=20261004)
    assert plan["summary"] == again["summary"]
    assert plan["summary"]["n_grids"] == 10
    assert plan["summary"]["n_development_grids"] == 2
    assert plan["summary"]["n_evaluation_grids"] == 8
    assert plan["summary"]["N"] == 30
    assert plan["summary"]["illustrative_example_used_as_count"] == 0
    assert plan["summary"]["H"] != 3000 * 60
    assert plan["anchors"]
    assert all(row["history_reason"] == "ok" for row in plan["anchors"])
    review_inside_paired = all(
        not row["human_review"] or row["paired"] for row in plan["anchors"]
    )
    assert review_inside_paired


def test_manifest_shares_candidate_bytes_and_skips_the_same_date() -> None:
    from scripts.temporal.fulltimeline_manifest import assemble_manifest, scoring_requests

    anchors = [
        {
            "anchor_id": "a",
            "paired": "1",
            "census_date": "2025-01-31",
            "grid_id": "G",
            "grid_role": "evaluation",
        }
    ]
    candidates = [
        {
            "anchor_id": "a",
            "capture_date": "2019-01-15",
            "callable": 1,
            "terminal_state": "",
            "version": "1",
            "provider": "TM",
            "requested_zoom": "19",
            "download_choice_reason": "tm_complete",
        }
    ]
    references = [
        {"anchor_id": "a", "kind": "aerial", "capture_date": "2025-01-31", "rank": 1},
        {"anchor_id": "a", "kind": "satellite", "capture_date": "2019-01-15", "rank": 1, "provider": "TM"},
        {"anchor_id": "a", "kind": "satellite", "capture_date": "2025-06-01", "rank": 2, "provider": "TM"},
    ]
    render = [
        {
            "anchor_id": "a",
            "kind": "candidate",
            "capture_date": "2019-01-15",
            "sha256": "cand",
            "png_path": "c.png",
            "qa_pass": "1",
            "source_tif": "c.tif",
        },
        {
            "anchor_id": "a",
            "kind": "aerial",
            "capture_date": "2025-01-31",
            "sha256": "air",
            "png_path": "a.png",
            "qa_pass": "1",
            "source_tif": "a.tif",
        },
        {
            "anchor_id": "a",
            "kind": "satellite",
            "capture_date": "2019-01-15",
            "sha256": "same-day",
            "png_path": "s.png",
            "qa_pass": "1",
            "source_tif": "s.tif",
        },
        {
            "anchor_id": "a",
            "kind": "satellite",
            "capture_date": "2025-06-01",
            "sha256": "sat",
            "png_path": "s2.png",
            "qa_pass": "1",
            "source_tif": "s2.tif",
        },
    ]
    manifest = assemble_manifest(anchors, candidates, references, render, [])
    requests = scoring_requests(manifest, anchors)
    by_arm = {row["arm"]: row for row in requests}
    assert by_arm["t2"]["candidate_sha256"] == by_arm["t3"]["candidate_sha256"] == "cand"
    assert by_arm["t3"]["satellite_sha256"] == "sat"
    assert by_arm["t3"]["satellite_date"] == "2025-06-01"
    assert by_arm["t2"]["satellite_sha256"] == ""


def test_failed_calls_stay_in_the_response_denominator() -> None:
    manifest = [
        {
            "anchor_id": "a",
            "capture_date": "2019-06-01",
            "callable": 1,
            "terminal_state": "call_failed",
            "t3_degradation": "",
            "candidate_sha256": "img",
            "aerial_sha256": "air",
            "satellite_sha256": "sat",
        },
        {
            "anchor_id": "a",
            "capture_date": "2019-08-01",
            "callable": 1,
            "terminal_state": "quality_failed",
            "candidate_sha256": "",
        },
    ]
    planned = plan_model_requests(manifest, paired_anchor_ids=set())
    assert [(row["capture_date"], row["arm"]) for row in planned] == [("2019-06-01", "t3")]


def test_outstanding_requests_match_the_attempt_filter() -> None:
    planned = [
        {"arm": "t3", "anchor_id": "a", "capture_date": "2019-01-01", "stage": "first_pass"},
        {"arm": "t3", "anchor_id": "a", "capture_date": "2019-02-01", "stage": "first_pass"},
        {"arm": "t3", "anchor_id": "a", "capture_date": "2019-03-01", "stage": "first_pass"},
        {"arm": "t3", "anchor_id": "b", "capture_date": "2019-01-01", "stage": "first_pass"},
    ]
    raw = [
        {**planned[0], "call_status": "ok"},
        {**planned[1], "call_status": "transport_failed"},
        {**planned[1], "call_status": "transport_failed"},
        {**planned[1], "call_status": "invalid_response"},
        {**planned[2], "call_status": "transport_failed"},
    ]
    slow = [
        request
        for request in pending_requests(planned, raw)
        if remaining_attempts(raw, cache_key(request), limit=3) > 0
    ]
    assert outstanding_requests(planned, raw, limit=3) == slow
    assert [row["capture_date"] for row in slow] == ["2019-03-01", "2019-01-01"]
    assert [row["anchor_id"] for row in slow] == ["a", "b"]


def test_manifest_indexes_raw_without_changing_terminal_states() -> None:
    from scripts.temporal.fulltimeline_manifest import assemble_manifest

    anchors = [{"anchor_id": "a", "paired": "0", "census_date": "2025-01-31", "grid_id": "G", "grid_role": "evaluation"}]
    candidates = [
        {
            "anchor_id": "a",
            "capture_date": day,
            "callable": 1,
            "terminal_state": "",
            "version": "1",
            "provider": "TM",
            "requested_zoom": "19",
            "download_choice_reason": "tm_complete",
        }
        for day in ("2019-01-15", "2019-02-15")
    ]
    render = [
        {
            "anchor_id": "a",
            "kind": "candidate",
            "capture_date": day,
            "sha256": f"cand-{day}",
            "png_path": f"{day}.png",
            "qa_pass": "1",
            "source_tif": f"{day}.tif",
        }
        for day in ("2019-01-15", "2019-02-15")
    ]
    raw = [
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-01-15",
            "stage": "first_pass",
            "call_status": "ok",
        },
        *[
            {
                "arm": "t3",
                "anchor_id": "a",
                "capture_date": "2019-02-15",
                "stage": "first_pass",
                "call_status": "transport_failed",
            }
            for _ in range(3)
        ],
    ]
    manifest = assemble_manifest(anchors, candidates, [], render, raw)
    by_day = {row["capture_date"]: row["terminal_state"] for row in manifest}
    assert by_day == {"2019-01-15": "scored", "2019-02-15": "transport_failed"}


def test_remaining_attempts_stops_resume_from_looping() -> None:
    key = ("t3", "a", "2019-06-01", "first_pass")
    rows: list[dict[str, object]] = []
    assert remaining_attempts(rows, key, limit=3) == 3
    failure = {
        "arm": "t3",
        "anchor_id": "a",
        "capture_date": "2019-06-01",
        "stage": "first_pass",
        "call_status": "transport_failed",
    }
    rows.extend([dict(failure), dict(failure), dict(failure)])
    assert remaining_attempts(rows, key, limit=3) == 0
    rows.append(
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-06-01",
            "stage": "first_pass",
            "call_status": "ok",
            "fingerprint": "same",
            "model_version": MODEL_ALIAS,
            "candidate_sha256": "img",
        }
    )
    assert remaining_attempts(rows, key, limit=3) == 0


def test_timeline_gaps_are_not_absent_and_fingerprint_has_no_date() -> None:
    candidates = [
        {
            "anchor_id": "a",
            "capture_date": "2019-01-15",
            "terminal_state": "not_downloadable",
            "callable": 0,
            "version": "1",
            "provider": "TM",
        },
        {
            "anchor_id": "a",
            "capture_date": "2019-06-01",
            "terminal_state": "",
            "callable": 1,
            "version": "1",
            "provider": "TM",
        },
    ]
    raw = [
        {
            "arm": "t3",
            "anchor_id": "a",
            "capture_date": "2019-06-01",
            "stage": "first_pass",
            "call_status": "ok",
            "verdict": "present",
            "quality_flag": "usable",
            "target_match": True,
        }
    ]
    observations = timeline_observations(candidates, raw, anchor_id="a", arm="t3")
    interval = infer_interval(
        observations,
        expected_dates=["2019-01-15", "2019-06-01"],
        aerial_pv="present",
        aerial_date="2025-01-31",
        aerial_precision="policy_census_bound",
    )
    assert interval["n_present"] == 1
    assert interval["n_absent"] == 0
    assert interval["n_unknown"] == 1
    assert interval["status"] == "left_censored"
    assert interval["install_date"] == ""
    fingerprint = request_fingerprint(
        {
            "prompt_sha256": "abc",
            "model_alias": MODEL_ALIAS,
            "arm": "t3",
            "candidate_sha256": "img",
        }
    )
    assert "2019" not in fingerprint
    assert len(fingerprint) == 64
