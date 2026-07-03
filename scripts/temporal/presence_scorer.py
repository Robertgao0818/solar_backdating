"""Injected PresenceScorer seam (ISSUE-05, Phase 1 — Provenance).

Every chip-scoring path in the temporal stack (adaptive scan, census-narrowing
scan, full-stack validation harness, chip-group matrix scorer) scores chips
through *one* injected `PresenceScorer` obtained from this module's registry
instead of importing a concrete scorer directly. The Gemini scorer is the first
registered implementation; a dry-run stub mirrors the old
`dry_run_gemini_result` semantics.

Design constraints baked in here:

* Importing this module never pulls Gemini / network dependencies. The Gemini
  implementation imports `scripts.validation.gemini_solar_image_review` lazily,
  only when a caller actually scores.
* Each scorer *declares* its vocabulary: the `quality_flag` and
  `decision_source` strings it can emit, and — crucially — the subset of
  `decision_source` values that mean "the scorer failed to reach a verdict"
  (`failure_decision_sources`). `scan_decision`'s >50%-failed ambiguity rule
  reads that declared set instead of hardcoding `"gemini_failed"`.
* Module-level vocabulary registries (`quality_flag` / `decision_source`) are
  seeded with every value inventoried across the temporal + validation scripts
  and are enforced at scan-state *write* time. New values are registrable
  additively via `register_quality_flag` / `register_decision_source`.

This module owns only the seam. The four call-sites are migrated separately;
their `Pick`/observation adapters map their native records onto `Pick` /
`PresenceObservation` here and back, preserving byte-identical persisted output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.validation.gemini_solar_image_review import (
        GeminiObservation,
        GeminiSequenceResult,
    )


# ---------------------------------------------------------------------------
# Vocabulary registries (seeded from the full temporal + validation inventory)
# ---------------------------------------------------------------------------

# quality_flag values emitted anywhere in the stack. Enforced at scan_state
# write time (see scan_state.save_scan_state). Additive via
# register_quality_flag().
_QUALITY_FLAGS: set[str] = {
    # scan_state.QUALITY_FLAGS (adaptive-scan RoundResult)
    "usable",
    "ambiguous",
    "unusable",
    # score_anchor_presence._base_quality_and_source
    "ok",
    "missing_chip",
    "no_date_metadata",
    "mixed_capture_dates",
    "needs_review",
    # score_target_sequence pending / Gemini sequence failure
    "sequence_failed",
    "sequence_pending_missing_review_png",
    # fullstack_noscan_run exception row
    "error",
}

# decision_source values emitted anywhere in the stack. Enforced at scan_state
# write time. Additive via register_decision_source().
_DECISION_SOURCES: set[str] = {
    # scan_state.DECISION_SOURCES (adaptive-scan RoundResult)
    "gemini_batch",
    "gemini_per_image",
    "gemini_failed",
    "dry_run_stub",
    "manual",
    # score_anchor_presence._base_quality_and_source
    "missing_chip",
    "manual_template",
    # sequence / matrix scorers
    "gemini_sequence",
    "gemini_matrix",
    "sequence_pending",
}

# Back-compat / default: the decision_source set the adaptive-scan ambiguity
# rule counted as "failed" before ISSUE-05 made it scorer-parameterized.
# scan_decision.failure_pct / decide_next_action default to this, preserving
# today's behavior byte-for-byte.
DEFAULT_FAILURE_DECISION_SOURCES: frozenset[str] = frozenset({"gemini_failed"})


def register_quality_flag(name: str) -> None:
    """Additively register a new quality_flag value so writes stop rejecting it."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"quality_flag must be a non-empty str, got {name!r}")
    _QUALITY_FLAGS.add(name)


