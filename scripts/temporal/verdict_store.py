"""Content-addressed verdict store + replay gate + churn monitor (ISSUE-07, PRD D6).

Memoizes scorer verdicts at the ``PresenceScorer`` seam. Key = (chip content
hash x scorer identity x prompt/config hash x scoring mode x call-time
instruction extras) -> observation record. Every call through a
``VerdictCachingScorer``-wrapped scorer consults the store first; re-runs pay
only never-seen keys, and a cached verdict can never drift — the program's
version-drift kill, delivered before any student model exists.

Correctness condition (PRD D6): keying is by **pixel hash** (sha256 of the
encoded chip bytes actually scored), never by capture-date/version metadata —
the imagery provider re-renders pixels under stable metadata, so metadata keys
would serve stale verdicts for changed pixels. A re-rendered chip is simply a
cache miss (re-scored), and the sentinel churn monitor below turns those
re-renders into an alert instead of silent re-scoring.

Memoization grain:

* ``batch`` / ``score()`` — one record per chip, in one normalized shape
  (``chip_verdict``) shared by both entrypoints, so a verdict stored via one
  replays through the other. ``chip_index`` / seam ``index`` / ``capture_date``
  are restamped from the requesting pick at replay time. Batch-composition
  effects (the model sees sibling chips + the census-reference clause) are
  deliberately flattened to the per-chip grain: on an identical re-run the
  composition is identical, and across compositions the first-seen verdict is
  the pinned one. ``census_mid_date_iso`` changes the instruction, so it is
  part of the key.
* ``sequence`` / ``matrix`` — one record per scored **window** (the verdict is
  target-level over the whole ordered chip set), keyed by the ordered list of
  per-chip hashes plus the instruction extras that enter the prompt (ordered
  capture dates; sequence ``max_tokens``; matrix target list). All-or-nothing:
  any changed chip in the window misses the whole window.

Never cached: verdicts whose ``decision_source`` is in the scorer's declared
``failure_decision_sources`` or that carry an ``error`` — a transient API
failure must be re-scored, not replayed forever. Chips whose bytes cannot be
hashed (missing/unreadable path, e.g. dry-run's ``chip_path=""``) pass through
to the scorer uncached, so dry-run behavior is byte-identical with or without
a store. ``raw_response`` (transport debugging payload) is deliberately NOT
memoized — no persisted product reads it back (it lives in per-run audit
JSONLs at scoring time); replayed results carry ``raw_response=""``.

Wrap order with the ISSUE-06 provenance sidecar: **provenance outermost,
verdict cache inner** (``with_scoring_provenance(with_verdict_store(scorer,
store), writer)``). Cache hits then still emit sidecar rows, so every verdict
in every run stays attributable; the sidecar's ``prompt_config_hash`` and the
store key's component agree by construction (both hash the scorer's
``prompt_config_fingerprint`` payload).

Concurrency: safe under the production parallelism pattern — one process,
many anchor worker threads (``ThreadPoolExecutor``) sharing one
``VerdictStore`` (all index/append/counter mutations under a
``threading.Lock``). Cross-process access is a **single-writer constraint,
enforced**: opening a store for write takes a non-blocking ``fcntl`` exclusive
lock on ``<store>.lock`` and a second writer raises immediately. Concurrent
``read_only=True`` opens are allowed (they see the file as of open time).

Store shape & size: append-only JSONL, one row per verdict, last-write-wins on
key collision, fully indexed in memory on open. At cohort scale (~11.5k
anchors x ~15-25 scored frames) expect ~200-300k ``chip_verdict`` rows at
~0.5 KB each => ~100-150 MB on disk / in RAM. ``compact()`` rewrites the file
keeping only the live row per key (relevant after repeated re-runs append
superseded rows). See ``docs/verdict_store.md``.

Backfill limitation (inherited from ISSUE-06): scans predating the scoring
provenance sidecar recorded no scorer identity, so their verdicts can never be
seeded into this store — replay coverage begins at sidecar deployment.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from scripts.temporal.presence_scorer import (
    DEFAULT_FAILURE_DECISION_SOURCES,
    PresenceObservation,
)
from scripts.temporal.scoring_provenance import ChipHasher, canonical_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.temporal.presence_scorer import PresenceScorer

STORE_RECORD_VERSION = 1
SENTINEL_MANIFEST_VERSION = 1

#: Escalation class stamped on churned sentinel frames: they must be re-scored
#: deliberately (surfaced to the operator), never silently.
CHURN_ESCALATION_CLASS = "rescore_deliberate"

#: Volatile scan-state fields ignored by the replay gate comparison.
REPLAY_IGNORED_FIELDS: tuple[str, ...] = ("started_at", "updated_at")

_STAT_KEYS = ("hits", "misses", "stored", "inner_calls", "uncacheable", "failures_not_cached")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def build_verdict_key(
    *,
    chip_sha256: str,
    scorer_name: str | None,
    model_id: str | None,
    api_format: str | None,
    prompt_config_hash: str,
    scoring_mode: str,
    extras: Mapping[str, Any] | None = None,
) -> str:
    """Digest the full verdict identity into one content-addressed key.

    ``chip_sha256`` is the pixel identity (a single chip's hash, or for
    window modes the canonical hash of the ordered per-chip hash list).
    ``extras`` carries call-time inputs that change the instruction the model
    executes but live outside the config object (census_mid_date_iso, window
    capture dates, matrix targets, ...). ``canonical_hash`` makes the digest
    independent of dict key order.
    """
    payload = {
        "key_version": STORE_RECORD_VERSION,
        "chip_sha256": chip_sha256,
        "scorer_name": scorer_name,
        "model_id": model_id,
        "api_format": api_format,
        "prompt_config_hash": prompt_config_hash,
        "scoring_mode": scoring_mode,
        "extras": dict(extras or {}),
    }
    return canonical_hash(payload)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class VerdictStore:
    """Append-only JSONL KV with an in-memory index, thread-safe, single-writer.

    Row shape: ``{record_version, ts_utc, key, key_fields, record_type,
    record}``. ``key_fields`` is kept verbatim for drift audits (it is the
    pre-hash key payload). Later rows for the same key win at load time.

    ``stats`` aggregates cache accounting across every
    ``VerdictCachingScorer`` sharing this store (proxies are per-anchor in the
    adaptive scan; the store is the run-level aggregation point).
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.corrupt_lines = 0
        self.stats: dict[str, int] = {k: 0 for k in _STAT_KEYS}
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, Any]] = {}
        self._lock_fd: int | None = None
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = Path(str(self.path) + ".lock")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                raise RuntimeError(
                    f"verdict store {self.path} is already open for write by another "
                    f"process — the store is single-writer (enforced via {lock_path}). "
                    "Open with read_only=True for concurrent readers."
                ) from None
            self._lock_fd = fd
        self._load()

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> "VerdictStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # A crash-truncated trailing line is expected after an unclean
                    # shutdown; count it, never fail the whole store.
                    self.corrupt_lines += 1
                    continue
                key = row.get("key")
                if not isinstance(key, str):
                    self.corrupt_lines += 1
                    continue
                self._index[key] = row

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._index.get(key)

    def put(
        self,
        key: str,
        key_fields: Mapping[str, Any],
        record_type: str,
        record: Mapping[str, Any],
    ) -> None:
        if self.read_only:
            raise RuntimeError(f"verdict store {self.path} was opened read-only")
        row = {
            "record_version": STORE_RECORD_VERSION,
            "ts_utc": _now_utc_iso(),
            "key": key,
            "key_fields": dict(key_fields),
            "record_type": record_type,
            "record": dict(record),
        }
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._index[key] = row

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._index.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)

    def bump(self, stat: str, n: int = 1) -> None:
        with self._lock:
            self.stats[stat] += n

    def stats_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.stats)

    def compact(self) -> tuple[int, int]:
        """Rewrite the file keeping only the live row per key.

        Returns ``(rows_before, rows_after)`` where ``rows_before`` counts the
        physical (non-empty) lines including superseded duplicates.
        """
        if self.read_only:
            raise RuntimeError(f"verdict store {self.path} was opened read-only")
        with self._lock:
            before = 0
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    before = sum(1 for line in fh if line.strip())
            fd, tmp_path = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    for row in self._index.values():
                        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                os.replace(tmp_path, self.path)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
            return before, len(self._index)


