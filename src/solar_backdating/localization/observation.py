"""``TargetLocalizationObservation`` — the semantic-localization layer's output
contract (PRD §5.3, ``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``).

**What this layer is, and is not** (§5.1 of the PRD — repeated here because this
is the single easiest place to misuse the schema): the 07-17 recenter pilot
(``docs/replan_v2/DATA-fullscan-run2-recenter-pilot-2026-07-17.md``) returned a
NO-GO on SP+LightGlue re-centering *as a placement-error rescue* — blind bucket
(67.6% of the corpus) came back 0/472 past the half-box tolerance, and the
pilot's own finding was that "registration and placement are orthogonal failure
modes": the dominant failure shape is a crosshair sitting on a driveway or lawn
that registers *perfectly cleanly* to the wrong spot, so a registration-quality
gate cannot see it coming. This module's cascade is **not** that rescue. Its
job is narrower and different: **training-label decontamination + uninformative
gating** — stopping a "PV not found because we never actually looked at the
right roof" observation from being fed to a decoder as a confirmed ``absent``.
Do not repurpose a low-``abstain`` rate here as evidence the blind bucket got
fixed; that question is closed NO-GO and this schema does not reopen it.

Hard invariant this schema exists to enforce (§3.3): ``absent`` is only a valid
*effective* label when ``target_localized=True``. When the localization
cascade cannot confirm the building/roof it was looking for, the correct
downstream label is ``uninformative`` — never a silent fallback to ``absent``.
Conversely, when two localization signals disagree, or a correction pushes the
projected target out of the configured bounds, the cascade must abstain
(``abstain=True`` -> uninformative), never guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping

SCHEMA_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# Enumerations. Plain string tuples (not enum.Enum) to match this repo's
# existing seam/emissions convention (solar_backdating.estimators.emissions
# SYMBOLS/STATES) -- cheap to serialize, cheap to extend, membership-checked
# in __post_init__ rather than relying on a type system the dataclass fields
# don't otherwise enforce.
# --------------------------------------------------------------------------- #

#: §5.2 "只允许有界 translation/similarity" -- DINO/aphek subpixel correction is
#: explicitly KILLed (2026-07-08); the cascade may only ever emit one of these.
TRANSFORM_TYPES: tuple[str, ...] = ("identity", "translation", "similarity")

#: §5.3 failure taxonomy. "" (empty string) means no failure -- the
#: observation localized cleanly. The three ``FORCED_ABSTAIN_REASONS`` below
#: are the §5.3 "两信号冲突或偏移超界" / ROI-encroachment cases that MUST
#: co-occur with ``abstain=True`` (enforced in ``__post_init__``); the other
#: non-empty reasons describe why localization stopped short of that but do
#: not by themselves force ``abstain`` (they instead drive ``target_localized
#: =False``, which is what ``effective_label`` gates ``absent`` on).
FAILURE_REASONS: tuple[str, ...] = (
    "",
    "building_not_found",
    "roof_plane_not_matched",
    "transform_conflict",
    "transform_out_of_bounds",
    "roi_expansion_clipped",
    "low_confidence",
    "dark_zone",  # ISSUE-24 weak-lock terminology: no reliable keypoint lock
    "stage_not_implemented",  # placeholder emitted only by stub-stage callers
)

#: §5.3 "两信号冲突或偏移超界 ⇒ abstain" -- a TLO carrying one of these reasons
#: must have ``abstain=True`` and ``target_localized=False`` (checked below).
FORCED_ABSTAIN_REASONS: frozenset[str] = frozenset(
    {"transform_conflict", "transform_out_of_bounds", "roi_expansion_clipped"}
)

#: Which cascade stage produced the observation (§5.2/§6 R2). ``identity`` is
#: the "trust TFW/Run3 geometry" default stage (no correction attempted);
#: ``phase_correlation`` and ``weak_lock`` are the two escalation tiers this
#: PRD scopes (§5.2), wired as stubs in this skeleton.
CASCADE_STAGES: tuple[str, ...] = ("identity", "phase_correlation", "weak_lock")

EFFECTIVE_LABELS: tuple[str, ...] = ("present", "absent", "uninformative")

# --------------------------------------------------------------------------- #
# Bounded-correction thresholds (§5.3 "偏移超界"). These are the "参数带上界
# 配置" the PRD calls for -- module-level defaults rather than a per-instance
# config plumbed through every constructor call, since __post_init__ needs a
# fixed bound to validate against at construction time. A future stage that
# wants a different bound should build its TLO from
# ``transform_within_bounds`` with its own thresholds *before* constructing
# the frozen dataclass (which re-validates against these same defaults) --
# i.e. these are the schema's floor, not a tunable stage parameter today.
# --------------------------------------------------------------------------- #
DEFAULT_MAX_TRANSLATION_M = 5.0
DEFAULT_SCALE_BOUNDS: tuple[float, float] = (0.85, 1.15)
DEFAULT_MAX_ROTATION_DEG = 8.0


def transform_within_bounds(
    transform_type: str,
    transform_params: Mapping[str, float],
    *,
    max_translation_m: float = DEFAULT_MAX_TRANSLATION_M,
    scale_bounds: tuple[float, float] = DEFAULT_SCALE_BOUNDS,
    max_rotation_deg: float = DEFAULT_MAX_ROTATION_DEG,
) -> bool:
    """True iff ``transform_params`` is within the configured bounds for
    ``transform_type``. ``identity`` is always within bounds (no correction to
    bound). Missing keys default to the identity value for that component
    (``dx_m``/``dy_m``=0.0, ``scale``=1.0, ``rotation_deg``=0.0) so a
    translation-only transform's absent ``scale``/``rotation_deg`` never trips
    the similarity bounds.
    """
    if transform_type == "identity":
        return True
    dx_m = float(transform_params.get("dx_m", 0.0))
    dy_m = float(transform_params.get("dy_m", 0.0))
    if math.hypot(dx_m, dy_m) > max_translation_m:
        return False
    if transform_type == "similarity":
        scale = float(transform_params.get("scale", 1.0))
        if not (scale_bounds[0] <= scale <= scale_bounds[1]):
            return False
        rotation_deg = float(transform_params.get("rotation_deg", 0.0))
        if abs(rotation_deg) > max_rotation_deg:
            return False
    return True


def _coerce_polygon(
    value: Any,
) -> str | tuple[tuple[float, float], ...] | None:
    """Normalize a polygon-ish value to the two accepted representations: a
    WKT string, or a tuple of (lon, lat) coordinate tuples. ``None`` means
    "not computed" (identity stage may leave this unset -- it trusts the
    caller-supplied nominal footprint is unchanged, so there is nothing to
    project)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    coords = tuple((float(x), float(y)) for x, y in value)
    return coords


