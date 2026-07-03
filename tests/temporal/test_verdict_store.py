"""ISSUE-07 verdict store — content-addressed memoization at the PresenceScorer seam.

Covers the acceptance criteria of docs/replan_v2/ISSUE-07-verdict-store.md:

* key construction: pixel-hash keyed, identity/prompt/mode/instruction-extras
  sensitive, order-independent;
* `VerdictStore`: JSONL persistence, last-write-wins, compaction, corrupt-tail
  tolerance, single-writer enforcement (flock) + read-only opens;
* `VerdictCachingScorer`: per-chip caching on the batch/`score()` entrypoints
  (shared record shape), window-level caching on sequence/matrix, partial-miss
  scoring of only never-seen chips, failure verdicts never cached, cache-miss
  accounting counters;
* churn monitor: sentinel manifest build/check, mutated sentinel detected,
  churn rate + escalation class;
* replay gate: an adaptive-scan anchor re-run through the store produces
  byte-identical scan states with zero inner scorer calls; ISSUE-06 provenance
  rows still emitted on cache hits (wrap order).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.temporal import gehi_common as _gehi_common
from scripts.temporal import gehi_download as _gehi_download
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal import scan_state as scan_state_mod
from scripts.temporal import verdict_store as vs
from scripts.temporal.gehi_download import DownloadResult
from scripts.temporal.presence_scorer import Pick as ScorerPick
from scripts.temporal.presence_scorer import PresenceObservation
from scripts.temporal.run_adaptive_scan import VintageCatalog, run_one_anchor
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import VintageEntry
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    GeminiMatrixObservation,
    GeminiObservation,
    GeminiSequenceObservation,
    GeminiSequenceResult,
    MatrixDatePick,
    MatrixTarget,
    SequenceDatePick,
)


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def _key(**overrides: Any) -> str:
    fields: dict[str, Any] = dict(
        chip_sha256="abc123",
        scorer_name="gemini",
        model_id="gemini-3-flash-preview",
        api_format="native",
        prompt_config_hash="sha256:feed",
        scoring_mode="batch",
        extras={"census_mid_date_iso": None},
    )
    fields.update(overrides)
    return vs.build_verdict_key(**fields)


def test_verdict_key_is_stable() -> None:
    assert _key() == _key()
    assert _key().startswith("sha256:")


def test_verdict_key_changes_with_every_component() -> None:
    base = _key()
    assert _key(chip_sha256="other") != base
    assert _key(scorer_name="student") != base
    assert _key(model_id="gemini-3-pro") != base
    assert _key(api_format="agy") != base
    assert _key(prompt_config_hash="sha256:beef") != base
    assert _key(scoring_mode="sequence") != base
    assert _key(extras={"census_mid_date_iso": "2023-06-01"}) != base


def test_verdict_key_extras_order_independent() -> None:
    a = _key(extras={"a": 1, "b": 2})
    b = _key(extras={"b": 2, "a": 1})
    assert a == b


# ---------------------------------------------------------------------------
# VerdictStore persistence
# ---------------------------------------------------------------------------


def test_store_put_get_roundtrip_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    record = {"pv_present": True, "pv_score": 0.9}
    with vs.VerdictStore(path) as store:
        assert store.get("sha256:k1") is None
        store.put("sha256:k1", {"chip_sha256": "c"}, "chip_verdict", record)
        row = store.get("sha256:k1")
        assert row is not None
        assert row["record"] == record
        assert row["record_type"] == "chip_verdict"
        assert len(store) == 1

    with vs.VerdictStore(path) as reopened:
        row = reopened.get("sha256:k1")
        assert row is not None and row["record"] == record


def test_store_last_write_wins_and_compact(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    with vs.VerdictStore(path) as store:
        store.put("sha256:k", {}, "chip_verdict", {"v": 1})
        store.put("sha256:k", {}, "chip_verdict", {"v": 2})
        assert store.get("sha256:k")["record"] == {"v": 2}
        assert len(path.read_text().strip().splitlines()) == 2
        before, after = store.compact()
        assert (before, after) == (2, 1)
    assert len(path.read_text().strip().splitlines()) == 1
    with vs.VerdictStore(path) as reopened:
        assert reopened.get("sha256:k")["record"] == {"v": 2}


def test_store_tolerates_truncated_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    with vs.VerdictStore(path) as store:
        store.put("sha256:k", {}, "chip_verdict", {"v": 1})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"record_version": 1, "key": "sha256:tr')  # crash-truncated line
    with vs.VerdictStore(path) as store:
        assert len(store) == 1
        assert store.corrupt_lines == 1


def test_store_single_writer_enforced_and_read_only(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    store = vs.VerdictStore(path)
    try:
        with pytest.raises(RuntimeError, match="single-writer"):
            vs.VerdictStore(path)
        # Read-only opens are allowed alongside the writer, but reject put().
        ro = vs.VerdictStore(path, read_only=True)
        with pytest.raises(RuntimeError, match="read-only"):
            ro.put("sha256:k", {}, "chip_verdict", {})
        ro.close()
    finally:
        store.close()
    # After close the lock is released and a new writer may open.
    vs.VerdictStore(path).close()


# ---------------------------------------------------------------------------
# Fake scorers
# ---------------------------------------------------------------------------


class _FakeBatchScorer:
    """Counts calls; embeds the call ordinal in evidence so a replayed verdict is
    distinguishable from a re-scored one."""

    name = "fake_batch"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable"})
    decision_sources = frozenset({"gemini_batch", "gemini_failed"})

    def __init__(self, *, decision_source: str = "gemini_batch") -> None:
        self.calls: list[list[Any]] = []
        self._decision_source = decision_source

    def prompt_config_fingerprint(self, mode: str, config: Any) -> dict[str, Any]:
        return {"scorer": self.name, "mode": mode, "model": getattr(config, "model", None)}

    def batch(self, picks, *, config, audit_writer=None, census_mid_date_iso=None,
              routing_salt=None, **_kw):
        self.calls.append(list(picks))
        n = len(self.calls)
        return [
            GeminiObservation(
                chip_index=p.chip_index,
                pv_present=True,
                confidence=0.9,
                quality_flag="usable",
                evidence=f"call {n}",
                notes="fake notes",
                decision_source=self._decision_source,
                raw_response="RAW",
            )
            for p in picks
        ]

    def score(self, picks, *, config=None, **_kw):
        self.calls.append(list(picks))
        n = len(self.calls)
        return [
            PresenceObservation(
                pv_present=True,
                pv_score=0.9,
                quality_flag="usable",
                decision_source=self._decision_source,
                index=p.index or (i + 1),
                capture_date=p.capture_date,
                evidence=f"call {n}",
                notes="fake notes",
            )
            for i, p in enumerate(picks)
        ]


class _Config:
    def __init__(self, model: str = "model-a", api_format: str = "native") -> None:
        self.model = model
        self.api_format = api_format


def _chip(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _batch_picks(tmp_path: Path, n: int) -> list[BatchPick]:
    return [
        BatchPick(
            chip_index=i,
            chip_path=_chip(tmp_path, f"chip_{i}.png", f"PIXELS-{i}".encode()),
            capture_date=f"2020-0{i}-01",
            version=100 + i,
        )
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# Caching proxy — batch entrypoint
# ---------------------------------------------------------------------------


def test_batch_second_identical_call_issues_zero_scorer_calls(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _batch_picks(tmp_path, 2)

    first = wrapped.batch(picks, config=_Config())
    assert len(fake.calls) == 1
    assert [o.evidence for o in first] == ["call 1", "call 1"]

    second = wrapped.batch(picks, config=_Config())
    assert len(fake.calls) == 1, "second identical run must issue zero scorer calls"
    assert [o.chip_index for o in second] == [1, 2]
    for a, b in zip(first, second):
        assert (a.pv_present, a.confidence, a.quality_flag, a.evidence, a.notes,
                a.decision_source, a.error) == (
            b.pv_present, b.confidence, b.quality_flag, b.evidence, b.notes,
            b.decision_source, b.error)
        assert b.raw_response == ""  # raw transport payload is deliberately not memoized

    stats = wrapped.cache_stats()
    assert stats["hits"] == 2 and stats["misses"] == 2
    assert stats["stored"] == 2 and stats["inner_calls"] == 1
    store.close()


def test_batch_partial_miss_scores_only_never_seen_chips(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _batch_picks(tmp_path, 3)

    wrapped.batch(picks[:2], config=_Config())
    results = wrapped.batch(picks, config=_Config())

    assert len(fake.calls) == 2
    assert [p.chip_index for p in fake.calls[1]] == [3], "only the never-seen chip is scored"
    assert sorted(o.chip_index for o in results) == [1, 2, 3]
    by_index = {o.chip_index: o for o in results}
    assert by_index[1].evidence == "call 1" and by_index[2].evidence == "call 1"
    assert by_index[3].evidence == "call 2"
    store.close()


def test_batch_failure_verdicts_are_never_cached(tmp_path: Path) -> None:
    fake = _FakeBatchScorer(decision_source="gemini_failed")
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _batch_picks(tmp_path, 1)

    wrapped.batch(picks, config=_Config())
    wrapped.batch(picks, config=_Config())
    assert len(fake.calls) == 2, "a failed verdict must be re-scored, not replayed"
    assert len(store) == 0
    assert wrapped.cache_stats()["failures_not_cached"] == 2
    store.close()


def test_batch_key_isolation_model_and_census_date(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _batch_picks(tmp_path, 1)

    wrapped.batch(picks, config=_Config(model="model-a"))
    wrapped.batch(picks, config=_Config(model="model-b"))
    assert len(fake.calls) == 2, "a different model identity must miss"

    wrapped.batch(picks, config=_Config(model="model-a"), census_mid_date_iso="2023-06-01")
    assert len(fake.calls) == 3, "a different census calibration date changes the instruction"
    wrapped.batch(picks, config=_Config(model="model-a"), census_mid_date_iso="2023-06-01")
    assert len(fake.calls) == 3
    store.close()


def test_batch_mutated_chip_pixels_miss(tmp_path: Path) -> None:
    """The correctness condition: keying is by pixel hash, so a provider re-render
    under stable (capture_date, version) metadata is a miss, never a stale hit."""
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _batch_picks(tmp_path, 1)

    wrapped.batch(picks, config=_Config())
    Path(picks[0].chip_path).write_bytes(b"RE-RENDERED PIXELS, SAME METADATA")
    wrapped.batch(picks, config=_Config())
    assert len(fake.calls) == 2
    store.close()


def test_batch_unhashable_chip_is_scored_and_not_cached(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    pick = BatchPick(chip_index=1, chip_path=tmp_path / "missing.png", capture_date="2020-01-01")

    wrapped.batch([pick], config=_Config())
    wrapped.batch([pick], config=_Config())
    assert len(fake.calls) == 2
    assert len(store) == 0
    assert wrapped.cache_stats()["uncacheable"] == 2
    store.close()


def test_batch_replay_restamps_chip_index(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    chip = _chip(tmp_path, "chip.png", b"PIXELS")

    wrapped.batch([BatchPick(chip_index=1, chip_path=chip)], config=_Config())
    replayed = wrapped.batch([BatchPick(chip_index=7, chip_path=chip)], config=_Config())
    assert len(fake.calls) == 1
    assert replayed[0].chip_index == 7
    store.close()


# ---------------------------------------------------------------------------
# Caching proxy — seam score() entrypoint (shares chip records with batch)
# ---------------------------------------------------------------------------


def test_score_mode_replays_and_restamps_index_and_date(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    chip = _chip(tmp_path, "chip.png", b"PIXELS")

    first = wrapped.score(
        [ScorerPick(chip_path=str(chip), capture_date="2020-01-01", index=1)],
        config=_Config(),
    )
    second = wrapped.score(
        [ScorerPick(chip_path=str(chip), capture_date="2021-12-31", index=5)],
        config=_Config(),
    )
    assert len(fake.calls) == 1
    assert first[0].evidence == second[0].evidence == "call 1"
    assert second[0].index == 5
    assert second[0].capture_date == "2021-12-31"
    store.close()


def test_chip_records_are_shared_between_batch_and_score(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    chip = _chip(tmp_path, "chip.png", b"PIXELS")

    wrapped.batch([BatchPick(chip_index=1, chip_path=chip)], config=_Config())
    replayed = wrapped.score(
        [ScorerPick(chip_path=str(chip), capture_date="2020-01-01", index=3)],
        config=_Config(),
    )
    assert len(fake.calls) == 1, "a batch-stored verdict replays through score()"
    assert isinstance(replayed[0], PresenceObservation)
    assert replayed[0].pv_score == 0.9
    store.close()


def test_score_partial_miss_with_unindexed_picks_maps_correctly(tmp_path: Path) -> None:
    """Regression: picks with a falsy `index` under a partial miss. The inner
    scorer renumbers `index or (i+1)` relative to the SUBSET it receives; the
    proxy must pin full-list indices onto the miss picks so a fresh verdict is
    never merged into (or stored under) a hit pick's slot."""
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    chip_a = _chip(tmp_path, "a.png", b"PIXELS-A")
    chip_b = _chip(tmp_path, "b.png", b"PIXELS-B")

    wrapped.score([ScorerPick(chip_path=str(chip_a), capture_date="2020-01-01")], config=_Config())
    assert len(store) == 1

    results = wrapped.score(
        [
            ScorerPick(chip_path=str(chip_a), capture_date="2020-01-01"),  # hit (pos 1)
            ScorerPick(chip_path=str(chip_b), capture_date="2021-01-01"),  # miss (pos 2)
        ],
        config=_Config(),
    )
    assert len(fake.calls) == 2
    assert [p.chip_path for p in fake.calls[1]] == [str(chip_b)]
    assert fake.calls[1][0].index == 2, "miss pick must carry its full-list index"
    assert [(o.index, o.evidence) for o in results] == [(1, "call 1"), (2, "call 2")]
    assert len(store) == 2, "fresh verdict stored under its own key, hit key untouched"
    # B replays on its own now.
    wrapped.score([ScorerPick(chip_path=str(chip_b), capture_date="2021-01-01")], config=_Config())
    assert len(fake.calls) == 2
    store.close()


