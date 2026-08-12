"""Scoring-provenance sidecar (ISSUE-06, PRD D5, user story 9).

Every chip that flows through the ``PresenceScorer`` seam (``presence_scorer``)
produces a verdict; this module records *what produced it*. For each scored chip
we emit one JSONL sidecar row carrying:

  * scorer identity — the resolved model string read from the ``config`` object
    passed **at call time** (NOT re-read from env downstream), plus ``api_format``
    so the identity is honest for the ``agy`` backend (which ignores ``model``);
  * a prompt/config hash — a stable digest of the exact instruction templates +
    the scoring knobs (model, api_format, token cap, thinking budget, ...);
  * a chip content hash — the sha256 of the encoded bytes actually handed to the
    scorer (the review PNG with its marker overlay, not the source GeoTIFF);
  * the scoring mode (batch / sequence / matrix), a UTC timestamp, and enough
    join keys (anchor_id, capture_date, version, chip_id, target, ...) to line
    the row up against the per-chip download sidecar and the verdict store.

The proxy is transparent: it delegates the scorer's declared vocabulary and
never mutates, reorders, or replaces the scorer's results — it only observes
them on the way out and appends rows. Chip-hash failures are recorded in-row and
never raise; a writer exception, by contrast, propagates (a silently dropped
provenance row defeats the entire point of the sidecar).

Join contract (mirrors ``gehi_download.CHIP_PROVENANCE_FIELDS``):
    scoring sidecar  ⋈  chip sidecar  ON  (anchor_id, capture_date, version).
``chip_sha256`` here hashes the **scored** asset (the review PNG — the bytes the
model saw, which is the D6 verdict-store correctness condition). The chip-side
sidecar hashes the **source** GeoTIFF. The two sha fields are therefore NOT
expected to be equal whenever a review PNG was the thing scored; they line up on
the tuple keys, not on the hash.

Backfill limitation (documented here and in docs/resolution_provenance.md):
historical scans predating this sidecar CANNOT be backfilled. Before it, the
model id was resolved from env at run time and discarded, so no scorer identity
was ever persisted for those verdicts. Drift audits and the ISSUE-07 verdict
store therefore begin at sidecar deployment, not at the start of scan history.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.temporal.presence_scorer import PresenceScorer


# The canonical scoring-provenance row keys. Pinned as a tuple (like
# ``CHIP_PROVENANCE_FIELDS``) so this module, the docs, and any downstream
# consumer agree on exactly these names and this order.
#
# Join to the per-chip download sidecar on (anchor_id, capture_date, version);
# ``chip_sha256`` hashes the SCORED asset (review PNG) while the chip sidecar
# hashes the SOURCE GeoTIFF, so the two sha fields intentionally differ when a
# review PNG was scored. ``context`` is a free-form dict holding whatever
# per-call keys don't have a dedicated column (rep, window_idx, ...).
SCORING_PROVENANCE_FIELDS: tuple[str, ...] = (
    "record_version",
    "ts_utc",
    "scorer_name",
    "model_id",
    "api_format",
    "prompt_config_hash",
    "scoring_mode",
    "anchor_id",
    "chip_id",
    "target_id",
    "target_label",
    "chip_index",
    "capture_date",
    "version",
    "chip_path",
    "chip_sha256",
    "chip_sha256_error",
    "decision_source",
    "quality_flag",
    "context",
)

RECORD_VERSION = 1

# Per-call context keys that get promoted to their own column instead of landing
# in the free-form ``context`` blob.
_KNOWN_CONTEXT_COLUMNS = ("anchor_id", "chip_id", "target_id", "target_label")


def canonical_hash(payload: Any) -> str:
    """Return ``"sha256:<hex>"`` of a canonical JSON serialization of ``payload``.

    Canonicalization (``sort_keys`` + compact separators + ``default=str``) makes
    the digest order-independent and total: two payloads that differ only in key
    order hash identically, and stray non-JSON-native members (Path, date) still
    hash deterministically instead of raising.
    """
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ChipHasher:
    """sha256 of chip files, cached per (path, mtime_ns, size) for one recorder.

    Held per ``ProvenanceRecordingScorer`` so re-scoring the same chip within a
    run hashes it once. The cache key includes mtime + size so a chip overwritten
    in place (e.g. a cache-refresh re-download upgrading the zoom) is re-hashed
    rather than served stale.

    Returns ``(hexdigest, None)`` on success and ``(None, error_message)`` for an
    empty/missing/unreadable path — this method NEVER raises, because a chip-hash
    failure must be recorded in the row, not abort scoring. The digest is a bare
    hex string (no ``sha256:`` prefix) to match ``gehi_download.sha256_file`` so
    the two sidecars' ``chip_sha256`` columns are format-comparable.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, int], tuple[str | None, str | None]] = {}

    def sha256(self, chip_path: str | Path | None) -> tuple[str | None, str | None]:
        if not chip_path:
            return None, "empty_chip_path"
        path = Path(chip_path)
        try:
            stat = path.stat()
        except OSError as exc:  # missing/inaccessible chip is a recorded, non-fatal state
            return None, f"stat_failed: {type(exc).__name__}: {exc}"
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            result: tuple[str | None, str | None] = (digest.hexdigest(), None)
        except OSError as exc:  # unreadable chip is recorded, not fatal
            result = (None, f"read_failed: {type(exc).__name__}: {exc}")
        self._cache[key] = result
        return result


