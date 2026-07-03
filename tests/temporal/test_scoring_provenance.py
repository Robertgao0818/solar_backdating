"""ISSUE-06 scoring-provenance sidecar tests (Phase 1 — Provenance).

The sidecar records, for every chip that flows through the PresenceScorer seam,
*which model / instruction / pixels* produced its verdict. These tests pin the
five acceptance criteria of ISSUE-06:

  AC1  every seam call emits one row per scored chip;
  AC2  scorer identity comes from the resolved config object (not re-read from
       env), for both the cheap and capable model tiers;
  AC3  the prompt/config hash is stable across runs with an identical config and
       moves when the prompt (or model) changes;
  AC4  the chip content hash is computed on the encoded bytes and is identical
       for an identical chip across recorder instances;
  AC5  the historical-backfill limitation is documented where consumers see it.

Unit layer (items 1-5): the hash primitives and the ProvenanceRecordingScorer
proxy in isolation. Integration layer (items 6-9): the five migrated call-sites.
No network: the Gemini implementation is never imported — fakes stand in for the
raw ``.batch`` / ``.sequence`` / ``.matrix`` callables the same way the existing
seam tests do.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.temporal.presence_scorer import (
    DryRunPresenceScorer,
    GeminiPresenceScorer,
    Pick,
    PresenceObservation,
)
from scripts.temporal.scoring_provenance import (
    SCORING_PROVENANCE_FIELDS,
    ChipHasher,
    canonical_hash,
    jsonl_writer,
    with_scoring_provenance,
)
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    GeminiClientConfig,
    GeminiMatrixObservation,
    GeminiObservation,
    GeminiSequenceObservation,
    GeminiSequenceResult,
    MatrixDatePick,
    SequenceDatePick,
)


# ---------------------------------------------------------------------------
# 1. canonical_hash — deterministic and key-order independent
# ---------------------------------------------------------------------------


def test_canonical_hash_is_deterministic_and_key_order_independent() -> None:
    a = canonical_hash({"model": "gemini-3-flash", "mode": "batch", "n": 3})
    b = canonical_hash({"n": 3, "mode": "batch", "model": "gemini-3-flash"})
    assert a == b
    assert a.startswith("sha256:")
    # Nested payloads with dict/list members hash stably regardless of key order.
    p1 = {"templates": ["x", "y"], "cfg": {"model": "m", "fmt": "native"}}
    p2 = {"cfg": {"fmt": "native", "model": "m"}, "templates": ["x", "y"]}
    assert canonical_hash(p1) == canonical_hash(p2)
    # A changed value changes the digest.
    assert canonical_hash(p1) != canonical_hash({"templates": ["x", "z"], "cfg": {"model": "m", "fmt": "native"}})


def test_canonical_hash_tolerates_non_json_native_values() -> None:
    # default=str keeps hashing total for stray objects (e.g. Path, date).
    assert canonical_hash({"p": Path("/tmp/x")}) == canonical_hash({"p": Path("/tmp/x")})


# ---------------------------------------------------------------------------
# 2. ChipHasher — content hash on encoded bytes (AC4)
# ---------------------------------------------------------------------------


def test_chip_hash_equals_sha256_of_file_bytes(tmp_path: Path) -> None:
    chip = tmp_path / "chip.png"
    payload = b"\x89PNG fake bytes of the scored review asset"
    chip.write_bytes(payload)

    digest, error = ChipHasher().sha256(str(chip))
    assert error is None
    assert digest == hashlib.sha256(payload).hexdigest()


def test_chip_hash_identical_across_recorder_instances(tmp_path: Path) -> None:
    chip = tmp_path / "chip.png"
    chip.write_bytes(b"identical content")
    d1, _ = ChipHasher().sha256(str(chip))
    d2, _ = ChipHasher().sha256(str(chip))
    assert d1 == d2 == hashlib.sha256(b"identical content").hexdigest()


def test_chip_hash_missing_and_empty_path_returns_none_plus_error(tmp_path: Path) -> None:
    d_empty, e_empty = ChipHasher().sha256("")
    assert d_empty is None and e_empty

    d_missing, e_missing = ChipHasher().sha256(str(tmp_path / "nope.png"))
    assert d_missing is None and e_missing

    d_none, e_none = ChipHasher().sha256(None)
    assert d_none is None and e_none


# ---------------------------------------------------------------------------
# 3. prompt/config hash stability + sensitivity (AC3)
# ---------------------------------------------------------------------------


def _config(**overrides: object) -> GeminiClientConfig:
    base = {"base_url": "https://stub.example", "api_key": "stub", "model": "gemini-3-flash-preview"}
    base.update(overrides)
    return GeminiClientConfig(**base)  # type: ignore[arg-type]


def test_prompt_config_hash_stable_across_scorer_instances_with_equal_config() -> None:
    cfg = _config()
    h1 = canonical_hash(GeminiPresenceScorer().prompt_config_fingerprint("batch", cfg))
    h2 = canonical_hash(GeminiPresenceScorer().prompt_config_fingerprint("batch", cfg))
    assert h1 == h2


def test_prompt_config_hash_changes_when_prompt_template_changes(monkeypatch) -> None:
    """AC3 acceptance: mutate BATCH_PROMPT_TEMPLATE and the batch fingerprint moves."""
    import scripts.validation.gemini_solar_image_review as gsir

    cfg = _config()
    before = canonical_hash(GeminiPresenceScorer().prompt_config_fingerprint("batch", cfg))
    monkeypatch.setattr(gsir, "BATCH_PROMPT_TEMPLATE", "TOTALLY DIFFERENT PROMPT N={count}")
    after = canonical_hash(GeminiPresenceScorer().prompt_config_fingerprint("batch", cfg))
    assert before != after


def test_prompt_config_hash_changes_when_model_differs() -> None:
    fp = GeminiPresenceScorer()
    h_cheap = canonical_hash(fp.prompt_config_fingerprint("batch", _config(model="cheap-model")))
    h_capable = canonical_hash(fp.prompt_config_fingerprint("batch", _config(model="capable-model")))
    assert h_cheap != h_capable


def test_prompt_config_fingerprint_never_contains_api_key() -> None:
    cfg = _config(api_key="super-secret-key")
    for mode in ("batch", "sequence", "matrix"):
        payload = GeminiPresenceScorer().prompt_config_fingerprint(mode, cfg)
        assert "super-secret-key" not in json.dumps(payload, default=str)
        assert "api_key" not in payload
        assert "base_url" not in payload


def test_batch_prompt_render_is_byte_identical_after_suffix_hoist() -> None:
    """Regression lock (ISSUE-06 §gemini surgical): hoisting the census-calibration
    suffix into BATCH_CENSUS_CALIBRATION_SUFFIX must leave the rendered batch
    prompt byte-for-byte unchanged for a fixed picks list. The expected suffix is
    the literal pre-hoist text with {ref_idx}=2 / {ref_date}=2024-03-15."""
    from scripts.validation.gemini_solar_image_review import (
        BATCH_PROMPT_TEMPLATE,
        _build_batch_prompt,
    )

    picks = [
        BatchPick(chip_index=1, chip_path=Path("/x/a.png"), capture_date="2018-06-01", version=100),
        BatchPick(chip_index=2, chip_path=Path("/x/b.png"), capture_date="2024-03-15", version=200),
    ]
    rendered = _build_batch_prompt(picks, census_mid_date_iso="2024-01-01")

    expected_suffix = (
        "\nCALIBRATION: chip 2 (capture_date 2024-03-15) is the most recent\n"
        "imagery in this batch and is from the census period. Each anchor is a known\n"
        "PV installation per the higher-level ground truth, so chip 2 should\n"
        "show PV at the yellow ring marker. Use it to calibrate panel appearance for\n"
        "this exact roof: same building, same roof material, same orientation. If chip\n"
        "2 clearly shows PV at the marker, label it pv_present=true; if you\n"
        "cannot see PV at the marker in chip 2, that means the marker is not\n"
        "well-positioned for this anchor and you should label that chip\n"
        "quality_flag='ambiguous' rather than absent. Do NOT propagate the GT-prior to\n"
        "other chips — score each older chip on its own visual evidence at the marker,\n"
        "using chip 2 only as appearance-calibration reference.\n"
    )
    assert rendered == BATCH_PROMPT_TEMPLATE.format(count=2) + expected_suffix
    # No census chip -> no suffix, prompt is exactly the base template.
    no_census = _build_batch_prompt(picks[:1], census_mid_date_iso=None)
    assert no_census == BATCH_PROMPT_TEMPLATE.format(count=1)


def test_dry_run_fingerprint_is_self_describing() -> None:
    from datetime import date

    scorer = DryRunPresenceScorer(label="appears_2020", install_date=date(2020, 1, 1))
    payload = scorer.prompt_config_fingerprint("batch", None)
    assert payload["scorer"] == "dry_run"
    assert payload["label"] == "appears_2020"


# ---------------------------------------------------------------------------
# Fakes: gemini-shaped raw callables (never touch the network)
# ---------------------------------------------------------------------------


class _FakeGeminiScorer:
    """Structurally a PresenceScorer with gemini-shaped ``.batch`` / ``.sequence``
    / ``.matrix`` raw callables. Records the kwargs its inner callables receive so
    a test can prove ``provenance_context`` is stripped before delegation."""

    name = "gemini"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable"})
    decision_sources = frozenset({"gemini_batch", "gemini_sequence", "gemini_matrix"})

    def __init__(self) -> None:
        self.batch_kwargs: list[dict] = []
        self.sequence_kwargs: list[dict] = []
        self.matrix_kwargs: list[dict] = []
        self.sequence_result: GeminiSequenceResult | None = None
        self.matrix_result: list[GeminiMatrixObservation] | None = None

    def prompt_config_fingerprint(self, mode: str, config) -> dict:
        return {"scorer": "gemini", "mode": mode, "model": getattr(config, "model", None)}

    @property
    def batch(self):
        def _batch(picks, *, config, audit_writer=None, census_mid_date_iso=None, routing_salt=None, **kw):
            assert "provenance_context" not in kw
            self.batch_kwargs.append({"config": config, "audit_writer": audit_writer, **kw})
            return [
                GeminiObservation(
                    chip_index=p.chip_index, pv_present=True, confidence=0.9,
                    quality_flag="usable", evidence="e", notes="n",
                    decision_source="gemini_batch",
                )
                for p in picks
            ]

        return _batch

    @property
    def sequence(self):
        def _sequence(picks, *, config, audit_writer=None, max_tokens=None, routing_salt=None, **kw):
            assert "provenance_context" not in kw
            self.sequence_kwargs.append({"config": config, "max_tokens": max_tokens, **kw})
            return self.sequence_result

        return _sequence

    @property
    def matrix(self):
        def _matrix(date_picks, targets, *, config, audit_writer=None, max_dates=5, **kw):
            assert "provenance_context" not in kw
            self.matrix_kwargs.append({"config": config, "max_dates": max_dates, **kw})
            return self.matrix_result

        return _matrix

    def score(self, picks, *, config, **kw):
        assert "provenance_context" not in kw
        return [
            PresenceObservation(
                pv_present=True, pv_score=0.9, quality_flag="usable",
                decision_source="gemini_batch", index=p.index, capture_date=p.capture_date,
            )
            for p in picks
        ]


def _batch_picks(tmp_path: Path, n: int) -> list[BatchPick]:
    picks = []
    for i in range(1, n + 1):
        chip = tmp_path / f"chip_{i}.png"
        chip.write_bytes(f"chip bytes {i}".encode())
        picks.append(BatchPick(chip_index=i, chip_path=chip, capture_date=f"20{18 + i}-01-01", version=100 + i))
    return picks


# ---------------------------------------------------------------------------
# 4. Wrapper .batch — one row per pick, identity from the call-time config (AC1/AC2)
# ---------------------------------------------------------------------------


def test_wrapper_batch_emits_one_row_per_pick_with_call_time_identity(tmp_path: Path) -> None:
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    wrapped = with_scoring_provenance(inner, rows.append, context={"anchor_id": "A1"})
    picks = _batch_picks(tmp_path, 3)

    cfg = _config(model="capable-model", api_format="native")
    result = wrapped.batch(picks, config=cfg, audit_writer=None)

    # Result is returned unchanged (same objects, gemini-shaped observations).
    assert [o.chip_index for o in result] == [1, 2, 3]
    # AC1: one sidecar row per scored chip.
    assert len(rows) == 3
    # AC2: identity resolved from the config passed at call time, not env.
    assert {r["model_id"] for r in rows} == {"capable-model"}
    assert {r["api_format"] for r in rows} == {"native"}
    assert {r["scorer_name"] for r in rows} == {"gemini"}
    assert {r["scoring_mode"] for r in rows} == {"batch"}
    assert {r["anchor_id"] for r in rows} == {"A1"}
    # Chip fields map back to the scored pick; chip hash is on the scored bytes.
    row1 = next(r for r in rows if r["chip_index"] == 1)
    assert row1["capture_date"] == "2019-01-01"
    assert row1["chip_sha256"] == hashlib.sha256(b"chip bytes 1").hexdigest()
    assert row1["decision_source"] == "gemini_batch"
    assert row1["quality_flag"] == "usable"
    assert set(row1) == set(SCORING_PROVENANCE_FIELDS)


def test_wrapper_batch_records_both_model_tiers(tmp_path: Path) -> None:
    """AC2 acceptance: two calls with the cheap and capable configs record the
    respective model — the identity tracks the config object per call."""
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    wrapped = with_scoring_provenance(inner, rows.append)
    picks = _batch_picks(tmp_path, 2)

    wrapped.batch(picks, config=_config(model="cheap-model"), audit_writer=None)
    wrapped.batch(picks, config=_config(model="capable-model"), audit_writer=None)

    assert {r["model_id"] for r in rows if r["chip_index"]} == {"cheap-model", "capable-model"}
    # And the prompt/config hash differs between the two tiers.
    cheap_hash = {r["prompt_config_hash"] for r in rows[:2]}
    capable_hash = {r["prompt_config_hash"] for r in rows[2:]}
    assert cheap_hash.isdisjoint(capable_hash)
    # Inner never saw provenance_context (asserted inside the fake), and the two
    # calls delegated the config unchanged.
    assert [k["config"].model for k in inner.batch_kwargs] == ["cheap-model", "capable-model"]


# ---------------------------------------------------------------------------
# 5. Wrapper .sequence / .matrix / .score — shape mapping + result identity
# ---------------------------------------------------------------------------


def _seq_result() -> GeminiSequenceResult:
    obs = [
        GeminiSequenceObservation(date_index=1, capture_date="2019-01-01", pv_present=False, pv_score=0.1, evidence="", notes=""),
        GeminiSequenceObservation(date_index=2, capture_date="2020-01-01", pv_present=True, pv_score=0.9, evidence="", notes=""),
    ]
    return GeminiSequenceResult(
        sequence_pattern="0-1", first_present_date="2020-01-01", first_present_date_index=2,
        confidence=0.9, consistency_flag="monotonic", quality_flag="usable",
        review_notes="", observations=obs, decision_source="gemini_sequence",
    )


def test_wrapper_sequence_emits_one_row_per_pick_and_returns_unchanged(tmp_path: Path) -> None:
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    inner.sequence_result = _seq_result()
    wrapped = with_scoring_provenance(inner, rows.append)

    picks = [
        SequenceDatePick(date_index=1, chip_path=tmp_path / "a.png", capture_date="2019-01-01", version="tm"),
        SequenceDatePick(date_index=2, chip_path=tmp_path / "b.png", capture_date="2020-01-01", version="wb"),
    ]
    (tmp_path / "a.png").write_bytes(b"a")
    (tmp_path / "b.png").write_bytes(b"b")

    result = wrapped.sequence(
        picks, config=_config(model="m"), audit_writer=None, max_tokens=8192,
        provenance_context={"anchor_id": "AX", "chip_id": "C1", "target_label": "T01"},
    )

    assert result is inner.sequence_result  # returned unchanged (identity)
    assert len(rows) == 2
    assert {r["scoring_mode"] for r in rows} == {"sequence"}
    assert {r["decision_source"] for r in rows} == {"gemini_sequence"}
    assert {r["quality_flag"] for r in rows} == {"usable"}
    assert {r["anchor_id"] for r in rows} == {"AX"}
    assert {r["chip_id"] for r in rows} == {"C1"}
    assert {r["target_label"] for r in rows} == {"T01"}
    # chip_index carries the date_index; capture_date/version from the pick.
    by_idx = {r["chip_index"]: r for r in rows}
    assert by_idx[2]["capture_date"] == "2020-01-01"
    assert by_idx[2]["version"] == "wb"


def _matrix_obs() -> list[GeminiMatrixObservation]:
    return [
        GeminiMatrixObservation(
            cell_index=1, date_index=1, capture_date="2019-01-01", target_id="tid_a",
            target_label="T01", pv_present=True, confidence=0.9, quality_flag="usable",
            evidence="", notes="", decision_source="gemini_matrix",
        ),
        GeminiMatrixObservation(
            cell_index=2, date_index=2, capture_date="2020-01-01", target_id="tid_a",
            target_label="T01", pv_present=True, confidence=0.9, quality_flag="usable",
            evidence="", notes="", decision_source="gemini_matrix",
        ),
    ]


def test_wrapper_matrix_maps_targets_and_dates_and_returns_unchanged(tmp_path: Path) -> None:
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    inner.matrix_result = _matrix_obs()
    wrapped = with_scoring_provenance(inner, rows.append, context={"chip_id": "chip_001"})

    (tmp_path / "d1.png").write_bytes(b"d1")
    (tmp_path / "d2.png").write_bytes(b"d2")
    date_picks = [
        MatrixDatePick(date_index=1, chip_path=tmp_path / "d1.png", capture_date="2019-01-01", version="v1"),
        MatrixDatePick(date_index=2, chip_path=tmp_path / "d2.png", capture_date="2020-01-01", version="v2"),
    ]
    targets = object()  # opaque to the wrapper; forwarded verbatim

    result = wrapped.matrix(date_picks, targets, config=_config(model="m"), audit_writer=None)

    assert result is inner.matrix_result
    assert len(rows) == 2
    assert {r["scoring_mode"] for r in rows} == {"matrix"}
    assert {r["chip_id"] for r in rows} == {"chip_001"}
    # target_id/target_label come from the observation, not the wrap-time context.
    assert {r["target_id"] for r in rows} == {"tid_a"}
    assert {r["target_label"] for r in rows} == {"T01"}
    by_idx = {r["chip_index"]: r for r in rows}
    assert by_idx[1]["chip_sha256"] == hashlib.sha256(b"d1").hexdigest()
    assert by_idx[2]["version"] == "v2"


def test_wrapper_score_batch_path_emits_rows_and_returns_unchanged() -> None:
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    wrapped = with_scoring_provenance(inner, rows.append, context={"anchor_id": "A9"})
    picks = [
        Pick(chip_path="", capture_date="2019-01-01", version=1, index=1),
        Pick(chip_path="", capture_date="2020-01-01", version=2, index=2),
    ]
    result = wrapped.score(picks, config=None)

    assert [o.index for o in result] == [1, 2]
    assert len(rows) == 2
    assert {r["scoring_mode"] for r in rows} == {"batch"}
    assert {r["anchor_id"] for r in rows} == {"A9"}
    # config=None -> model_id/api_format are honestly None (dry-run-like path).
    assert {r["model_id"] for r in rows} == {None}
    # empty chip_path -> chip hash unavailable, recorded in-row, never raised.
    assert {r["chip_sha256"] for r in rows} == {None}
    assert all(r["chip_sha256_error"] for r in rows)


def test_wrapper_delegates_declared_attributes_and_passthrough() -> None:
    inner = _FakeGeminiScorer()
    wrapped = with_scoring_provenance(inner, lambda _r: None)
    assert wrapped.name == "gemini"
    assert wrapped.failure_decision_sources == frozenset({"gemini_failed"})
    assert wrapped.quality_flags == inner.quality_flags
    assert wrapped.decision_sources == inner.decision_sources


def test_jsonl_writer_appends_one_line_per_record(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "scoring_provenance.jsonl"
    write = jsonl_writer(path)
    write({"a": 1, "context": {"rep": 2}})
    write({"a": 2, "context": {}})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["a"] == 1
    assert json.loads(lines[1])["context"] == {}


def test_backfill_limitation_documented_in_both_required_places() -> None:
    """AC5: 'historical scans cannot be backfilled ... stated where downstream
    consumers will see it.' The spec requires the statement in BOTH the module
    docstring and docs/resolution_provenance.md. Lock both so neither can be
    quietly deleted."""
    import scripts.temporal.scoring_provenance as sp_module

    module_doc = (sp_module.__doc__ or "").lower()
    assert "cannot be backfilled" in module_doc
    assert "issue-07" in module_doc

    repo_root = Path(__file__).resolve().parents[2]
    doc_text = (repo_root / "docs" / "resolution_provenance.md").read_text(encoding="utf-8").lower()
    assert "scoring provenance sidecar (issue-06" in doc_text
    assert "cannot be backfilled" in doc_text


def test_wrapper_free_form_context_lands_in_context_blob() -> None:
    """The fullstack call-site passes rep / window_idx per call; those non-column
    keys must land in the free-form ``context`` blob, not be dropped."""
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    inner.sequence_result = _seq_result()
    wrapped = with_scoring_provenance(inner, rows.append)
    picks = [SequenceDatePick(date_index=1, chip_path="", capture_date="2019-01-01")]
    wrapped.sequence(
        picks, config=_config(), audit_writer=None, max_tokens=None,
        provenance_context={"anchor_id": "A", "chip_id": "C", "target_label": "T01", "rep": 3, "window_idx": 2},
    )
    assert rows[0]["anchor_id"] == "A"
    assert rows[0]["context"] == {"rep": 3, "window_idx": 2}


# ===========================================================================
# Integration (items 6-9): the five migrated call-sites.
# ===========================================================================


# ---------------------------------------------------------------------------
# 6. Adaptive scan, dry-run: rows emitted, scan_state byte-identical (AC1).
# ---------------------------------------------------------------------------


def test_adaptive_dry_run_emits_rows_and_preserves_scan_state(tmp_path: Path, monkeypatch) -> None:
    from scripts.temporal import scan_state as scan_state_mod
    from scripts.temporal.run_adaptive_scan import run_one_anchor
    from scripts.temporal.scan_config import AdaptiveScanConfig

    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2020-01-01T00:00:00Z")
    anchor = {"anchor_id": "prov_dry_anchor", "region_key": "johannesburg", "grid_id": "G1"}
    config = AdaptiveScanConfig()

    def _run(dirname: str, writer):
        d = tmp_path / dirname
        state = run_one_anchor(
            anchor, config, d,
            dry_run=True, force_restart=True,
            chips_dir=tmp_path / "chips", audit_dir=tmp_path / "audit",
            census_mid_date_iso=None,
            scoring_provenance_writer=writer,
        )
        return state, (d / "prov_dry_anchor.json").read_bytes()

    _state_off, bytes_off = _run("off", None)
    rows: list[dict] = []
    state_on, bytes_on = _run("on", rows.append)

    # Scan-state JSON is byte-identical whether or not the sidecar writer is attached.
    assert bytes_off == bytes_on
    # AC1: one sidecar row per scored chip (dry-run scores every pick — no downloads fail).
    n_scored = sum(len(rnd.results) for rnd in state_on.rounds)
    assert n_scored > 0
    assert len(rows) == n_scored
    assert {r["scorer_name"] for r in rows} == {"dry_run"}
    assert {r["scoring_mode"] for r in rows} == {"batch"}
    assert {r["anchor_id"] for r in rows} == {"prov_dry_anchor"}
    assert {r["model_id"] for r in rows} == {None}  # dry-run has no model


# ---------------------------------------------------------------------------
# 7. Adaptive scan, real path fakes: identity from the resolved config, both tiers.
# ---------------------------------------------------------------------------


def test_adaptive_real_path_records_config_model_both_tiers(tmp_path: Path, monkeypatch) -> None:
    from tests.temporal.test_run_adaptive_scan_seam import _FakeScorer, _install_gehi_stubs
    from scripts.temporal.run_adaptive_scan import execute_round_real, run_one_anchor
    from scripts.temporal.scan_config import AdaptiveScanConfig
    from scripts.temporal.scan_decision import VintageEntry
    from scripts.temporal.scan_state import Pick, Round

    capable = _config(model="capable-model")
    cheap = _config(model="cheap-model")
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    _install_gehi_stubs(monkeypatch, tmp_path, vintages)

    # (a) run_one_anchor tiering reaches the sidecar: round 1 (initial) is the
    #     cheap tier, so the identity recorded is the cheap model — read from the
    #     config object the tiering selected, never from env.
    scorer = _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok")
    rows: list[dict] = []
    run_one_anchor(
        {"anchor_id": "prov_real_anchor", "region_key": "johannesburg", "grid_id": "G1"},
        AdaptiveScanConfig(), tmp_path / "scan_states",
        dry_run=False, force_restart=True,
        chips_dir=tmp_path / "chips", audit_dir=tmp_path / "audit",
        gemini_config=capable, gemini_config_round1=cheap,
        scorer=scorer, census_mid_date_iso=None,
        scoring_provenance_writer=rows.append,
    )
    assert rows
    assert {r["model_id"] for r in rows} == {"cheap-model"}
    assert all(r["model_id"] is not None for r in rows)  # from config, not env
    assert {r["scorer_name"] for r in rows} == {"fake_adaptive"}
    assert {r["anchor_id"] for r in rows} == {"prov_real_anchor"}

    # (b) the capable tier at the same production scoring function: execute_round_real
    #     with the capable config records the capable model.
    rows_cap: list[dict] = []
    wrapped = with_scoring_provenance(
        _FakeScorer(pv_present=True, quality_flag="usable", decision_source="stubscorer_ok"),
        rows_cap.append, context={"anchor_id": "cap"},
    )
    rnd = Round(
        round_id=2, round_type="bisection", window_start_date=None, window_end_date=None,
        picks=[Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20)],
    )
    execute_round_real(
        rnd, {"anchor_id": "cap", "region_key": "johannesburg"},
        AdaptiveScanConfig(gemini_max_dates_per_call=5),
        chips_dir=tmp_path / "chips_cap", audit_dir=tmp_path / "audit_cap",
        gemini_config=capable, scorer=wrapped, vintage_check=None, census_mid_date_iso=None,
    )
    assert rows_cap
    assert {r["model_id"] for r in rows_cap} == {"capable-model"}


# ---------------------------------------------------------------------------
# 8. Census-narrowing scan: rows next to output carry the anchor context.
# ---------------------------------------------------------------------------


def test_census_run_one_anchor_emits_sidecar_rows(tmp_path: Path, monkeypatch) -> None:
    from tests.temporal.test_run_census2023_scan_seam import (
        _FakeScorer,
        _cohort_row,
        _fake_state,
        _patch_io,
        _seq_result,
    )
    from tests.temporal.test_run_census2023_scan_seam import _config as _census_config
    from scripts.temporal.run_census2023_scan import CensusJob, RateLimiter, run_one_anchor

    _patch_io(monkeypatch, _fake_state())
    scorer = _FakeScorer(result=_seq_result())
    rows: list[dict] = []
    out = run_one_anchor(
        CensusJob(_cohort_row()),
        main_dir=tmp_path / "main", norecent_dir=tmp_path / "norecent",
        census_chips_dir=tmp_path / "chips", audit_dir=None, config=_census_config(),
        zoom_ladder=(19, 18), max_tokens=8192, routing_salt_mode="none",
        limiter=RateLimiter(0.0), scorer=scorer,
        scoring_provenance_writer=rows.append,
    )
    # The census result CSV row is unchanged (downstream invariance is its own test);
    # here we only assert the sidecar was populated with the sequence-scored frames.
    assert out["census_decision"] == "narrowed"
    assert rows
    assert {r["scoring_mode"] for r in rows} == {"sequence"}
    assert {r["anchor_id"] for r in rows} == {"census_anchor_01"}
    assert {r["decision_source"] for r in rows} == {"gemini_sequence"}


# ---------------------------------------------------------------------------
# 9. Library-level default-resolution branch (target-sequence + matrix + fullstack).
# ---------------------------------------------------------------------------


def test_target_sequence_default_branch_emits_provenance(tmp_path: Path, monkeypatch) -> None:
    from tests.temporal.test_score_target_sequence import _result, _review_pngs
    from scripts.temporal import presence_scorer as ps
    from scripts.temporal.score_target_sequence import score_target_sequences

    class _FakeSeqScorer:
        name = "gemini"
        failure_decision_sources = frozenset({"gemini_failed"})
        quality_flags = frozenset({"usable"})
        decision_sources = frozenset({"gemini_sequence"})

        @property
        def sequence(self):
            def _seq(date_picks, *, config, audit_writer=None, max_tokens=None, **kw):
                assert "provenance_context" not in kw
                return _result([False, True])

            return _seq

        def score(self, picks, *, config, **kw):  # pragma: no cover - unused
            return []

    monkeypatch.setattr(ps, "get_scorer", lambda name, **_k: _FakeSeqScorer())
    rows: list[dict] = []
    dates = ["2018-03-30", "2019-07-30"]
    score_target_sequences(
        review_pngs=_review_pngs(tmp_path, dates),
        dates=dates,
        config=_config(),
        scoring_provenance_writer=rows.append,
    )
    assert rows
    assert {r["scoring_mode"] for r in rows} == {"sequence"}
    assert {r["anchor_id"] for r in rows} == {"target_01"}
    assert {r["chip_id"] for r in rows} == {"chip_001"}
    assert {r["target_label"] for r in rows} == {"T01"}


def test_matrix_default_branch_emits_provenance(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from tests.temporal.test_score_chip_group_matrix import _artifacts, _targets
    from scripts.temporal import presence_scorer as ps
    from scripts.temporal.score_chip_group_matrix import score_chip_group_matrices

    class _FakeMatrixScorer:
        name = "gemini"
        failure_decision_sources = frozenset({"gemini_failed"})
        quality_flags = frozenset({"usable"})
        decision_sources = frozenset({"gemini_matrix"})

        def matrix(self, date_picks, targets, *, config, audit_writer=None, **kw):
            assert "provenance_context" not in kw
            out = []
            cell = 1
            for pick in date_picks:
                for target in targets:
                    out.append(
                        GeminiMatrixObservation(
                            cell_index=cell, date_index=pick.date_index,
                            capture_date=pick.capture_date, target_id=target.target_id,
                            target_label=target.target_label, pv_present=True, confidence=0.9,
                            quality_flag="usable", evidence="", notes="",
                            decision_source="gemini_matrix",
                        )
                    )
                    cell += 1
            return out

        def score(self, picks, *, config, **kw):  # pragma: no cover - unused
            return []

    ps.register_scorer("prov_matrix", lambda **_k: _FakeMatrixScorer())
    try:
        rows: list[dict] = []
        result_rows = score_chip_group_matrices(
            artifacts_by_chip={"chip_001": _artifacts(tmp_path, 2)},
            targets_by_chip={"chip_001": _targets(1)},
            config=_config(),
            scorer_name="prov_matrix",
            scoring_provenance_writer=rows.append,
        )
    finally:
        ps._SCORER_FACTORIES.pop("prov_matrix", None)

    assert result_rows  # the CSV rows still produced
    assert rows
    assert {r["scoring_mode"] for r in rows} == {"matrix"}
    assert {r["chip_id"] for r in rows} == {"chip_001"}
    assert {r["target_label"] for r in rows} == {"T01"}


def test_fullstack_style_direct_wrap_emits_provenance(tmp_path: Path) -> None:
    """fullstack_noscan_run wraps the resolved scorer in main() and passes a
    per-job provenance_context. This mirrors that shape directly (the wrapper is
    the unit exercised at that call-site)."""
    rows: list[dict] = []
    inner = _FakeGeminiScorer()
    inner.sequence_result = _seq_result()
    score_sequence = with_scoring_provenance(inner, rows.append).sequence
    picks = [SequenceDatePick(date_index=1, chip_path="", capture_date="2019-01-01")]
    score_sequence(
        picks, config=_config(model="m"), audit_writer=None, max_tokens=None,
        provenance_context={"anchor_id": "anc", "chip_id": "chip", "target_label": "T01", "rep": 1, "window_idx": 0},
    )
    assert rows
    assert rows[0]["scoring_mode"] == "sequence"
    assert rows[0]["anchor_id"] == "anc"
    assert rows[0]["context"] == {"rep": 1, "window_idx": 0}