def register_decision_source(name: str) -> None:
    """Additively register a new decision_source value so writes stop rejecting it."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"decision_source must be a non-empty str, got {name!r}")
    _DECISION_SOURCES.add(name)


def known_quality_flags() -> frozenset[str]:
    return frozenset(_QUALITY_FLAGS)


def known_decision_sources() -> frozenset[str]:
    return frozenset(_DECISION_SOURCES)


def validate_quality_flag(name: str) -> None:
    if name not in _QUALITY_FLAGS:
        raise ValueError(
            f"unknown quality_flag {name!r}; register it via "
            f"presence_scorer.register_quality_flag(). Known: {sorted(_QUALITY_FLAGS)}"
        )


def validate_decision_source(name: str) -> None:
    if name not in _DECISION_SOURCES:
        raise ValueError(
            f"unknown decision_source {name!r}; register it via "
            f"presence_scorer.register_decision_source(). Known: {sorted(_DECISION_SOURCES)}"
        )


def validate_emission(quality_flag: str, decision_source: str) -> None:
    """Write-time vocab check where raw scorer output enters a call-site's write path.

    Sequence-shaped results carry one (quality_flag, decision_source) pair per
    result; matrix-shaped results carry one per cell observation. Call-sites
    validate at ingest so an unregistered vocabulary value fails loudly instead
    of flowing into CSVs. Rows the call-site synthesizes itself (pending/error
    sentinels, degraded fill rows) are its own vocabulary, not a scorer
    emission, and are exempt.
    """
    validate_quality_flag(quality_flag)
    validate_decision_source(decision_source)


# ---------------------------------------------------------------------------
# Contract dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Pick:
    """One chip to score, post-download (has a resolved chip_path / actual_zoom).

    Mirrors the design-doc contract `Pick(chip_path, capture_date, version,
    actual_zoom)`. `index` carries the call-site ordinal (BatchPick.chip_index /
    SequenceDatePick.date_index / MatrixDatePick.date_index) so observations can
    be mapped back to the originating pick without positional guessing.

    NOTE: this is NOT `scan_state.Pick` (the pre-download "what to fetch"
    record). Call-sites build a `presence_scorer.Pick` from their native pick +
    the download outcome before scoring.
    """

    chip_path: str
    capture_date: str = ""
    version: str | int = ""
    actual_zoom: int | None = None
    index: int = 0


@dataclass
class PresenceObservation:
    """One scorer verdict for one chip.

    Mirrors the design-doc contract `PresenceObservation(pv_present, pv_score,
    quality_flag, decision_source)`. `pv_present is None` == abstain. `pv_score`
    is the codebase's `confidence` under the contract's name — call-sites that
    persist `confidence` (scan_state.RoundResult) read `.pv_score`.

    `index` / `capture_date` / `evidence` / `notes` / `error` are carried so a
    call-site can rebuild its native persisted record byte-identically (e.g.
    scan_state.RoundResult needs evidence + notes + chip_index==index).

    NOTE: distinct from `scripts.temporal.geid_temporal_common.PresenceObservation`
    (a different, install-date-inference record). Import the right one.
    """

    pv_present: bool | None
    pv_score: float | None
    quality_flag: str
    decision_source: str
    index: int = 0
    capture_date: str = ""
    evidence: str = ""
    notes: str = ""
    error: str | None = None


def validate_observation(obs: PresenceObservation) -> None:
    """Raise ValueError if the observation carries unregistered vocabulary."""
    validate_quality_flag(obs.quality_flag)
    validate_decision_source(obs.decision_source)


# ---------------------------------------------------------------------------
# Scorer protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PresenceScorer(Protocol):
    """Uniform scoring surface every call-site routes through.

    Attributes are declarations, not behavior:
      * `name` — registry identity.
      * `failure_decision_sources` — decision_source values this scorer emits to
        mean "no verdict / failed"; threaded into scan_decision's ambiguity rule.
      * `quality_flags` / `decision_sources` — the vocabulary this scorer can
        emit (a subset of the module registries).

    `score(picks, *, config, ...)` is the canonical per-chip (batch) entrypoint
    returning `list[PresenceObservation]`. Concrete scorers may additionally
    expose native raw callables (see `GeminiPresenceScorer.batch/sequence/matrix`)
    that preserve the existing Gemini signatures for the already-`scorer=`-seamed
    call-sites.
    """

    name: str
    failure_decision_sources: frozenset[str]
    quality_flags: frozenset[str]
    decision_sources: frozenset[str]

    def score(
        self, picks: list[Pick], *, config: Any, **kwargs: Any
    ) -> list[PresenceObservation]: ...


# ---------------------------------------------------------------------------
# Gemini implementation (lazy import — no Gemini deps at module import)
# ---------------------------------------------------------------------------


class GeminiPresenceScorer:
    """Wraps the production Gemini callables behind the seam.

    * `.score(picks, *, config, ...)` — canonical batch path: adapts seam `Pick`
      -> `BatchPick`, calls `score_batch_with_fallback`, adapts
      `GeminiObservation` -> `PresenceObservation`.
    * `.batch` / `.sequence` / `.matrix` — the raw Gemini callables, lazily
      imported, with their EXACT existing signatures. The two already-seamed
      call-sites (score_target_sequence.score_target_sequences,
      score_chip_group_matrix.score_chip_group_matrices) set their `scorer=`
      default to `.sequence` / `.matrix` so their native result types
      (GeminiSequenceResult / list[GeminiMatrixObservation]) and CSV output stay
      byte-identical.
    """

    name = "gemini"
    failure_decision_sources = frozenset({"gemini_failed"})
    quality_flags = frozenset({"usable", "ambiguous", "unusable", "sequence_failed"})
    decision_sources = frozenset(
        {
            "gemini_batch",
            "gemini_per_image",
            "gemini_failed",
            "gemini_sequence",
            "gemini_matrix",
        }
    )

    @property
    def batch(self) -> Callable[..., list["GeminiObservation"]]:
        from scripts.validation.gemini_solar_image_review import score_batch_with_fallback

        return score_batch_with_fallback

    @property
    def sequence(self) -> Callable[..., "GeminiSequenceResult"]:
        from scripts.validation.gemini_solar_image_review import score_single_target_sequence

        return score_single_target_sequence

    @property
    def matrix(self) -> Callable[..., list[Any]]:
        from scripts.validation.gemini_solar_image_review import score_target_date_matrix

        return score_target_date_matrix

    def prompt_config_fingerprint(self, mode: str, config: Any) -> dict[str, Any]:
        """Instruction + config identity for the scoring-provenance hash (ISSUE-06).

        Returns the exact prompt templates that construct the request for `mode`
        plus the config knobs that change the *instruction the model executes*.
        Lazily imports the prompt constants so this stays free of Gemini/network
        deps until a provenance-wrapped scorer actually hashes.

        Batch mode lists all three templates its retry policy can render — the
        batch template, the census-calibration suffix, and the per-image fallback
        prompt (`DEFAULT_PROMPT`) — because a change to any of them changes what
        some scored chip in a batch was actually asked. `base_url` / `api_key` /
        `native_path` / `timeout` are gateway/transport identity, not instruction
        identity, and are deliberately EXCLUDED (and the api_key must never enter a
        hash input payload). Tolerates `config=None` (fields resolve to None).
        """
        from scripts.validation.gemini_solar_image_review import (
            BATCH_CENSUS_CALIBRATION_SUFFIX,
            BATCH_PROMPT_TEMPLATE,
            DEFAULT_PROMPT,
            MATRIX_PROMPT_TEMPLATE,
            SEQUENCE_PROMPT_TEMPLATE,
        )

        templates = {
            "batch": [BATCH_PROMPT_TEMPLATE, BATCH_CENSUS_CALIBRATION_SUFFIX, DEFAULT_PROMPT],
            "sequence": [SEQUENCE_PROMPT_TEMPLATE],
            "matrix": [MATRIX_PROMPT_TEMPLATE],
        }.get(mode, [])
        payload: dict[str, Any] = {
            "scorer": "gemini",
            "mode": mode,
            "prompt_templates": templates,
            "model": getattr(config, "model", None),
            "api_format": getattr(config, "api_format", None),
            "max_tokens_per_chip": getattr(config, "max_tokens_per_chip", None),
            "thinking_level": getattr(config, "thinking_level", None),
            "thinking_budget": getattr(config, "thinking_budget", None),
        }
        if mode == "matrix":
            payload["matrix_json_mode"] = getattr(config, "matrix_json_mode", None)
        return payload

    @staticmethod
    def _to_observation(obs: "GeminiObservation") -> PresenceObservation:
        return PresenceObservation(
            pv_present=obs.pv_present,
            pv_score=obs.confidence,
            quality_flag=obs.quality_flag,
            decision_source=obs.decision_source,
            index=obs.chip_index,
            evidence=obs.evidence,
            notes=obs.notes,
            error=obs.error,
        )

    def score(
        self,
        picks: list[Pick],
        *,
        config: Any,
        audit_writer: Callable[[dict[str, Any]], None] | None = None,
        census_mid_date_iso: str | None = None,
        routing_salt: str | None = None,
        poster: Callable[..., dict[str, Any]] | None = None,
    ) -> list[PresenceObservation]:
        from scripts.validation.gemini_solar_image_review import BatchPick

        batch_picks = [
            BatchPick(
                chip_index=pick.index or (i + 1),
                chip_path=Path(pick.chip_path),
                capture_date=pick.capture_date,
                version=pick.version,
                actual_zoom=pick.actual_zoom,
            )
            for i, pick in enumerate(picks)
        ]
        observations = self.batch(
            batch_picks,
            config=config,
            audit_writer=audit_writer,
            poster=poster,
            census_mid_date_iso=census_mid_date_iso,
            routing_salt=routing_salt,
        )
        by_index = {p.index or (i + 1): p for i, p in enumerate(picks)}
        results: list[PresenceObservation] = []
        for obs in observations:
            out = self._to_observation(obs)
            pick = by_index.get(obs.chip_index)
            if pick is not None:
                out.capture_date = pick.capture_date
            results.append(out)
        return results


# ---------------------------------------------------------------------------
# Dry-run stub (mirrors run_adaptive_scan.dry_run_gemini_result semantics)
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


@dataclass
class DryRunPresenceScorer:
    """Deterministic offline stub. Reproduces `dry_run_gemini_result` exactly.

    Constructed once per anchor with the anchor's resolved dry-run profile
    (`label` + optional `install_date`), mirroring how `run_adaptive_scan`
    derives a `DryRunProfile` per anchor today. `.score` maps each pick to a
    synthetic verdict:

      * label == "all_present"  -> pv_present True
      * label == "all_absent"   -> pv_present False
      * otherwise (appears_YYYY)-> pv_present = pick_date >= install_date

    with pv_score=0.95, quality_flag="usable", decision_source="dry_run_stub",
    evidence=f"stub evidence for {label}", notes=f"dry_run profile={label}".
    """

    label: str
    install_date: date | None = None
    name: str = field(default="dry_run", init=False)
    failure_decision_sources: frozenset[str] = field(
        default=frozenset(), init=False
    )
    quality_flags: frozenset[str] = field(default=frozenset({"usable"}), init=False)
    decision_sources: frozenset[str] = field(
        default=frozenset({"dry_run_stub"}), init=False
    )

    def prompt_config_fingerprint(self, mode: str, config: Any = None) -> dict[str, Any]:
        """Instruction identity for the dry-run stub (ISSUE-06).

        The stub has no prompt and reads no `config`; its verdicts are a pure
        function of the anchor's deterministic `label` + `install_date`, so those
        two fields fully describe "what produced this verdict".
        """
        return {
            "scorer": "dry_run",
            "mode": mode,
            "label": self.label,
            "install_date": str(self.install_date),
        }

    def _pv_present(self, capture_date: str) -> bool:
        if self.label == "all_present":
            return True
        if self.label == "all_absent":
            return False
        if self.install_date is None:
            raise ValueError(
                f"dry-run profile label {self.label!r} requires an install_date"
            )
        return _parse_iso(capture_date) >= self.install_date

    def score(
        self, picks: list[Pick], *, config: Any = None, **_kwargs: Any
    ) -> list[PresenceObservation]:
        results: list[PresenceObservation] = []
        for i, pick in enumerate(picks):
            results.append(
                PresenceObservation(
                    pv_present=self._pv_present(pick.capture_date),
                    pv_score=0.95,
                    quality_flag="usable",
                    decision_source="dry_run_stub",
                    index=pick.index or (i + 1),
                    capture_date=pick.capture_date,
                    evidence=f"stub evidence for {self.label}",
                    notes=f"dry_run profile={self.label}",
                )
            )
        return results


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SCORER_FACTORIES: dict[str, Callable[..., PresenceScorer]] = {}


def register_scorer(name: str, factory: Callable[..., PresenceScorer]) -> None:
    """Register (or replace) a scorer factory under `name`.

    `factory(**kwargs)` must return an object satisfying the `PresenceScorer`
    protocol. Registration is additive across the process; later registrations
    override earlier ones for the same name.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"scorer name must be a non-empty str, got {name!r}")
    _SCORER_FACTORIES[name] = factory