def jsonl_writer(path: str | Path) -> Callable[[Mapping[str, Any]], None]:
    """Build a thread-safe append-a-JSON-line writer for the sidecar file.

    Mirrors the chip-provenance writer convention (mkdir parents, one JSON object
    per line, ``ensure_ascii=False``) plus a ``threading.Lock`` so concurrent
    anchor workers never interleave a partial line. ``default=str`` keeps the
    free-form ``context`` blob serializable.
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def write(record: Mapping[str, Any]) -> None:
        line = json.dumps(dict(record), ensure_ascii=False, default=str)
        with lock, out_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    return write


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProvenanceRecordingScorer:
    """Transparent proxy around a ``PresenceScorer`` that emits a sidecar row per
    scored chip.

    Wraps the four seam entrypoints (``score`` + the raw ``batch`` / ``sequence``
    / ``matrix`` accessors), delegating everything else (name, declared
    vocabulary, any other attribute) to the inner scorer via ``__getattr__``. Each
    wrapped entrypoint:

      * accepts an optional per-call ``provenance_context`` mapping, strips it, and
        calls the inner callable with the ORIGINAL signature (the inner scorer
        never sees ``provenance_context``);
      * merges ``provenance_context`` over the wrap-time ``context`` (per-call
        wins), routing the known keys (anchor_id / chip_id / target_id /
        target_label) to their columns and the remainder to ``context``;
      * reads identity off the ``config`` object passed at call time
        (``model`` / ``api_format``) so model tiering is captured per call; and
      * returns the inner scorer's result object UNCHANGED (same identity).

    ``prompt_config_hash`` uses ``inner.prompt_config_fingerprint(mode, config)``
    when the scorer exposes it, else a minimal ``{scorer, mode, model}`` payload;
    it is cached per ``(mode, config)`` (``GeminiClientConfig`` is a frozen, thus
    hashable, dataclass).
    """

    def __init__(
        self,
        inner: "PresenceScorer",
        writer: Callable[[Mapping[str, Any]], None],
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._context: dict[str, Any] = dict(context or {})
        self._hasher = ChipHasher()
        self._prompt_cache: dict[Any, str] = {}

    # -- attribute delegation -------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes NOT defined on this proxy (name,
        # failure_decision_sources, quality_flags, decision_sources, and any
        # scorer-specific extras) — delegate them to the inner scorer verbatim.
        return getattr(self._inner, name)

    # -- provenance plumbing --------------------------------------------------

    def _prompt_hash(self, mode: str, config: Any) -> str:
        """Digest the scorer's instruction+config identity for `(mode, config)`.

        Prefers the scorer's own ``prompt_config_fingerprint`` payload (the exact
        prompt templates + instruction knobs), else a minimal fallback. Memoized
        per ``(mode, config)`` — ``GeminiClientConfig`` is a frozen (hashable)
        dataclass; an unhashable config just skips the cache.
        """
        try:
            cache_key: Any = (mode, config)
            cached = self._prompt_cache.get(cache_key)
        except TypeError:  # unhashable config — skip caching, still hash
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

    def _resolve_context(
        self, provenance_context: Mapping[str, Any] | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Merge per-call context over wrap-time context and split it.

        Returns ``(columns, leftover)``: the known keys
        (``_KNOWN_CONTEXT_COLUMNS``) that get their own row column, and the
        remainder that lands in the free-form ``context`` blob. Per-call keys win.
        """
        merged = dict(self._context)
        if provenance_context:
            merged.update(provenance_context)
        columns = {k: merged.pop(k) for k in _KNOWN_CONTEXT_COLUMNS if k in merged}
        return columns, merged

    def _record(
        self,
        *,
        mode: str,
        config: Any,
        chip_path: Any,
        capture_date: Any,
        version: Any,
        chip_index: Any,
        decision_source: Any,
        quality_flag: Any,
        columns: Mapping[str, Any],
        leftover_context: Mapping[str, Any],
        target_id: Any = None,
        target_label: Any = None,
    ) -> None:
        """Build one sidecar row and hand it to the writer in pinned field order.

        Hashes the scored chip (never raises — failure is recorded in
        ``chip_sha256_error``), reads identity off ``config``, and emits the row
        keyed strictly by ``SCORING_PROVENANCE_FIELDS``. ``target_id`` /
        ``target_label`` from the observation (matrix) win over the context ones.
        """
        chip_path_str = str(chip_path) if chip_path else ""
        chip_sha256, chip_sha256_error = self._hasher.sha256(chip_path_str)
        record = {
            "record_version": RECORD_VERSION,
            "ts_utc": _now_utc_iso(),
            "scorer_name": getattr(self._inner, "name", None),
            "model_id": getattr(config, "model", None),
            "api_format": getattr(config, "api_format", None),
            "prompt_config_hash": self._prompt_hash(mode, config),
            "scoring_mode": mode,
            "anchor_id": columns.get("anchor_id"),
            "chip_id": columns.get("chip_id"),
            "target_id": target_id if target_id is not None else columns.get("target_id"),
            "target_label": target_label if target_label is not None else columns.get("target_label"),
            "chip_index": chip_index,
            "capture_date": capture_date,
            "version": version,
            "chip_path": chip_path_str or None,
            "chip_sha256": chip_sha256,
            "chip_sha256_error": chip_sha256_error,
            "decision_source": decision_source,
            "quality_flag": quality_flag,
            "context": dict(leftover_context),
        }
        # Emit in the pinned field order; writer exceptions propagate (fail loud).
        self._writer({field: record[field] for field in SCORING_PROVENANCE_FIELDS})

    # -- wrapped entrypoints --------------------------------------------------

    def score(
        self,
        picks: list[Any],
        *,
        config: Any = None,
        provenance_context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        results = self._inner.score(picks, config=config, **kwargs)
        columns, leftover = self._resolve_context(provenance_context)
        by_index = {getattr(p, "index", None): p for p in picks}
        for obs in results:
            pick = by_index.get(getattr(obs, "index", None))
            self._record(
                mode="batch",
                config=config,
                chip_path=getattr(pick, "chip_path", "") if pick is not None else "",
                capture_date=getattr(obs, "capture_date", "")
                or (getattr(pick, "capture_date", "") if pick is not None else ""),
                version=getattr(pick, "version", "") if pick is not None else "",
                chip_index=getattr(obs, "index", None),
                decision_source=getattr(obs, "decision_source", None),
                quality_flag=getattr(obs, "quality_flag", None),
                columns=columns,
                leftover_context=leftover,
            )
        return results

    @property
    def batch(self) -> Callable[..., list[Any]]:
        inner_batch = self._inner.batch  # type: ignore[attr-defined]

        def _wrapped(
            picks: list[Any],
            *,
            config: Any,
            provenance_context: Mapping[str, Any] | None = None,
            **kwargs: Any,
        ) -> list[Any]:
            # ``routing_salt_seed`` is a verdict/provenance identity extra,
            # not a Gemini transport argument.  Keep it in this wrapper's
            # context, but never leak it into a raw scorer callable.
            inner_kwargs = dict(kwargs)
            inner_kwargs.pop("routing_salt_seed", None)
            observations = inner_batch(picks, config=config, **inner_kwargs)
            columns, leftover = self._resolve_context(provenance_context)
            by_index = {getattr(p, "chip_index", None): p for p in picks}
            for obs in observations:
                pick = by_index.get(getattr(obs, "chip_index", None))
                self._record(
                    mode="batch",
                    config=config,
                    chip_path=getattr(pick, "chip_path", "") if pick is not None else "",
                    capture_date=getattr(pick, "capture_date", "") if pick is not None else "",
                    version=getattr(pick, "version", "") if pick is not None else "",
                    chip_index=getattr(obs, "chip_index", None),
                    decision_source=getattr(obs, "decision_source", None),
                    quality_flag=getattr(obs, "quality_flag", None),
                    columns=columns,
                    leftover_context=leftover,
                )
            return observations

        return _wrapped

    @property
    def sequence(self) -> Callable[..., Any]:
        inner_sequence = self._inner.sequence  # type: ignore[attr-defined]

        def _wrapped(
            picks: list[Any],
            *,
            config: Any,
            provenance_context: Mapping[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            result = inner_sequence(picks, config=config, **kwargs)
            columns, leftover = self._resolve_context(provenance_context)
            # Sequence verdicts are target-level (one decision_source / quality_flag
            # for the whole window); emit one row per input date pick.
            decision_source = getattr(result, "decision_source", None)
            quality_flag = getattr(result, "quality_flag", None)
            for pick in picks:
                self._record(
                    mode="sequence",
                    config=config,
                    chip_path=getattr(pick, "chip_path", ""),
                    capture_date=getattr(pick, "capture_date", ""),
                    version=getattr(pick, "version", ""),
                    chip_index=getattr(pick, "date_index", None),
                    decision_source=decision_source,
                    quality_flag=quality_flag,
                    columns=columns,
                    leftover_context=leftover,
                )
            return result

        return _wrapped

    @property
    def matrix(self) -> Callable[..., list[Any]]:
        inner_matrix = self._inner.matrix  # type: ignore[attr-defined]

        def _wrapped(
            date_picks: list[Any],
            targets: Any,
            *,
            config: Any,
            provenance_context: Mapping[str, Any] | None = None,
            **kwargs: Any,
        ) -> list[Any]:
            observations = inner_matrix(date_picks, targets, config=config, **kwargs)
            columns, leftover = self._resolve_context(provenance_context)
            picks_by_date_index = {getattr(p, "date_index", None): p for p in date_picks}
            for obs in observations:
                pick = picks_by_date_index.get(getattr(obs, "date_index", None))
                self._record(
                    mode="matrix",
                    config=config,
                    chip_path=getattr(pick, "chip_path", "") if pick is not None else "",
                    capture_date=getattr(pick, "capture_date", "") if pick is not None else "",
                    version=getattr(pick, "version", "") if pick is not None else "",
                    chip_index=getattr(obs, "date_index", None),
                    decision_source=getattr(obs, "decision_source", None),
                    quality_flag=getattr(obs, "quality_flag", None),
                    columns=columns,
                    leftover_context=leftover,
                    target_id=getattr(obs, "target_id", None),
                    target_label=getattr(obs, "target_label", None),
                )
            return observations

        return _wrapped


def with_scoring_provenance(
    scorer: "PresenceScorer",
    writer: Callable[[Mapping[str, Any]], None],
    context: Mapping[str, Any] | None = None,
) -> ProvenanceRecordingScorer:
    """Wrap ``scorer`` so each scored chip emits one provenance row to ``writer``.

    ``context`` is the wrap-time base context (e.g. ``{"anchor_id": ...}``);
    per-call ``provenance_context`` on the wrapped entrypoints merges over it.
    """
    return ProvenanceRecordingScorer(scorer, writer, context=context)