@dataclass(frozen=True)
class TargetLocalizationObservation:
    """One ``(anchor_id, capture_date)`` observation's semantic-localization +
    roof-geometry-registration verdict (PRD §5.3).

    Field groups:

    * Identity: ``anchor_id``, ``capture_date``.
    * Semantic association layer (§5.2 row 1): ``building_found``,
      ``roof_plane_matched``.
    * Geometric registration layer (§5.2 row 2): ``transform_type``,
      ``transform_params``, ``registration_confidence``,
      ``shift_uncertainty_m``, ``roi_expansion_m``, ``roi_expansion_clipped``.
    * Combined verdict: ``target_localized``, ``projected_target_polygon``,
      ``failure_reason``, ``abstain``.
    * Provenance: ``cascade_stage`` -- which stage in the phase-correlation ->
      weak-lock -> RANSAC cascade (§5.2) produced this observation.

    ``transform_params`` is stored as a read-only ``MappingProxyType`` (a plain
    ``dict`` field would defeat ``frozen=True`` -- the dataclass would still
    forbid reassigning the field, but not mutating the dict it points to).

    Invariants (raised as ``ValueError`` in ``__post_init__``; see each check's
    inline comment for the PRD clause it encodes):
    """

    anchor_id: str
    capture_date: date
    building_found: bool
    roof_plane_matched: bool
    target_localized: bool
    transform_type: str
    transform_params: Mapping[str, float]
    registration_confidence: float | None
    shift_uncertainty_m: float | None
    projected_target_polygon: str | tuple[tuple[float, float], ...] | None
    failure_reason: str
    cascade_stage: str
    abstain: bool
    roi_expansion_m: float = 0.0
    roi_expansion_clipped: bool = False

    def __post_init__(self) -> None:
        # Freeze the mapping field (see class docstring) and normalize the
        # polygon field to one of its two accepted shapes.
        object.__setattr__(self, "transform_params", MappingProxyType(dict(self.transform_params)))
        object.__setattr__(
            self, "projected_target_polygon", _coerce_polygon(self.projected_target_polygon)
        )

        if self.transform_type not in TRANSFORM_TYPES:
            raise ValueError(
                f"transform_type={self.transform_type!r} not in {TRANSFORM_TYPES}"
            )
        if self.cascade_stage not in CASCADE_STAGES:
            raise ValueError(
                f"cascade_stage={self.cascade_stage!r} not in {CASCADE_STAGES}"
            )
        if self.failure_reason not in FAILURE_REASONS:
            raise ValueError(
                f"failure_reason={self.failure_reason!r} not in {FAILURE_REASONS}"
            )

        # §5.2 semantic layer precedes geometric layer: can't match a roof
        # plane on a building that was never found.
        if self.roof_plane_matched and not self.building_found:
            raise ValueError("roof_plane_matched=True requires building_found=True")

        # §3.3 core red line: absent-eligibility (target_localized=True) can
        # never be reached without both the building and its roof plane
        # having been confirmed.
        if self.target_localized and not self.building_found:
            raise ValueError("target_localized=True requires building_found=True")
        if self.target_localized and not self.roof_plane_matched:
            raise ValueError("target_localized=True requires roof_plane_matched=True")

        # §5.3: a forced-abstain failure reason must actually abstain, and an
        # abstaining observation cannot simultaneously claim to be localized.
        if self.failure_reason in FORCED_ABSTAIN_REASONS:
            if not self.abstain:
                raise ValueError(
                    f"failure_reason={self.failure_reason!r} requires abstain=True"
                )
            if self.target_localized:
                raise ValueError(
                    f"failure_reason={self.failure_reason!r} requires target_localized=False"
                )

        # §5.3 uncertainty-expansion escape hatch: expanding the ROI into a
        # neighboring roof plane is exactly the "偏移超界"-adjacent case that
        # must abstain rather than silently widen the localized footprint.
        if self.roi_expansion_clipped and not self.abstain:
            raise ValueError("roi_expansion_clipped=True requires abstain=True")

        # abstain=True must either give up localization outright, or -- if it
        # somehow still claims target_localized=True -- explain itself with a
        # failure_reason (team-lead spec: "abstain=True ⇒ target_localized=
        # False 或带 failure_reason").
        if self.abstain and self.target_localized and not self.failure_reason:
            raise ValueError(
                "abstain=True with target_localized=True requires a non-empty failure_reason"
            )

        # §5.3 "偏移超界 ⇒ abstain": a non-identity transform outside the
        # configured bounds may never be used to localize; it must instead
        # abstain (uninformative), with a reason that says so.
        if self.transform_type != "identity" and not transform_within_bounds(
            self.transform_type, self.transform_params
        ):
            if not (self.abstain and not self.target_localized):
                raise ValueError(
                    f"transform_params={dict(self.transform_params)!r} for "
                    f"transform_type={self.transform_type!r} exceed the configured "
                    f"bounds (max_translation_m={DEFAULT_MAX_TRANSLATION_M}, "
                    f"scale_bounds={DEFAULT_SCALE_BOUNDS}, "
                    f"max_rotation_deg={DEFAULT_MAX_ROTATION_DEG}); must abstain "
                    "instead of localizing on an out-of-bounds correction"
                )
            if self.failure_reason not in FORCED_ABSTAIN_REASONS:
                raise ValueError(
                    "an out-of-bounds transform that abstains must set "
                    f"failure_reason to one of {sorted(FORCED_ABSTAIN_REASONS)}, "
                    f"got {self.failure_reason!r}"
                )

        # identity stage never corrects -- the whole point is "trust TFW/Run3
        # geometry as-is" (§9 item 2).
        if self.cascade_stage == "identity" and self.transform_type != "identity":
            raise ValueError("cascade_stage='identity' requires transform_type='identity'")

        if self.registration_confidence is not None and not (
            0.0 <= self.registration_confidence <= 1.0
        ):
            raise ValueError(
                f"registration_confidence={self.registration_confidence!r} out of [0, 1]"
            )
        if self.shift_uncertainty_m is not None and self.shift_uncertainty_m < 0.0:
            raise ValueError(f"shift_uncertainty_m={self.shift_uncertainty_m!r} < 0")
        if self.roi_expansion_m < 0.0:
            raise ValueError(f"roi_expansion_m={self.roi_expansion_m!r} < 0")

    # ------------------------------------------------------------------ #
    # Serialization                                                       #
    # ------------------------------------------------------------------ #

    def to_json_record(self) -> dict[str, Any]:
        """JSON-safe dict (one JSONL line in ``replay_localization_cascade.py``'s
        output). Round-trips exactly through ``from_json_record``."""
        polygon = self.projected_target_polygon
        if isinstance(polygon, tuple):
            polygon_json: Any = [list(pt) for pt in polygon]
        else:
            polygon_json = polygon
        return {
            "schema_version": SCHEMA_VERSION,
            "anchor_id": self.anchor_id,
            "capture_date": self.capture_date.isoformat(),
            "building_found": self.building_found,
            "roof_plane_matched": self.roof_plane_matched,
            "target_localized": self.target_localized,
            "transform_type": self.transform_type,
            "transform_params": dict(self.transform_params),
            "registration_confidence": self.registration_confidence,
            "shift_uncertainty_m": self.shift_uncertainty_m,
            "projected_target_polygon": polygon_json,
            "failure_reason": self.failure_reason,
            "cascade_stage": self.cascade_stage,
            "abstain": self.abstain,
            "roi_expansion_m": self.roi_expansion_m,
            "roi_expansion_clipped": self.roi_expansion_clipped,
        }

    @staticmethod
    def from_json_record(d: Mapping[str, Any]) -> "TargetLocalizationObservation":
        """Inverse of ``to_json_record``. Does not check ``schema_version``
        against the current ``SCHEMA_VERSION`` (no prior version has shipped
        yet) -- a future breaking change to this schema should add that check
        here."""
        return TargetLocalizationObservation(
            anchor_id=d["anchor_id"],
            capture_date=date.fromisoformat(d["capture_date"]),
            building_found=bool(d["building_found"]),
            roof_plane_matched=bool(d["roof_plane_matched"]),
            target_localized=bool(d["target_localized"]),
            transform_type=d["transform_type"],
            transform_params=dict(d.get("transform_params") or {}),
            registration_confidence=d.get("registration_confidence"),
            shift_uncertainty_m=d.get("shift_uncertainty_m"),
            projected_target_polygon=d.get("projected_target_polygon"),
            failure_reason=d.get("failure_reason", ""),
            cascade_stage=d["cascade_stage"],
            abstain=bool(d["abstain"]),
            roi_expansion_m=float(d.get("roi_expansion_m", 0.0)),
            roi_expansion_clipped=bool(d.get("roi_expansion_clipped", False)),
        )