def test_score_full_miss_passes_through_inner_result_identity(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = [ScorerPick(chip_path="", capture_date="2020-01-01", index=1)]
    results = wrapped.score(picks, config=None)
    assert len(results) == 1 and results[0].evidence == "call 1"
    assert len(store) == 0  # empty chip_path is unhashable -> pass-through
    store.close()


# ---------------------------------------------------------------------------
# Caching proxy — sequence entrypoint (window-level)
# ---------------------------------------------------------------------------


def _seq_result(*, decision_source: str = "gemini_sequence", error: str | None = None,
                marker: str = "run 1") -> GeminiSequenceResult:
    return GeminiSequenceResult(
        sequence_pattern="absent_then_present",
        first_present_date="2021-06-15",
        first_present_date_index=2,
        confidence=0.85,
        consistency_flag="consistent",
        quality_flag="usable",
        review_notes=marker,
        observations=[
            GeminiSequenceObservation(
                date_index=1, capture_date="2020-06-15", pv_present=False,
                pv_score=0.1, evidence="bare roof", notes="",
            ),
            GeminiSequenceObservation(
                date_index=2, capture_date="2021-06-15", pv_present=True,
                pv_score=0.9, evidence="panel grid", notes="",
            ),
        ],
        decision_source=decision_source,
        raw_response="RAW",
        error=error,
    )


class _FakeSequenceScorer:
    name = "fake_seq"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable", "sequence_failed"})
    decision_sources = frozenset({"gemini_sequence", "gemini_failed"})

    def __init__(self, *, decision_source: str = "gemini_sequence") -> None:
        self.calls = 0
        self._decision_source = decision_source

    def prompt_config_fingerprint(self, mode: str, config: Any) -> dict[str, Any]:
        return {"scorer": self.name, "mode": mode, "model": getattr(config, "model", None)}

    @property
    def sequence(self):
        def _seq(picks, *, config, audit_writer=None, max_tokens=None, routing_salt=None, **_kw):
            self.calls += 1
            return _seq_result(decision_source=self._decision_source, marker=f"run {self.calls}")

        return _seq

    def score(self, picks, *, config=None, **_kw):  # pragma: no cover - unused
        raise NotImplementedError


def _seq_picks(tmp_path: Path) -> list[SequenceDatePick]:
    return [
        SequenceDatePick(date_index=1, chip_path=_chip(tmp_path, "s1.png", b"S1"),
                         capture_date="2020-06-15", version="tm"),
        SequenceDatePick(date_index=2, chip_path=_chip(tmp_path, "s2.png", b"S2"),
                         capture_date="2021-06-15", version="wb"),
    ]


def test_sequence_window_replay_zero_calls_and_field_identity(tmp_path: Path) -> None:
    fake = _FakeSequenceScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _seq_picks(tmp_path)

    first = wrapped.sequence(picks, config=_Config())
    second = wrapped.sequence(picks, config=_Config())
    assert fake.calls == 1
    assert isinstance(second, GeminiSequenceResult)
    assert second.review_notes == first.review_notes == "run 1"
    assert second.observations == first.observations
    assert (second.sequence_pattern, second.first_present_date,
            second.first_present_date_index, second.confidence,
            second.consistency_flag, second.quality_flag, second.decision_source,
            second.error) == (
        first.sequence_pattern, first.first_present_date,
        first.first_present_date_index, first.confidence,
        first.consistency_flag, first.quality_flag, first.decision_source, first.error)
    assert second.raw_response == ""
    store.close()


def test_sequence_key_covers_window_composition(tmp_path: Path) -> None:
    fake = _FakeSequenceScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _seq_picks(tmp_path)

    wrapped.sequence(picks, config=_Config())
    # Mutate one chip in the window -> whole window misses.
    Path(picks[0].chip_path).write_bytes(b"S1-RERENDERED")
    wrapped.sequence(picks, config=_Config())
    assert fake.calls == 2

    # A different capture-date labelling changes the instruction -> miss.
    relabeled = [
        SequenceDatePick(date_index=p.date_index, chip_path=p.chip_path,
                         capture_date="2019-01-01", version=p.version)
        for p in picks
    ]
    wrapped.sequence(relabeled, config=_Config())
    assert fake.calls == 3

    # max_tokens is instruction identity -> miss.
    wrapped.sequence(picks, config=_Config(), max_tokens=1234)
    assert fake.calls == 4
    store.close()


def test_sequence_failed_result_not_cached(tmp_path: Path) -> None:
    fake = _FakeSequenceScorer(decision_source="gemini_failed")
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks = _seq_picks(tmp_path)

    wrapped.sequence(picks, config=_Config())
    wrapped.sequence(picks, config=_Config())
    assert fake.calls == 2
    assert len(store) == 0
    store.close()


# ---------------------------------------------------------------------------
# Caching proxy — matrix entrypoint (window-level)
# ---------------------------------------------------------------------------


def _matrix_obs(decision_source: str = "gemini_matrix", marker: str = "run 1"
                ) -> list[GeminiMatrixObservation]:
    out = []
    cell = 0
    for date_index, capture_date in ((1, "2020-06-15"), (2, "2021-06-15")):
        for target_id, label in (("T1", "A"), ("T2", "B")):
            cell += 1
            out.append(
                GeminiMatrixObservation(
                    cell_index=cell, date_index=date_index, capture_date=capture_date,
                    target_id=target_id, target_label=label, pv_present=True,
                    confidence=0.8, quality_flag="usable", evidence=marker, notes="",
                    decision_source=decision_source, raw_response="RAW",
                )
            )
    return out


class _FakeMatrixScorer:
    name = "fake_matrix"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable"})
    decision_sources = frozenset({"gemini_matrix", "gemini_failed"})

    def __init__(self, *, fail_one_cell: bool = False) -> None:
        self.calls = 0
        self._fail_one_cell = fail_one_cell

    def prompt_config_fingerprint(self, mode: str, config: Any) -> dict[str, Any]:
        return {"scorer": self.name, "mode": mode, "model": getattr(config, "model", None)}

    @property
    def matrix(self):
        def _matrix(date_picks, targets, *, config, audit_writer=None, **_kw):
            self.calls += 1
            obs = _matrix_obs(marker=f"run {self.calls}")
            if self._fail_one_cell:
                obs[0].decision_source = "gemini_failed"
            return obs

        return _matrix

    def score(self, picks, *, config=None, **_kw):  # pragma: no cover - unused
        raise NotImplementedError


def _matrix_inputs(tmp_path: Path) -> tuple[list[MatrixDatePick], list[MatrixTarget]]:
    picks = [
        MatrixDatePick(date_index=1, chip_path=_chip(tmp_path, "m1.png", b"M1"),
                       capture_date="2020-06-15"),
        MatrixDatePick(date_index=2, chip_path=_chip(tmp_path, "m2.png", b"M2"),
                       capture_date="2021-06-15"),
    ]
    targets = [MatrixTarget("T1", "A"), MatrixTarget("T2", "B")]
    return picks, targets


def test_matrix_window_replay_and_target_sensitivity(tmp_path: Path) -> None:
    fake = _FakeMatrixScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks, targets = _matrix_inputs(tmp_path)

    first = wrapped.matrix(picks, targets, config=_Config())
    second = wrapped.matrix(picks, targets, config=_Config())
    assert fake.calls == 1
    assert [o.evidence for o in second] == [o.evidence for o in first]
    assert all(isinstance(o, GeminiMatrixObservation) for o in second)
    assert all(o.raw_response == "" for o in second)
    assert [(o.cell_index, o.date_index, o.target_id) for o in second] == [
        (o.cell_index, o.date_index, o.target_id) for o in first
    ]

    # A different target set is a different instruction -> miss.
    wrapped.matrix(picks, [MatrixTarget("T9", "Z")], config=_Config())
    assert fake.calls == 2
    store.close()


def test_matrix_with_any_failed_cell_not_cached(tmp_path: Path) -> None:
    fake = _FakeMatrixScorer(fail_one_cell=True)
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    picks, targets = _matrix_inputs(tmp_path)

    wrapped.matrix(picks, targets, config=_Config())
    wrapped.matrix(picks, targets, config=_Config())
    assert fake.calls == 2
    assert len(store) == 0
    store.close()


# ---------------------------------------------------------------------------
# Proxy delegation
# ---------------------------------------------------------------------------


def test_proxy_delegates_declared_scorer_attributes(tmp_path: Path) -> None:
    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    wrapped = vs.with_verdict_store(fake, store)
    assert wrapped.name == "fake_batch"
    assert wrapped.failure_decision_sources == frozenset({"gemini_failed"})
    assert wrapped.quality_flags == fake.quality_flags
    assert wrapped.decision_sources == fake.decision_sources
    store.close()


# ---------------------------------------------------------------------------
# Churn monitor (sentinel chip set)
# ---------------------------------------------------------------------------


def test_sentinel_check_no_churn(tmp_path: Path) -> None:
    chips = [_chip(tmp_path, f"sent_{i}.png", f"SENTINEL-{i}".encode()) for i in range(3)]
    manifest_path = tmp_path / "sentinels.json"
    vs.build_sentinel_manifest(chips, manifest_path)
    report = vs.check_sentinel_manifest(manifest_path)
    assert report.total == 3
    assert report.unchanged == 3
    assert report.churn_rate == 0.0
    assert report.alert is False
    assert report.churned == [] and report.missing == []


def test_sentinel_detects_mutated_chip_and_reports_churn_rate(tmp_path: Path) -> None:
    chips = [_chip(tmp_path, f"sent_{i}.png", f"SENTINEL-{i}".encode()) for i in range(4)]
    manifest_path = tmp_path / "sentinels.json"
    vs.build_sentinel_manifest(chips, manifest_path)

    chips[0].write_bytes(b"PROVIDER RE-RENDER, same metadata")  # churn
    chips[1].unlink()  # eviction, a separate class from churn

    report = vs.check_sentinel_manifest(manifest_path)
    assert report.total == 4
    assert report.unchanged == 2
    assert len(report.churned) == 1 and len(report.missing) == 1
    assert report.churn_rate == pytest.approx(1 / 3)  # over checked (non-missing) sentinels
    assert report.alert is True  # default threshold: any churn alerts
    entry = report.churned[0]
    assert entry["chip_path"].endswith("sent_0.png")
    assert entry["baseline_sha256"] != entry["current_sha256"]
    assert entry["escalation"] == vs.CHURN_ESCALATION_CLASS


def test_sentinel_alert_threshold(tmp_path: Path) -> None:
    chips = [_chip(tmp_path, f"sent_{i}.png", f"SENTINEL-{i}".encode()) for i in range(4)]
    manifest_path = tmp_path / "sentinels.json"
    vs.build_sentinel_manifest(chips, manifest_path)
    chips[0].write_bytes(b"mutated")
    report = vs.check_sentinel_manifest(manifest_path, alert_churn_rate=0.5)
    assert report.churn_rate == pytest.approx(0.25)
    assert report.alert is False


# ---------------------------------------------------------------------------
# Replay gate — adaptive scan through the store: byte-identical scan states,
# zero scorer calls on the re-run. (Freezes now_iso so byte-identity is exact.)
# ---------------------------------------------------------------------------


class _CountingAdaptiveScorer:
    name = "fake_adaptive"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable"})
    decision_sources = frozenset({"gemini_batch"})

    def __init__(self) -> None:
        self.batch_calls = 0

    def prompt_config_fingerprint(self, mode: str, config: Any) -> dict[str, Any]:
        return {"scorer": self.name, "mode": mode, "model": getattr(config, "model", None)}

    def batch(self, picks, *, config, audit_writer=None, census_mid_date_iso=None,
              routing_salt=None, **_kw):
        self.batch_calls += 1
        return [
            GeminiObservation(
                chip_index=p.chip_index, pv_present=True, confidence=0.9,
                quality_flag="usable", evidence="panel grid", notes="",
                decision_source="gemini_batch",
            )
            for p in picks
        ]

    def score(self, picks, *, config=None, **_kw):  # pragma: no cover - unused
        raise NotImplementedError


def _ok_download(path: Path, capture_date: str) -> DownloadResult:
    return DownloadResult(
        anchor_id="a", capture_date=capture_date, version="100",
        requested_zoom_ladder=(20, 19), actual_zoom=20, path=path,
        sha256="deadbeef", status="ok", error=None,
        gehi_command="", download_stdout_sha256="",
    )


def _install_gehi_stubs(monkeypatch, tmp_path: Path, vintages: list[VintageEntry]) -> None:
    def fake_catalog(anchor, config):
        return VintageCatalog(
            vintages=list(vintages),
            available_dates_by_zoom={19: {v.capture_date for v in vintages}},
        )

    def fake_download(anchor, *, capture_date, version, zoom_ladder, output_root,
                      provider="TM", vintage_check=None):
        p = tmp_path / f"chip_{capture_date}.tif"
        p.write_bytes(f"TIF-{capture_date}".encode())
        return _ok_download(p, capture_date)

    def fake_review_png(p: Path) -> Path:
        png = Path(str(p)).with_suffix(".png")
        png.write_bytes(b"PNG-" + Path(p).read_bytes())
        return png

    monkeypatch.setattr(ras, "_fetch_real_vintage_catalog", fake_catalog)
    monkeypatch.setattr(
        ras, "make_vintage_check",
        lambda anchor, *, available_dates_by_zoom, config: (lambda z, d: True),
    )
    monkeypatch.setattr(_gehi_download, "download_chip_with_zoom_ladder", fake_download)
    monkeypatch.setattr(_gehi_common, "ensure_review_png", fake_review_png)


def test_replay_gate_byte_identical_scan_states_zero_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2026-01-01T00:00:00Z")
    vintages = [
        VintageEntry(capture_date="2020-06-15", version=100),
        VintageEntry(capture_date="2021-06-15", version=101),
    ]
    _install_gehi_stubs(monkeypatch, tmp_path, vintages)
    anchor = {"anchor_id": "A_replay", "region_key": "johannesburg", "grid_id": "G1"}
    scorer = _CountingAdaptiveScorer()
    store = vs.VerdictStore(tmp_path / "verdicts.jsonl")

    def _run(dirname: str) -> bytes:
        d = tmp_path / dirname
        run_one_anchor(
            anchor, AdaptiveScanConfig(), d,
            dry_run=False, force_restart=True,
            chips_dir=tmp_path / "chips", audit_dir=tmp_path / "audit",
            gemini_config=_Config(), scorer=scorer,
            census_mid_date_iso=None, verdict_store=store,
        )
        return (d / "A_replay.json").read_bytes()

    first = _run("run_a")
    calls_after_first = scorer.batch_calls
    assert calls_after_first > 0
    second = _run("run_b")

    assert first == second, "replayed cohort slice must produce byte-identical scan states"
    assert scorer.batch_calls == calls_after_first, "re-run must issue zero scorer calls"
    assert store.stats["inner_calls"] == calls_after_first
    assert vs.compare_scan_state_dirs(tmp_path / "run_a", tmp_path / "run_b") == []
    store.close()


def test_replay_gate_dry_run_unaffected_by_store(tmp_path: Path, monkeypatch) -> None:
    """Dry-run picks carry no chip files (chip_path='') -> unhashable -> the store
    passes through and stays empty; behavior is byte-identical to no-store."""
    monkeypatch.setattr(scan_state_mod, "now_iso", lambda: "2026-01-01T00:00:00Z")
    anchor = {"anchor_id": "dry_store_anchor", "region_key": "johannesburg", "grid_id": "G1"}
    store = vs.VerdictStore(tmp_path / "verdicts.jsonl")

    def _run(dirname: str, **kwargs) -> bytes:
        d = tmp_path / dirname
        run_one_anchor(
            anchor, AdaptiveScanConfig(), d,
            dry_run=True, force_restart=True,
            chips_dir=tmp_path / "chips", audit_dir=tmp_path / "audit",
            census_mid_date_iso=None, **kwargs,
        )
        return (d / f"{anchor['anchor_id']}.json").read_bytes()

    with_store = _run("run_store", verdict_store=store)
    without_store = _run("run_plain")
    assert with_store == without_store
    assert len(store) == 0
    store.close()


def test_provenance_rows_still_emitted_on_cache_hits(tmp_path: Path) -> None:
    """Wrap order (provenance outermost, verdict cache inner): a fully-cached call
    still emits one ISSUE-06 sidecar row per chip."""
    from scripts.temporal.scoring_provenance import with_scoring_provenance

    fake = _FakeBatchScorer()
    store = vs.VerdictStore(tmp_path / "store.jsonl")
    rows: list[dict] = []
    wrapped = with_scoring_provenance(vs.with_verdict_store(fake, store), rows.append)
    picks = _batch_picks(tmp_path, 2)

    wrapped.batch(picks, config=_Config())
    assert len(rows) == 2 and len(fake.calls) == 1
    wrapped.batch(picks, config=_Config())
    assert len(fake.calls) == 1, "cache hit"
    assert len(rows) == 4, "cache hits must still be attributable via the sidecar"
    # The provenance prompt hash and the store key component agree by construction.
    assert {r["prompt_config_hash"] for r in rows} == {
        row["key_fields"]["prompt_config_hash"] for row in store.rows()
    }
    store.close()


# ---------------------------------------------------------------------------
# Scan-state dir comparison helper (the operational replay gate)
# ---------------------------------------------------------------------------


def test_compare_scan_state_dirs_flags_differences(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    state = {"anchor_id": "X", "status": "done_appears", "started_at": "t1", "updated_at": "t1"}
    (a / "X.json").write_text(json.dumps(state))
    (b / "X.json").write_text(json.dumps({**state, "started_at": "t2", "updated_at": "t2"}))
    assert vs.compare_scan_state_dirs(a, b) == []  # volatile timestamps ignored

    (b / "X.json").write_text(json.dumps({**state, "status": "done_ambiguous_nonmonotonic"}))
    diffs = vs.compare_scan_state_dirs(a, b)
    assert len(diffs) == 1 and "X.json" in diffs[0]

    (b / "Y.json").write_text(json.dumps(state))
    assert any("Y.json" in d for d in vs.compare_scan_state_dirs(a, b))