# ---------------------------------------------------------------------------
# Record (de)hydration — one normalized chip shape, window shapes for seq/matrix
# ---------------------------------------------------------------------------

_CHIP_RECORD_FIELDS = (
    "pv_present", "pv_score", "quality_flag", "decision_source", "evidence", "notes", "error",
)


def _chip_record_from_batch_obs(obs: Any) -> dict[str, Any]:
    return {
        "pv_present": obs.pv_present,
        "pv_score": obs.confidence,
        "quality_flag": obs.quality_flag,
        "decision_source": obs.decision_source,
        "evidence": obs.evidence,
        "notes": obs.notes,
        "error": obs.error,
    }


def _chip_record_from_presence_obs(obs: PresenceObservation) -> dict[str, Any]:
    return {f: getattr(obs, f) for f in _CHIP_RECORD_FIELDS}


def _batch_obs_from_record(record: Mapping[str, Any], chip_index: int) -> Any:
    from scripts.validation.gemini_solar_image_review import GeminiObservation

    return GeminiObservation(
        chip_index=chip_index,
        pv_present=record["pv_present"],
        confidence=record["pv_score"],
        quality_flag=record["quality_flag"],
        evidence=record["evidence"],
        notes=record["notes"],
        decision_source=record["decision_source"],
        raw_response="",
        error=record.get("error"),
    )