# --------------------------------------------------------------------------- #
# §3.3 label gate: absent is only ever valid when target_localized=True.
# --------------------------------------------------------------------------- #

#: Raw Gemini/quality_flag tokens this repo's banked panel currently uses that
#: collapse to "uninformative" before the localization gate is even applied
#: (quality-side uninformative, orthogonal to placement). Kept narrow and
#: explicit rather than an "anything unrecognized" catch-all working by
#: coincidence -- extend this set deliberately if a new token appears.
_QUALITY_UNINFORMATIVE_FLAGS = frozenset({"unusable"})
_VERDICT_UNINFORMATIVE_TOKENS = frozenset({"uninformative", "corrupt", "unusable", "ambiguous"})


def _legacy_three_state(gemini_verdict: str, quality_flag: str) -> str:
    """Three-state label with no localization gate applied -- i.e. the label
    this repo already computed before this layer existed (RUN 3's
    present/absent/uninformative(+corrupt) mapping, §3.3). Quality-side
    uninformative (``quality_flag == "unusable"``) wins over the verdict."""
    if quality_flag in _QUALITY_UNINFORMATIVE_FLAGS:
        return "uninformative"
    verdict = (gemini_verdict or "").strip().lower()
    if verdict in _VERDICT_UNINFORMATIVE_TOKENS:
        return "uninformative"
    if verdict in ("present", "absent"):
        return verdict
    return "uninformative"  # unrecognized token: fail closed, never guess present/absent


