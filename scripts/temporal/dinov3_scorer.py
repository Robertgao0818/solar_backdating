"""Frozen DINOv3-L-SAT ``PresenceScorer`` scaffold (ISSUE-03, PRD §D2/D3/D4).

This is *tracer slice 3* of the DINOv3 scorer-backbone swap: it proves the
mechanism end-to-end — a `PresenceScorer` backed by a **frozen**
DINOv3-L-SAT encoder (timm ``vit_large_patch16_dinov3.sat493m``, 303M, ungated)
plus a *placeholder* light head — **before** the head is trained (head training
is ISSUE-04). Scores are therefore reproducible-but-meaningless: the tests here
assert shape / ordering / determinism, never accuracy.

Mechanism
---------
* **Frozen encoder.** The backbone is loaded once, ``requires_grad_(False)`` +
  ``.eval()``, and every forward runs under ``torch.inference_mode()``. A frozen
  FM cannot be polluted by noisy pseudo-labels (the failure ISSUE-04 must avoid).
* **Anchor conditioning.** The anchor sits at chip centre by construction. We
  upscale the chip to ``input_size`` (a constructor parameter — the upscaling
  *policy* is validated in ISSUE-04's ablation, NOT frozen here), forward it
  through the encoder, drop the ``num_prefix_tokens`` (cls + register) tokens,
  reshape the remaining patch tokens to a square grid, and **mean-pool the
  centre ``center_pool_k`` × ``center_pool_k`` patch tokens** as the
  anchor-conditioned feature. No drawn marker (the marker was for the LLM to
  read; spatial pooling replaces it).
* **Light head.** A tiny ``nn.Linear`` embedding-dim → 3 logits
  (present / absent / unusable). For this slice the head is a **fixed-seed
  placeholder** (``torch.Generator`` with an explicit seed) so scores are
  bit-reproducible across independently constructed scorers and across
  processes. ISSUE-04 replaces it with a trained ``head_checkpoint``.
* **Mapping.** softmax over the 3 classes; ``pv_score`` = present-class
  probability (finite float, uncalibrated placeholder band — calibration is
  ISSUE-04). argmax present → ``(True, "usable")``, absent →
  ``(False, "usable")``, unusable → ``(None, "unusable")``.
  ``decision_source="dinov3_frozen"`` for every scored chip. An unreadable /
  missing chip does not crash the batch — it emits an abstain observation with
  ``decision_source="dinov3_failed"``, ``quality_flag="missing_chip"``,
  ``pv_present=None`` and an ``error`` message.

Two scoring surfaces
--------------------
* ``score(picks, *, config, ...)`` — the canonical seam entrypoint returning
  ``list[PresenceObservation]`` (used by the dry-run/sequence-shaped callers and
  the unit tests).
* ``batch(picks, *, config, ...)`` — the raw callable the **adaptive-scan real
  path** (``run_adaptive_scan.execute_round_real``) invokes; it returns
  ``GeminiObservation`` records with the exact fields that path maps onto
  ``RoundResult``, so the DINOv3 scorer is a drop-in there too.

Both surfaces funnel through one per-chip method, ``_observe``, so their answers
and their abstain handling are identical, and so tests can monkeypatch a single
method to stub inference.

Weights resolution (never in the repo)
--------------------------------------
timm downloads the backbone from the HuggingFace Hub on first load (~1.2 GB,
ungated). To keep weights **out of the git tree and under ``~/zasolar_data/``**
(subrepo data-discipline rule / PRD §D8), the constructor takes
``weights_cache_dir`` (default ``~/zasolar_data/models/dinov3_sat/hf_cache``,
overridable via the ``SOLAR_DINOV3_WEIGHTS_DIR`` env var). At model-load time it
is passed as ``cache_dir`` straight to ``timm.create_model`` (threaded to
``huggingface_hub.hf_hub_download``), which is authoritative regardless of import
order — ``huggingface_hub`` freezes ``HF_HUB_CACHE`` from ``XDG_CACHE_HOME`` at
import time, so a late ``os.environ`` override alone would miss the snapshot. The
same directory is also exported as ``HF_HOME`` / ``HF_HUB_CACHE`` so the xet
chunk cache co-locates there. Nothing is ever written into the repo.

Import hygiene
--------------
``torch`` / ``timm`` / ``PIL`` / ``numpy`` are imported **lazily** (inside
methods), so importing this module — and, crucially, importing
``scripts.temporal.presence_scorer`` (whose ``dinov3_frozen`` factory imports
this module only inside the factory body) — never pulls torch. Only the two new
``decision_source`` values are registered at import time (cheap, torch-free).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.temporal.presence_scorer import (
    Pick,
    PresenceObservation,
    register_decision_source,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.validation.gemini_solar_image_review import GeminiObservation

# Additive registration of the two DINOv3 decision_source values so scan_state's
# write-time vocabulary gate accepts them. quality_flags "usable" / "unusable" /
# "missing_chip" are already seeded in presence_scorer.
DECISION_SOURCE_OK = "dinov3_frozen"
DECISION_SOURCE_FAILED = "dinov3_failed"
register_decision_source(DECISION_SOURCE_OK)
register_decision_source(DECISION_SOURCE_FAILED)

# Defaults (all overridable via the constructor — nothing here is frozen policy).
DEFAULT_BACKBONE_MODEL_ID = "vit_large_patch16_dinov3.sat493m"
DEFAULT_INPUT_SIZE = 256  # the model's native input; small chips are upscaled to it
DEFAULT_CENTER_POOL_K = 3
DEFAULT_HEAD_SEED = 20260703
DEFAULT_WEIGHTS_CACHE_DIR = Path("/home/gaosh/zasolar_data/models/dinov3_sat/hf_cache")
_WEIGHTS_ENV_VAR = "SOLAR_DINOV3_WEIGHTS_DIR"


def _resolve_weights_cache_dir(explicit: str | os.PathLike[str] | None) -> Path:
    """Weights cache dir: explicit arg > env override > default (all under
    ``~/zasolar_data``, never the repo)."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(_WEIGHTS_ENV_VAR)
    if env:
        return Path(env)
    return DEFAULT_WEIGHTS_CACHE_DIR