def _presence_obs_from_record(
    record: Mapping[str, Any], *, index: int, capture_date: str
) -> PresenceObservation:
    return PresenceObservation(
        pv_present=record["pv_present"],
        pv_score=record["pv_score"],
        quality_flag=record["quality_flag"],
        decision_source=record["decision_source"],
        index=index,
        capture_date=capture_date,
        evidence=record["evidence"],
        notes=record["notes"],
        error=record.get("error"),
    )


def _sequence_record_from_result(result: Any) -> dict[str, Any]:
    return {
        "sequence_pattern": result.sequence_pattern,
        "first_present_date": result.first_present_date,
        "first_present_date_index": result.first_present_date_index,
        "confidence": result.confidence,
        "consistency_flag": result.consistency_flag,
        "quality_flag": result.quality_flag,
        "review_notes": result.review_notes,
        "decision_source": result.decision_source,
        "error": result.error,
        "observations": [
            {
                "date_index": o.date_index,
                "capture_date": o.capture_date,
                "pv_present": o.pv_present,
                "pv_score": o.pv_score,
                "evidence": o.evidence,
                "notes": o.notes,
            }
            for o in result.observations
        ],
    }


def _sequence_result_from_record(record: Mapping[str, Any]) -> Any:
    from scripts.validation.gemini_solar_image_review import (
        GeminiSequenceObservation,
        GeminiSequenceResult,
    )

    return GeminiSequenceResult(
        sequence_pattern=record["sequence_pattern"],
        first_present_date=record["first_present_date"],
        first_present_date_index=record["first_present_date_index"],
        confidence=record["confidence"],
        consistency_flag=record["consistency_flag"],
        quality_flag=record["quality_flag"],
        review_notes=record["review_notes"],
        observations=[GeminiSequenceObservation(**o) for o in record["observations"]],
        decision_source=record["decision_source"],
        raw_response="",
        error=record.get("error"),
    )


def _matrix_record_from_observations(observations: list[Any]) -> dict[str, Any]:
    return {
        "observations": [
            {
                "cell_index": o.cell_index,
                "date_index": o.date_index,
                "capture_date": o.capture_date,
                "target_id": o.target_id,
                "target_label": o.target_label,
                "pv_present": o.pv_present,
                "confidence": o.confidence,
                "quality_flag": o.quality_flag,
                "evidence": o.evidence,
                "notes": o.notes,
                "decision_source": o.decision_source,
                "error": o.error,
            }
            for o in observations
        ]
    }