def verdict_token(pv_present: bool | None, quality_flag: str | None = None) -> str:
    """Canonical ``pv_present`` bool -> three-state verdict token mapping.

    RUN 3's raw per-frame verdict, as stored in ``scan_state.json``'s
    ``results[].pv_present`` (see
    ``solar_backdating.eval.scan_state_io``'s module docstring: JSON
    ``true``/``false``/``null``), is a Python ``bool | None``, not a string.
    ``effective_label`` (and everything downstream of it -- the R0 manifest
    generator's ``label_v1`` column, this cascade's replay script) needs the
    string token form. This function is the **single canonical mapping** from
    the RUN 3 bool to that token: ``True`` -> ``"present"``, ``False`` ->
    ``"absent"``, ``None``/missing -> ``"uninformative"`` (fail-closed --
    mirrors ``_legacy_three_state``'s unrecognized-token handling: an
    observation with no verdict at all is never guessed as present or
    absent).

    Callers MUST import this function rather than inlining their own
    bool->token map -- two independent inline mappings (one in the manifest
    generator, one in the replay script) is exactly the kind of caliber
    drift this schema exists to prevent.

    ``quality_flag`` is accepted for call-site convenience (a caller often
    has both fields in hand at once) but is **not** interpreted here --
    quality-side uninformative handling (``quality_flag == "unusable"``) is
    ``_legacy_three_state``'s / ``effective_label``'s job, not this
    function's, to keep this mapping single-purpose (bool -> token only).
    The parameter is accepted and ignored so a call site doesn't need an
    `if quality_flag is unusable` branch of its own before reaching for the
    canonical present/absent/uninformative gate in ``effective_label``.
    """
    del quality_flag  # accepted for call-site convenience; not interpreted here (see docstring)
    if pv_present is True:
        return "present"
    if pv_present is False:
        return "absent"
    return "uninformative"