@dataclass
class _ChipVerdict:
    """Internal per-chip result funnelled from ``_observe`` to both surfaces."""

    pv_present: bool | None
    pv_score: float | None
    quality_flag: str
    decision_source: str
    error: str | None = None


class Dinov3PresenceScorer:
    """Frozen DINOv3-L-SAT presence scorer (placeholder head — ISSUE-03).

    Satisfies the ``PresenceScorer`` protocol (``name`` +
    ``failure_decision_sources`` / ``quality_flags`` / ``decision_sources``
    declarations + ``score``) and additionally exposes the raw ``batch`` callable
    the adaptive-scan real path consumes.
    """

    name = "dinov3_frozen"
    failure_decision_sources = frozenset({DECISION_SOURCE_FAILED})
    quality_flags = frozenset({"usable", "unusable", "missing_chip"})
    decision_sources = frozenset({DECISION_SOURCE_OK, DECISION_SOURCE_FAILED})

    def __init__(
        self,
        *,
        backbone_model_id: str = DEFAULT_BACKBONE_MODEL_ID,
        input_size: int = DEFAULT_INPUT_SIZE,
        center_pool_k: int = DEFAULT_CENTER_POOL_K,
        head_seed: int = DEFAULT_HEAD_SEED,
        head_checkpoint: str | os.PathLike[str] | None = None,
        weights_cache_dir: str | os.PathLike[str] | None = None,
        device: str = "cpu",
    ) -> None:
        if input_size <= 0 or input_size % 16 != 0:
            raise ValueError(
                f"input_size must be a positive multiple of the patch size (16), got {input_size}"
            )
        if center_pool_k <= 0:
            raise ValueError(f"center_pool_k must be positive, got {center_pool_k}")
        self.backbone_model_id = backbone_model_id
        self.input_size = int(input_size)
        self.center_pool_k = int(center_pool_k)
        self.head_seed = int(head_seed)
        self.head_checkpoint = Path(head_checkpoint) if head_checkpoint is not None else None
        self.weights_cache_dir = _resolve_weights_cache_dir(weights_cache_dir)
        self.device = device
        # Heavy state (built lazily on first real inference — kept None so
        # construction, the missing-chip path, and the fingerprint stay
        # torch-free and weights-free).
        self._encoder: Any = None
        self._head: Any = None
        self._mean: Any = None
        self._std: Any = None

    # -- instruction-identity payload (ISSUE-06 provenance) -------------------

    def prompt_config_fingerprint(self, mode: str, config: Any = None) -> dict[str, Any]:
        """Instruction identity for the scoring-provenance hash (ISSUE-06).

        Device-independent, secret-free. The DINOv3 scorer has no prompt; "what
        produced this verdict" is fully described by the backbone id, the head
        identity (trained checkpoint path or the placeholder seed), and the
        anchor-conditioning geometry (``center_pool_k`` / ``input_size``).
        """
        if self.head_checkpoint is not None:
            head_identity = f"checkpoint:{self.head_checkpoint}"
        else:
            head_identity = f"placeholder_seed:{self.head_seed}"
        return {
            "scorer": self.name,
            "mode": mode,
            "backbone_model_id": self.backbone_model_id,
            "head": head_identity,
            "center_pool_k": self.center_pool_k,
            "input_size": self.input_size,
        }

    # -- lazy heavy-state construction ---------------------------------------

    def _ensure_model(self) -> None:
        """Build the frozen encoder + placeholder head once (lazy, torch here)."""
        if self._encoder is not None:
            return
        import timm
        import torch
        from torch import nn

        # Route the HF hub download under ~/zasolar_data. We pass `cache_dir`
        # explicitly to timm.create_model (threaded straight to
        # huggingface_hub.hf_hub_download) — this is authoritative regardless of
        # import order, because huggingface_hub freezes HF_HUB_CACHE from
        # XDG_CACHE_HOME at import time and a late os.environ override would miss
        # the snapshot. The env vars are still set so the xet chunk cache
        # co-locates under the same directory instead of the XDG default.
        self.weights_cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(self.weights_cache_dir)
        os.environ["HF_HUB_CACHE"] = str(self.weights_cache_dir)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(self.weights_cache_dir)

        encoder = timm.create_model(
            self.backbone_model_id,
            pretrained=True,
            num_classes=0,
            cache_dir=str(self.weights_cache_dir),
        )
        encoder.requires_grad_(False)
        encoder.eval()
        encoder.to(self.device)

        data_cfg = timm.data.resolve_model_data_config(encoder)
        mean = torch.tensor(data_cfg["mean"], dtype=torch.float32).view(3, 1, 1)
        std = torch.tensor(data_cfg["std"], dtype=torch.float32).view(3, 1, 1)

        embed_dim = encoder.num_features
        head = nn.Linear(embed_dim, 3)
        if self.head_checkpoint is not None:
            state = torch.load(self.head_checkpoint, map_location=self.device)
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            head.load_state_dict(state)
        else:
            # Deterministic, process-independent placeholder init: fill the
            # parameters from an explicit generator instead of the global RNG.
            gen = torch.Generator(device="cpu").manual_seed(self.head_seed)
            with torch.no_grad():
                head.weight.copy_(torch.randn(3, embed_dim, generator=gen) * 0.02)
                head.bias.copy_(torch.zeros(3))
        head.requires_grad_(False)
        head.eval()
        head.to(self.device)

        self._encoder = encoder
        self._head = head
        self._mean = mean.to(self.device)
        self._std = std.to(self.device)

    # -- per-chip inference ---------------------------------------------------

    def _load_chip_tensor(self, chip_path: str) -> Any:
        """Load + upscale + normalize one chip to a ``[1,3,H,W]`` tensor."""
        import numpy as np
        import torch
        from PIL import Image

        with Image.open(chip_path) as raw:
            img = raw.convert("RGB").resize(
                (self.input_size, self.input_size), Image.BILINEAR
            )
        arr = np.asarray(img, dtype=np.float32) / 255.0  # HWC in [0,1]
        tensor = torch.from_numpy(arr).permute(2, 0, 1)  # CHW
        tensor = (tensor - self._mean) / self._std
        return tensor.unsqueeze(0).to(self.device)

    def _center_pool(self, patch_tokens: Any) -> Any:
        """Mean-pool the centre k×k patch tokens of a ``[1,P,C]`` token grid."""
        num_patches = patch_tokens.shape[1]
        grid = int(round(num_patches**0.5))
        if grid * grid != num_patches:
            raise ValueError(
                f"patch-token count {num_patches} is not a perfect square; "
                "cannot form a spatial grid for centre pooling"
            )
        k = min(self.center_pool_k, grid)
        channels = patch_tokens.shape[2]
        grid_tokens = patch_tokens.reshape(1, grid, grid, channels)
        start = (grid - k) // 2
        center = grid_tokens[:, start : start + k, start : start + k, :]
        return center.reshape(1, k * k, channels).mean(dim=1)  # [1, C]

    def _infer_probs(self, chip_path: str) -> Any:
        """Frozen forward → softmax over [present, absent, unusable]."""
        import torch

        self._ensure_model()
        tensor = self._load_chip_tensor(chip_path)
        with torch.inference_mode():
            feats = self._encoder.forward_features(tensor)  # [1, N, C]
            prefix = int(getattr(self._encoder, "num_prefix_tokens", 0))
            patch_tokens = feats[:, prefix:, :]
            pooled = self._center_pool(patch_tokens)
            logits = self._head(pooled)  # [1, 3]
            probs = torch.softmax(logits, dim=1)[0]
        return probs

    def _observe(self, chip_path: str) -> _ChipVerdict:
        """Score one chip, funnelling both surfaces. Never raises on a bad chip.

        Missing / unreadable chip → abstain verdict (``dinov3_failed`` /
        ``missing_chip``) so the batch survives. A readable chip is forwarded
        through the frozen encoder + placeholder head and mapped to a class.
        """
        if not chip_path or not os.path.exists(chip_path):
            return _ChipVerdict(
                pv_present=None,
                pv_score=None,
                quality_flag="missing_chip",
                decision_source=DECISION_SOURCE_FAILED,
                error=f"chip not found: {chip_path!r}",
            )
        try:
            probs = self._infer_probs(chip_path)
        except Exception as exc:  # unreadable/corrupt chip is recorded, not fatal
            return _ChipVerdict(
                pv_present=None,
                pv_score=None,
                quality_flag="missing_chip",
                decision_source=DECISION_SOURCE_FAILED,
                error=f"unreadable chip {chip_path!r}: {type(exc).__name__}: {exc}",
            )
        present_prob = float(probs[0])
        cls = int(probs.argmax())
        if cls == 0:
            return _ChipVerdict(True, present_prob, "usable", DECISION_SOURCE_OK)
        if cls == 1:
            return _ChipVerdict(False, present_prob, "usable", DECISION_SOURCE_OK)
        return _ChipVerdict(None, present_prob, "unusable", DECISION_SOURCE_OK)

    # -- seam entrypoint ------------------------------------------------------

    def score(
        self, picks: list[Pick], *, config: Any = None, **_kwargs: Any
    ) -> list[PresenceObservation]:
        """Canonical seam surface: one ``PresenceObservation`` per pick, in order.

        ``index`` is mapped exactly like ``GeminiPresenceScorer.score``
        (``pick.index`` or ``i + 1``); ``capture_date`` is carried over.
        """
        results: list[PresenceObservation] = []
        for i, pick in enumerate(picks):
            verdict = self._observe(pick.chip_path)
            results.append(
                PresenceObservation(
                    pv_present=verdict.pv_present,
                    pv_score=verdict.pv_score,
                    quality_flag=verdict.quality_flag,
                    decision_source=verdict.decision_source,
                    index=pick.index or (i + 1),
                    capture_date=pick.capture_date,
                    evidence="",
                    notes="",
                    error=verdict.error,
                )
            )
        return results

    # -- raw callable for the adaptive-scan real path -------------------------

    def batch(
        self,
        picks: list[Any],
        *,
        config: Any = None,
        audit_writer: Any = None,
        census_mid_date_iso: str | None = None,
        routing_salt: str | None = None,
        **_kwargs: Any,
    ) -> list["GeminiObservation"]:
        """Adaptive-scan real-path surface: ``GeminiObservation`` per pick, in order.

        Mirrors ``score_batch_with_fallback``'s contract closely enough that
        ``execute_round_real`` maps the results onto ``RoundResult`` byte-identically
        to the Gemini path. ``audit_writer`` / ``census_mid_date_iso`` /
        ``routing_salt`` are accepted for signature parity and are inert (the
        frozen scorer has no batch retry ladder, no census-calibration prompt, and
        no routing salt).
        """
        from scripts.validation.gemini_solar_image_review import GeminiObservation

        observations: list["GeminiObservation"] = []
        for pick in picks:
            verdict = self._observe(pick.chip_path)
            observations.append(
                GeminiObservation(
                    chip_index=pick.chip_index,
                    pv_present=verdict.pv_present,
                    confidence=verdict.pv_score,
                    quality_flag=verdict.quality_flag,
                    evidence="",
                    notes="",
                    decision_source=verdict.decision_source,
                    error=verdict.error,
                )
            )
        return observations