def _matrix_observations_from_record(record: Mapping[str, Any]) -> list[Any]:
    from scripts.validation.gemini_solar_image_review import GeminiMatrixObservation

    return [
        GeminiMatrixObservation(raw_response="", **o) for o in record["observations"]
    ]


# ---------------------------------------------------------------------------
# Caching proxy
# ---------------------------------------------------------------------------


class VerdictCachingScorer:
    """Transparent proxy that consults the store before the wrapped scorer.

    Mirrors ``ProvenanceRecordingScorer``'s shape: wraps the four seam
    entrypoints, delegates everything else (name, declared vocabulary, custom
    extras) to the inner scorer via ``__getattr__``, and never mutates a fresh
    (non-replayed) result — a full-miss call returns the inner scorer's result
    object unchanged. Cache accounting lands on the shared store's ``stats``.
    """

    def __init__(self, inner: "PresenceScorer", store: VerdictStore) -> None:
        self._inner = inner
        self._store = store
        self._hasher = ChipHasher()
        self._prompt_cache: dict[Any, str] = {}
        declared = getattr(inner, "failure_decision_sources", None)
        self._failure_sources = (
            DEFAULT_FAILURE_DECISION_SOURCES if declared is None else frozenset(declared)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def cache_stats(self) -> dict[str, int]:
        return self._store.stats_snapshot()

    # -- key plumbing ----------------------------------------------------------

    def _prompt_hash(self, mode: str, config: Any) -> str:
        """Digest of the scorer's instruction+config identity for ``(mode, config)``.

        Same payload as the ISSUE-06 sidecar's ``prompt_config_hash`` (both hash
        ``inner.prompt_config_fingerprint``), so sidecar rows and store keys line
        up in drift audits. Memoized per hashable ``(mode, config)``.
        """
        try:
            cache_key: Any = (mode, config)
            cached = self._prompt_cache.get(cache_key)
        except TypeError:
            cache_key = None
            cached = None
        if cached is not None:
            return cached
        fingerprint = getattr(self._inner, "prompt_config_fingerprint", None)
        if callable(fingerprint):
            payload = fingerprint(mode, config)
        else:
            payload = {
                "scorer": getattr(self._inner, "name", None),
                "mode": mode,
                "model": getattr(config, "model", None),
            }
        digest = canonical_hash(payload)
        if cache_key is not None:
            self._prompt_cache[cache_key] = digest
        return digest

    def _key_fields(
        self, chip_sha256: str, mode: str, config: Any, extras: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "chip_sha256": chip_sha256,
            "scorer_name": getattr(self._inner, "name", None),
            "model_id": getattr(config, "model", None),
            "api_format": getattr(config, "api_format", None),
            "prompt_config_hash": self._prompt_hash(mode, config),
            "scoring_mode": mode,
            "extras": dict(extras),
        }

    def _cacheable(self, decision_source: Any, error: Any) -> bool:
        return decision_source not in self._failure_sources and not error

    # -- per-chip entrypoints (mode "batch") ------------------------------------

    def _lookup_chips(
        self,
        picks: list[Any],
        chip_paths: list[Any],
        config: Any,
        extras: Mapping[str, Any],
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str], list[int]]:
        """Consult the store per chip. Returns (hit records by pick position,
        keys by pick position for cacheable chips, miss pick positions)."""
        hits: dict[int, dict[str, Any]] = {}
        keys: dict[int, str] = {}
        miss_positions: list[int] = []
        for pos, pick in enumerate(picks):
            digest, _err = self._hasher.sha256(chip_paths[pos])
            if digest is None:
                self._store.bump("uncacheable")
                miss_positions.append(pos)
                continue
            fields = self._key_fields(digest, "batch", config, extras)
            key = build_verdict_key(**fields)
            keys[pos] = key
            row = self._store.get(key)
            if row is not None and row.get("record_type") == "chip_verdict":
                hits[pos] = row["record"]
                self._store.bump("hits")
            else:
                self._store.bump("misses")
                miss_positions.append(pos)
        return hits, keys, miss_positions

    def _store_chip_verdict(
        self, key: str | None, config: Any, extras: Mapping[str, Any],
        chip_sha256_for_fields: str | None, record: dict[str, Any],
    ) -> None:
        if key is None:
            return
        if self._cacheable(record.get("decision_source"), record.get("error")):
            fields = self._key_fields(chip_sha256_for_fields or "", "batch", config, extras)
            self._store.put(key, fields, "chip_verdict", record)
            self._store.bump("stored")
        else:
            self._store.bump("failures_not_cached")

    @property
    def batch(self) -> Callable[..., list[Any]]:
        inner_batch = self._inner.batch  # type: ignore[attr-defined]

        def _wrapped(picks: list[Any], *, config: Any, **kwargs: Any) -> list[Any]:
            extras = {"census_mid_date_iso": kwargs.get("census_mid_date_iso")}
            chip_paths = [getattr(p, "chip_path", None) for p in picks]
            indices = [getattr(p, "chip_index", i + 1) for i, p in enumerate(picks)]
            hits, keys, miss_positions = self._lookup_chips(picks, chip_paths, config, extras)
            misses = [picks[pos] for pos in miss_positions]

            fresh: list[Any] = []
            if misses or not picks:
                self._store.bump("inner_calls")
                fresh = inner_batch(misses if hits else picks, config=config, **kwargs)
            if not hits:
                # Full miss: store cacheable verdicts, return the inner result as-is.
                self._record_fresh_batch(fresh, picks, keys, chip_paths, config, extras)
                return fresh
            self._record_fresh_batch(fresh, picks, keys, chip_paths, config, extras)
            fresh_by_index = {getattr(o, "chip_index", None): o for o in fresh}
            merged: list[Any] = []
            for pos, pick in enumerate(picks):
                if pos in hits:
                    merged.append(_batch_obs_from_record(hits[pos], indices[pos]))
                elif indices[pos] in fresh_by_index:
                    merged.append(fresh_by_index.pop(indices[pos]))
            merged.extend(o for o in fresh_by_index.values() if o is not None)
            return merged

        return _wrapped

    def _record_fresh_batch(
        self, fresh: list[Any], picks: list[Any], keys: dict[int, str],
        chip_paths: list[Any], config: Any, extras: Mapping[str, Any],
    ) -> None:
        pos_by_chip_index = {
            getattr(p, "chip_index", i + 1): i for i, p in enumerate(picks)
        }
        for obs in fresh:
            pos = pos_by_chip_index.get(getattr(obs, "chip_index", None))
            if pos is None or pos not in keys:
                continue
            digest, _err = self._hasher.sha256(chip_paths[pos])
            self._store_chip_verdict(
                keys[pos], config, extras, digest, _chip_record_from_batch_obs(obs)
            )

    def score(
        self, picks: list[Any], *, config: Any = None, **kwargs: Any
    ) -> list[Any]:
        extras = {"census_mid_date_iso": kwargs.get("census_mid_date_iso")}
        chip_paths = [getattr(p, "chip_path", None) for p in picks]
        indices = [getattr(p, "index", 0) or (i + 1) for i, p in enumerate(picks)]
        hits, keys, miss_positions = self._lookup_chips(picks, chip_paths, config, extras)

        # A pick with a falsy `index` gets its FULL-LIST positional index pinned
        # before an inner partial-miss call: the inner scorer's own `index or
        # (i+1)` fallback renumbers relative to the subset, which would collide
        # with the full-list numbering used for hit-merging and key storage.
        misses: list[Any] = []
        for pos in miss_positions:
            pick = picks[pos]
            if not getattr(pick, "index", 0) and dataclasses.is_dataclass(pick):
                pick = dataclasses.replace(pick, index=indices[pos])
            misses.append(pick)

        fresh: list[Any] = []
        if misses or not picks:
            self._store.bump("inner_calls")
            fresh = self._inner.score(misses if hits else picks, config=config, **kwargs)
        # Store cacheable fresh verdicts under their pick's key.
        pos_by_index = {indices[i]: i for i in range(len(picks))}
        for obs in fresh:
            pos = pos_by_index.get(getattr(obs, "index", None))
            if pos is None or pos not in keys:
                continue
            digest, _err = self._hasher.sha256(chip_paths[pos])
            self._store_chip_verdict(
                keys[pos], config, extras, digest, _chip_record_from_presence_obs(obs)
            )
        if not hits:
            return fresh
        fresh_by_index = {getattr(o, "index", None): o for o in fresh}
        merged: list[Any] = []
        for pos, pick in enumerate(picks):
            if pos in hits:
                merged.append(
                    _presence_obs_from_record(
                        hits[pos],
                        index=indices[pos],
                        capture_date=getattr(pick, "capture_date", ""),
                    )
                )
            elif indices[pos] in fresh_by_index:
                merged.append(fresh_by_index.pop(indices[pos]))
        merged.extend(o for o in fresh_by_index.values() if o is not None)
        return merged

    # -- window entrypoints ------------------------------------------------------

    def _window_sha(self, chip_paths: list[Any]) -> str | None:
        hashes: list[str] = []
        for path in chip_paths:
            digest, _err = self._hasher.sha256(path)
            if digest is None:
                return None
            hashes.append(digest)
        return canonical_hash(hashes)

    @property
    def sequence(self) -> Callable[..., Any]:
        inner_sequence = self._inner.sequence  # type: ignore[attr-defined]

        def _wrapped(picks: list[Any], *, config: Any, **kwargs: Any) -> Any:
            window_sha = self._window_sha([getattr(p, "chip_path", None) for p in picks])
            if window_sha is None or not picks:
                self._store.bump("uncacheable")
                self._store.bump("inner_calls")
                return inner_sequence(picks, config=config, **kwargs)
            extras = {
                "capture_dates": [getattr(p, "capture_date", "") for p in picks],
                "max_tokens": kwargs.get("max_tokens"),
            }
            fields = self._key_fields(window_sha, "sequence", config, extras)
            key = build_verdict_key(**fields)
            row = self._store.get(key)
            if row is not None and row.get("record_type") == "sequence_verdict":
                self._store.bump("hits")
                return _sequence_result_from_record(row["record"])
            self._store.bump("misses")
            self._store.bump("inner_calls")
            result = inner_sequence(picks, config=config, **kwargs)
            if self._cacheable(
                getattr(result, "decision_source", None), getattr(result, "error", None)
            ):
                self._store.put(key, fields, "sequence_verdict", _sequence_record_from_result(result))
                self._store.bump("stored")
            else:
                self._store.bump("failures_not_cached")
            return result

        return _wrapped

    @property
    def matrix(self) -> Callable[..., list[Any]]:
        inner_matrix = self._inner.matrix  # type: ignore[attr-defined]

        def _wrapped(
            date_picks: list[Any], targets: Any, *, config: Any, **kwargs: Any
        ) -> list[Any]:
            window_sha = self._window_sha([getattr(p, "chip_path", None) for p in date_picks])
            if window_sha is None or not date_picks or not targets:
                self._store.bump("uncacheable")
                self._store.bump("inner_calls")
                return inner_matrix(date_picks, targets, config=config, **kwargs)
            extras = {
                "capture_dates": [getattr(p, "capture_date", "") for p in date_picks],
                "targets": [
                    [getattr(t, "target_id", None), getattr(t, "target_label", None)]
                    for t in targets
                ],
            }
            fields = self._key_fields(window_sha, "matrix", config, extras)
            key = build_verdict_key(**fields)
            row = self._store.get(key)
            if row is not None and row.get("record_type") == "matrix_verdicts":
                self._store.bump("hits")
                return _matrix_observations_from_record(row["record"])
            self._store.bump("misses")
            self._store.bump("inner_calls")
            observations = inner_matrix(date_picks, targets, config=config, **kwargs)
            complete = len(observations) == len(date_picks) * len(targets) and all(
                self._cacheable(
                    getattr(o, "decision_source", None), getattr(o, "error", None)
                )
                for o in observations
            )
            if complete:
                self._store.put(
                    key, fields, "matrix_verdicts", _matrix_record_from_observations(observations)
                )
                self._store.bump("stored")
            else:
                self._store.bump("failures_not_cached")
            return observations

        return _wrapped