def effective_label(
    gemini_verdict: str,
    quality_flag: str,
    tlo: "TargetLocalizationObservation | None",
) -> dict[str, Any]:
    """Apply the §3.3 localization gate to a raw Gemini verdict.

    Returns ``{"label": ..., "localization_pending": bool, "gated_reason": str}``
    where ``label`` is one of ``EFFECTIVE_LABELS``.

    Contract:

    * ``tlo is None`` -- the localization layer has not run for this
      observation yet (e.g. every RUN 3 row today, pre-R2). Per the
      team-lead brief this **preserves legacy semantics unchanged**
      (``label = _legacy_three_state(...)``) rather than guessing a gate
      outcome, and sets ``localization_pending=True`` so callers can tell
      "vetted, passed the gate" apart from "not vetted yet". This is a
      deliberate compatibility shim, not an endorsement that ungated
      ``absent`` rows are safe -- it exists so this function can be dropped
      into the existing 311k-observation panel today without silently
      rewriting every row's label.
    * ``tlo is not None`` -- the gate is live. If the legacy label would be
      ``absent`` but the TLO did not confirm a localized target
      (``not tlo.target_localized`` or ``tlo.abstain``), the label is
      downgraded to ``uninformative`` (``gated_reason`` explains why:
      ``"building_not_found"``, ``"roof_plane_not_matched"``, ``"abstain"``,
      or the TLO's own ``failure_reason`` if more specific). ``present`` and
      ``uninformative`` legacy labels pass through unchanged -- the §3.3 red
      line is specifically about `absent` mislabeling placement failures,
      not about gating positive evidence.
    """
    base = _legacy_three_state(gemini_verdict, quality_flag)

    if tlo is None:
        return {"label": base, "localization_pending": True, "gated_reason": ""}

    if base == "absent" and (tlo.abstain or not tlo.target_localized):
        if tlo.abstain:
            reason = tlo.failure_reason or "abstain"
        elif not tlo.building_found:
            reason = "building_not_found"
        elif not tlo.roof_plane_matched:
            reason = "roof_plane_not_matched"
        else:
            reason = tlo.failure_reason or "not_localized"
        return {"label": "uninformative", "localization_pending": False, "gated_reason": reason}

    return {"label": base, "localization_pending": False, "gated_reason": ""}