def get_scorer(name: str, **kwargs: Any) -> PresenceScorer:
    """Construct the scorer registered under `name`; unknown name -> ValueError."""
    try:
        factory = _SCORER_FACTORIES[name]
    except KeyError:
        raise ValueError(
            f"unknown scorer {name!r}; registered scorers: {sorted(_SCORER_FACTORIES)}"
        ) from None
    return factory(**kwargs)


def available_scorers() -> list[str]:
    return sorted(_SCORER_FACTORIES)


def _make_gemini(**_kwargs: Any) -> GeminiPresenceScorer:
    return GeminiPresenceScorer()


def _make_dry_run(*, label: str, install_date: date | None = None) -> DryRunPresenceScorer:
    return DryRunPresenceScorer(label=label, install_date=install_date)


def _make_dinov3_frozen(**kwargs: Any) -> PresenceScorer:
    """Construct the frozen DINOv3-L-SAT scorer (ISSUE-03).

    The heavy module (torch / timm / PIL) is imported LAZILY here, inside the
    factory body, so importing ``presence_scorer`` stays free of torch — the swap
    is selectable behind the seam without paying the FM import cost until a caller
    actually asks for ``dinov3_frozen``.
    """
    from scripts.temporal.dinov3_scorer import Dinov3PresenceScorer

    return Dinov3PresenceScorer(**kwargs)


register_scorer("gemini", _make_gemini)
register_scorer("dry_run", _make_dry_run)
register_scorer("dinov3_frozen", _make_dinov3_frozen)