def with_verdict_store(scorer: "PresenceScorer", store: VerdictStore) -> VerdictCachingScorer:
    """Wrap ``scorer`` so every seam call consults ``store`` first.

    Compose with the ISSUE-06 sidecar as
    ``with_scoring_provenance(with_verdict_store(scorer, store), writer)`` —
    provenance outermost, so cache hits still emit sidecar rows.
    """
    return VerdictCachingScorer(scorer, store)


# ---------------------------------------------------------------------------
# Churn monitor — fixed sentinel chip set
# ---------------------------------------------------------------------------


@dataclass
class ChurnReport:
    """Result of one sentinel sweep.

    ``churn_rate`` is computed over checked (non-missing) sentinels; missing
    files are a separate class (local eviction, not provider churn). Churned
    entries carry ``escalation=CHURN_ESCALATION_CLASS``: they must be re-scored
    deliberately (they will miss the store anyway — the flag makes the re-render
    an operator-visible event instead of a silent re-score).
    """

    total: int
    unchanged: int
    churned: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    churn_rate: float = 0.0
    alert: bool = False
    alert_churn_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "unchanged": self.unchanged,
            "churned": self.churned,
            "missing": self.missing,
            "churn_rate": self.churn_rate,
            "alert": self.alert,
            "alert_churn_rate": self.alert_churn_rate,
        }


