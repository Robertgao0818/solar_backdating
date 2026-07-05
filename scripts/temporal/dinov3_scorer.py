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
from collections.abc import Sequence
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
DEFAULT_UPSCALE_POLICY = "bilinear"  # small chips upscale to input_size with this
VALID_UPSCALE_POLICIES = frozenset({"bilinear", "bicubic"})
DEFAULT_HEAD_SEED = 20260703
DEFAULT_WEIGHTS_CACHE_DIR = Path("/home/gaosh/zasolar_data/models/dinov3_sat/hf_cache")
_WEIGHTS_ENV_VAR = "SOLAR_DINOV3_WEIGHTS_DIR"

# Head-bundle class order (contract v1): logit index 0/1/2. FIXED — matches the
# scaffold's argmax mapping (present / absent / unusable).
HEAD_CLASS_ORDER = ("present", "absent", "unusable")


def _resolve_weights_cache_dir(explicit: str | os.PathLike[str] | None) -> Path:
    """Weights cache dir: explicit arg > env override > default (all under
    ``~/zasolar_data``, never the repo)."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(_WEIGHTS_ENV_VAR)
    if env:
        return Path(env)
    return DEFAULT_WEIGHTS_CACHE_DIR


# ---------------------------------------------------------------------------
# Head-bundle contract v1 (torch-free sidecar read + torch .pt load)
# ---------------------------------------------------------------------------
#
# A trained head is a *bundle*: ``<name>.pt`` (torch.save of
# ``{"schema_version": 1, "head_arch": "linear", "state_dict": <nn.Linear
# state_dict>}``) plus an optional ``<name>.json`` sidecar carrying the frozen
# config, the calibration band, and the ``.pt`` file's sha256. The sidecar is
# plain JSON, so config resolution / calibration / the fingerprint / the
# integrity hash all read it **without torch** (construction stays torch-free);
# only the actual state_dict load (``load_head_bundle``) pulls torch, and it does
# so lazily, invoked from ``_ensure_model``.


def _read_head_sidecar(pt_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read the JSON sidecar next to ``<pt>.pt`` (``.json`` sibling). Torch-free.

    Returns the parsed dict, or ``None`` when no sidecar exists — the legacy
    (ISSUE-03) raw-state_dict checkpoint has none, and ``None`` means "no bundle
    metadata: fall back to module defaults + argmax mapping".
    """
    import json

    sidecar = Path(pt_path).with_suffix(".json")
    if not sidecar.exists():
        return None
    with open(sidecar, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _sha256_file(path: str | os.PathLike[str]) -> str:
    """Streaming sha256 of a file's bytes. Torch-free (stdlib ``hashlib``)."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_head_bundle(
    pt_path: str | os.PathLike[str],
) -> tuple[Any, dict[str, Any] | None]:
    """Load a head bundle → ``(state_dict, meta)``.

    ``meta`` is the parsed JSON sidecar, or ``None`` when the sidecar is missing
    (legacy ISSUE-03 raw-state_dict behavior). The state_dict is unwrapped from
    the ``{"state_dict": ...}`` envelope when present (the bundle format) and used
    as-is otherwise (a legacy raw state_dict). ``torch`` is imported lazily here;
    this helper is only ever called from ``_ensure_model`` (the sidecar/meta half
    is split out into the torch-free ``_read_head_sidecar`` for construction-time
    reads).
    """
    import torch

    meta = _read_head_sidecar(pt_path)
    state = torch.load(pt_path, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    return state, meta


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
    # "ambiguous" (ISSUE-04) is emitted by the calibrated abstain band.
    quality_flags = frozenset({"usable", "ambiguous", "unusable", "missing_chip"})
    decision_sources = frozenset({DECISION_SOURCE_OK, DECISION_SOURCE_FAILED})

    def __init__(
        self,
        *,
        backbone_model_id: str | None = None,
        input_size: int | None = None,
        center_pool_k: int | None = None,
        upscale_policy: str | None = None,
        head_seed: int = DEFAULT_HEAD_SEED,
        head_checkpoint: str | os.PathLike[str] | None = None,
        weights_cache_dir: str | os.PathLike[str] | None = None,
        device: str = "cpu",
    ) -> None:
        # Read the head-bundle sidecar first (torch-free) so the anchor-geometry
        # sentinels AND the backbone id can resolve from the bundle config. A None
        # ctor arg means "adopt the bundle config value, else the module default";
        # an explicit ctor arg that disagrees with the bundle config is a
        # construction error (a bundle carries both the geometry AND the backbone
        # the head was trained under — the head is a linear map on THAT backbone's
        # embeddings, so silently overriding either would score the head at a
        # geometry/backbone it never saw and mislabel the scoring provenance).
        self.head_checkpoint = Path(head_checkpoint) if head_checkpoint is not None else None
        self._head_meta: dict[str, Any] | None = (
            _read_head_sidecar(self.head_checkpoint) if self.head_checkpoint is not None else None
        )
        bundle_config = self._head_meta.get("config") if self._head_meta else None

        # Contract v1: _observe maps logits by FIXED index (probs[0]=P(present),
        # argmax==2 => unusable). A bundle trained under any other class order
        # would be silently mis-mapped on every verdict, so reject it here
        # (torch-free, construction time) rather than mislabel downstream.
        if self._head_meta is not None:
            sidecar_order = self._head_meta.get("class_order")
            if sidecar_order is not None and tuple(sidecar_order) != HEAD_CLASS_ORDER:
                raise ValueError(
                    f"head-bundle class_order {tuple(sidecar_order)!r} != contract "
                    f"{HEAD_CLASS_ORDER!r} ({self.head_checkpoint})"
                )

        def _resolve(explicit: Any, key: str, default: Any) -> Any:
            bundle_val = bundle_config.get(key) if bundle_config else None
            if explicit is None:
                return bundle_val if bundle_val is not None else default
            if bundle_val is not None and bundle_val != explicit:
                raise ValueError(
                    f"constructor {key}={explicit!r} conflicts with head-bundle "
                    f"config {key}={bundle_val!r} ({self.head_checkpoint})"
                )
            return explicit

        backbone_model_id = _resolve(backbone_model_id, "backbone_model_id", DEFAULT_BACKBONE_MODEL_ID)
        input_size = _resolve(input_size, "input_size", DEFAULT_INPUT_SIZE)
        center_pool_k = _resolve(center_pool_k, "center_pool_k", DEFAULT_CENTER_POOL_K)
        upscale_policy = _resolve(upscale_policy, "upscale_policy", DEFAULT_UPSCALE_POLICY)

        # Validation applies post-resolution (the resolved value is what runs).
        if input_size <= 0 or input_size % 16 != 0:
            raise ValueError(
                f"input_size must be a positive multiple of the patch size (16), got {input_size}"
            )
        if center_pool_k <= 0:
            raise ValueError(f"center_pool_k must be positive, got {center_pool_k}")
        if upscale_policy not in VALID_UPSCALE_POLICIES:
            raise ValueError(
                f"upscale_policy must be one of {sorted(VALID_UPSCALE_POLICIES)}, "
                f"got {upscale_policy!r}"
            )

        self.backbone_model_id = backbone_model_id
        self.input_size = int(input_size)
        self.center_pool_k = int(center_pool_k)
        self.upscale_policy = str(upscale_policy)
        self.head_seed = int(head_seed)
        self.weights_cache_dir = _resolve_weights_cache_dir(weights_cache_dir)
        self.device = device

        # Calibration band from the bundle (torch-free). None => argmax mapping
        # (placeholder head, legacy checkpoint, or a bundle not yet calibrated).
        calib = self._head_meta.get("calibration") if self._head_meta else None
        self._calibration: dict[str, Any] | None = calib
        self._calib_lo: float | None = float(calib["lo"]) if calib is not None else None
        self._calib_hi: float | None = float(calib["hi"]) if calib is not None else None

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
        fingerprint: dict[str, Any] = {
            "scorer": self.name,
            "mode": mode,
            "backbone_model_id": self.backbone_model_id,
            "head": head_identity,
            "center_pool_k": self.center_pool_k,
            "input_size": self.input_size,
            # Upscaling is a scoring-time policy that changes what the encoder
            # sees, so it is always part of the verdict identity (ISSUE-04).
            "upscale_policy": self.upscale_policy,
        }
        # A bundle checkpoint additionally pins the head bytes (sha256) and the
        # calibration band — both change the decision, so both enter the identity.
        if self.head_checkpoint is not None and self._head_meta is not None:
            fingerprint["head_sha256"] = self._head_meta.get("head_pt_sha256")
            calib = self._calibration
            fingerprint["calibration"] = (
                {"lo": calib["lo"], "hi": calib["hi"]} if calib is not None else None
            )
        return fingerprint

    # -- lazy heavy-state construction ---------------------------------------

    def _verify_head_integrity(self) -> None:
        """Fail loudly if the .pt bytes disagree with the sidecar ``head_pt_sha256``.

        Torch-free (streaming ``hashlib``) and deliberately called *before* the
        heavy backbone build in ``_ensure_model``, so a tampered / mismatched
        bundle is caught without a 1.2 GB download. A missing sidecar or a sidecar
        without a hash is a legacy checkpoint — nothing to verify.
        """
        if self.head_checkpoint is None or self._head_meta is None:
            return
        expected = self._head_meta.get("head_pt_sha256")
        if not expected:
            return
        actual = _sha256_file(self.head_checkpoint)
        if actual != expected:
            raise ValueError(
                f"head bundle integrity check failed for {self.head_checkpoint}: "
                f".pt sha256 {actual} != sidecar head_pt_sha256 {expected}"
            )

    def _ensure_model(self) -> None:
        """Build the frozen encoder + head once (lazy, torch here)."""
        if self._encoder is not None:
            return
        # Integrity gate runs first (torch-free) so a corrupt bundle fails before
        # the expensive backbone download.
        self._verify_head_integrity()
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
            # Bundle or legacy raw state_dict — load_head_bundle unwraps either.
            state, _meta = load_head_bundle(self.head_checkpoint)
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

    def _pil_resample(self) -> Any:
        """PIL resampling filter for ``upscale_policy`` (validated at construction)."""
        from PIL import Image

        return {"bilinear": Image.BILINEAR, "bicubic": Image.BICUBIC}[self.upscale_policy]

    def _load_chip_tensor(self, chip_path: str) -> Any:
        """Load + upscale + normalize one chip to a ``[1,3,H,W]`` tensor."""
        import numpy as np
        import torch
        from PIL import Image

        with Image.open(chip_path) as raw:
            img = raw.convert("RGB").resize(
                (self.input_size, self.input_size), self._pil_resample()
            )
        arr = np.asarray(img, dtype=np.float32) / 255.0  # HWC in [0,1]
        tensor = torch.from_numpy(arr).permute(2, 0, 1)  # CHW
        # Move to the model device BEFORE normalizing: _mean/_std live on
        # self.device (_ensure_model), so normalizing a CPU tensor against them
        # raises a device mismatch on any CUDA scorer.
        tensor = tensor.unsqueeze(0).to(self.device)
        return (tensor - self._mean) / self._std

    def _center_pool(self, patch_tokens: Any) -> Any:
        """Mean-pool the centre k×k patch tokens of a ``[B,P,C]`` token grid → ``[B,C]``.

        Batch-general: ``B == 1`` for the per-chip scoring path (``_infer_probs``)
        and ``B == batch_size`` for ``embed_chips`` — both share this exact pooling.
        """
        batch = patch_tokens.shape[0]
        num_patches = patch_tokens.shape[1]
        grid = int(round(num_patches**0.5))
        if grid * grid != num_patches:
            raise ValueError(
                f"patch-token count {num_patches} is not a perfect square; "
                "cannot form a spatial grid for centre pooling"
            )
        k = min(self.center_pool_k, grid)
        channels = patch_tokens.shape[2]
        grid_tokens = patch_tokens.reshape(batch, grid, grid, channels)
        start = (grid - k) // 2
        center = grid_tokens[:, start : start + k, start : start + k, :]
        return center.reshape(batch, k * k, channels).mean(dim=1)  # [B, C]

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

    # -- batched embedding surface (training-time feature extraction) ----------

    def embed_chips(self, chip_paths: "Sequence[str]", batch_size: int = 32) -> Any:
        """Batched frozen anchor-conditioned embeddings → ``float32 [N, embed_dim]``.

        Extracts the SAME pooled feature the scoring head consumes (identical
        ``_load_chip_tensor`` preprocessing + centre pooling as ``_infer_probs``,
        under ``torch.inference_mode``), so head training / calibration and
        serving stay pixel-for-pixel consistent. Returns a CPU numpy array; row
        order matches ``chip_paths``.

        Strictness contrast: unlike ``_observe`` (which abstains on a
        missing/unreadable chip so a scan batch survives), this **raises** — a
        broken chip in a training/feature-extraction batch is a hard error, not a
        silently-dropped row.
        """
        import numpy as np
        import torch

        self._ensure_model()
        blocks: list[Any] = []
        for start in range(0, len(chip_paths), batch_size):
            batch_paths = chip_paths[start : start + batch_size]
            # _load_chip_tensor raises on a missing/unreadable chip (no abstain).
            tensors = [self._load_chip_tensor(p) for p in batch_paths]
            batch = torch.cat(tensors, dim=0)  # [B, 3, H, W]
            with torch.inference_mode():
                feats = self._encoder.forward_features(batch)  # [B, N, C]
                prefix = int(getattr(self._encoder, "num_prefix_tokens", 0))
                patch_tokens = feats[:, prefix:, :]
                pooled = self._center_pool(patch_tokens)  # [B, C]
            blocks.append(pooled.detach().to("cpu", dtype=torch.float32).numpy())
        if not blocks:
            embed_dim = int(self._encoder.num_features)
            return np.zeros((0, embed_dim), dtype=np.float32)
        return np.concatenate(blocks, axis=0).astype(np.float32)

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
        if self._calibration is not None:
            # Calibrated abstain band (ISSUE-04 / PRD D5). unusable stays its own
            # argmax class; otherwise present/absent/abstain derive from the band
            # on P(present), NOT from the LLM self-report. pv_score = P(present)
            # in every branch (contract v1).
            if cls == 2:
                return _ChipVerdict(None, present_prob, "unusable", DECISION_SOURCE_OK)
            if present_prob > self._calib_hi:
                return _ChipVerdict(True, present_prob, "usable", DECISION_SOURCE_OK)
            if present_prob < self._calib_lo:
                return _ChipVerdict(False, present_prob, "usable", DECISION_SOURCE_OK)
            return _ChipVerdict(None, present_prob, "ambiguous", DECISION_SOURCE_OK)
        # Legacy / placeholder mapping (no calibration band): plain argmax, exactly
        # as ISSUE-03 — byte-identical for the placeholder head and legacy ckpts.
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