def build_sentinel_manifest(
    chip_paths: list[str | Path], manifest_path: str | Path
) -> dict[str, Any]:
    """Hash the fixed sentinel chip set now and persist the baseline manifest.

    Sentinels should be a stable, geographically spread sample of scored chips
    (re-downloaded on each sweep by the caller's own fetch step; this module
    only compares bytes). An unreadable chip at baseline time is an error —
    a baseline you cannot hash cannot detect churn.
    """
    hasher = ChipHasher()
    sentinels: list[dict[str, Any]] = []
    for path in chip_paths:
        digest, err = hasher.sha256(path)
        if digest is None:
            raise ValueError(f"cannot hash sentinel chip {path}: {err}")
        sentinels.append(
            {"sentinel_id": Path(path).name, "chip_path": str(path), "chip_sha256": digest}
        )
    manifest = {
        "manifest_version": SENTINEL_MANIFEST_VERSION,
        "created_utc": _now_utc_iso(),
        "sentinels": sentinels,
    }
    out = Path(manifest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def check_sentinel_manifest(
    manifest_path: str | Path, *, alert_churn_rate: float = 0.0
) -> ChurnReport:
    """Re-hash every sentinel and report the churn rate against the baseline.

    ``alert`` fires when ``churn_rate > alert_churn_rate`` — the default 0.0
    alerts on any churn (a provider re-render sweep is exactly the spike the
    monitor exists to surface).
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    hasher = ChipHasher()
    churned: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unchanged = 0
    for sentinel in manifest.get("sentinels", []):
        digest, err = hasher.sha256(sentinel["chip_path"])
        if digest is None:
            missing.append({**sentinel, "error": err})
        elif digest != sentinel["chip_sha256"]:
            churned.append(
                {
                    "sentinel_id": sentinel["sentinel_id"],
                    "chip_path": sentinel["chip_path"],
                    "baseline_sha256": sentinel["chip_sha256"],
                    "current_sha256": digest,
                    "escalation": CHURN_ESCALATION_CLASS,
                }
            )
        else:
            unchanged += 1
    total = len(manifest.get("sentinels", []))
    checked = total - len(missing)
    churn_rate = (len(churned) / checked) if checked else 0.0
    return ChurnReport(
        total=total,
        unchanged=unchanged,
        churned=churned,
        missing=missing,
        churn_rate=churn_rate,
        alert=churn_rate > alert_churn_rate,
        alert_churn_rate=alert_churn_rate,
    )


# ---------------------------------------------------------------------------
# Replay gate — scan-state dir comparison ignoring volatile timestamps
# ---------------------------------------------------------------------------


def compare_scan_state_dirs(
    dir_a: str | Path,
    dir_b: str | Path,
    *,
    ignore_fields: tuple[str, ...] = REPLAY_IGNORED_FIELDS,
) -> list[str]:
    """Compare two scan-state directories; return human-readable differences.

    Empty list == replay-identical. Top-level volatile timestamp fields
    (``started_at`` / ``updated_at``) are ignored; every other byte of the
    parsed state must match. Used by the ``replay-diff`` CLI subcommand and the
    replay-gate tests.
    """
    a_dir, b_dir = Path(dir_a), Path(dir_b)
    a_files = {p.name for p in a_dir.glob("*.json")}
    b_files = {p.name for p in b_dir.glob("*.json")}
    diffs = [f"only in {a_dir}: {name}" for name in sorted(a_files - b_files)]
    diffs += [f"only in {b_dir}: {name}" for name in sorted(b_files - a_files)]
    for name in sorted(a_files & b_files):
        state_a = json.loads((a_dir / name).read_text(encoding="utf-8"))
        state_b = json.loads((b_dir / name).read_text(encoding="utf-8"))
        for fld in ignore_fields:
            state_a.pop(fld, None)
            state_b.pop(fld, None)
        if canonical_hash(state_a) != canonical_hash(state_b):
            diffs.append(f"scan state differs (beyond {ignore_fields}): {name}")
    return diffs


# ---------------------------------------------------------------------------
# CLI: stats / compact / sentinel-init / sentinel-check / replay-diff
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="record counts per record_type")
    p_stats.add_argument("--store", type=Path, required=True)

    p_compact = sub.add_parser("compact", help="drop superseded rows (last-write-wins)")
    p_compact.add_argument("--store", type=Path, required=True)

    p_init = sub.add_parser("sentinel-init", help="hash a fixed sentinel chip set as baseline")
    p_init.add_argument("--manifest", type=Path, required=True)
    p_init.add_argument("--chip", type=Path, action="append", default=[])
    p_init.add_argument(
        "--chips-file", type=Path, help="text file with one chip path per line"
    )

    p_check = sub.add_parser("sentinel-check", help="re-hash sentinels, report churn rate")
    p_check.add_argument("--manifest", type=Path, required=True)
    p_check.add_argument("--alert-churn-rate", type=float, default=0.0)

    p_diff = sub.add_parser("replay-diff", help="compare two scan-state dirs (replay gate)")
    p_diff.add_argument("dir_a", type=Path)
    p_diff.add_argument("dir_b", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "stats":
        with VerdictStore(args.store, read_only=True) as store:
            by_type: dict[str, int] = {}
            for row in store.rows():
                by_type[row.get("record_type", "?")] = by_type.get(row.get("record_type", "?"), 0) + 1
            print(json.dumps({
                "path": str(store.path), "records": len(store),
                "by_record_type": by_type, "corrupt_lines": store.corrupt_lines,
            }, indent=2))
        return 0

    if args.cmd == "compact":
        with VerdictStore(args.store) as store:
            before, after = store.compact()
        print(json.dumps({"rows_before": before, "rows_after": after}, indent=2))
        return 0

    if args.cmd == "sentinel-init":
        chips: list[str | Path] = list(args.chip)
        if args.chips_file:
            chips += [
                line.strip()
                for line in args.chips_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        if not chips:
            parser.error("sentinel-init needs --chip and/or --chips-file")
        manifest = build_sentinel_manifest(chips, args.manifest)
        print(f"wrote {len(manifest['sentinels'])} sentinels -> {args.manifest}")
        return 0

    if args.cmd == "sentinel-check":
        report = check_sentinel_manifest(args.manifest, alert_churn_rate=args.alert_churn_rate)
        print(json.dumps(report.to_dict(), indent=2))
        return 2 if report.alert else 0

    if args.cmd == "replay-diff":
        diffs = compare_scan_state_dirs(args.dir_a, args.dir_b)
        for diff in diffs:
            print(diff)
        print(f"{'REPLAY-IDENTICAL' if not diffs else f'{len(diffs)} difference(s)'}")
        return 1 if diffs else 0

    return 0  # pragma: no cover - unreachable (subparsers required)


if __name__ == "__main__":
    sys.exit(main())
