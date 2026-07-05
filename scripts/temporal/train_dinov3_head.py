#!/usr/bin/env python3
"""Train + calibrate + co-teacher-score the DINOv3-L-SAT light head (ISSUE-04).

Tracer slice 4 of the DINOv3 scorer-backbone swap (PRD
``docs/dinov3_sat_scorer_backbone_prd.md`` §D3/D5/D6/D7, issue
``docs/dinov3_scorer/ISSUE-04-train-head-calibrate.md``). The backbone stays
**frozen**; this script only touches the tiny ``nn.Linear`` head that sits on the
anchor-pooled embedding and the single global lo/hi abstain band derived from the
held-out Gemini labels.

Five argparse subcommands, each a thin CLI wrapper over a pure/testable library
function (heavy deps imported LAZILY — ``--help`` and module import stay
torch-free, matching the repo's import-hygiene discipline):

1. ``extract-features`` — join the render-provenance PNGs to the label manifest,
   run the FROZEN encoder (``Dinov3PresenceScorer.embed_chips``) once per chip,
   and cache ``[N, embed_dim]`` features + aligned metadata to an ``.npz`` (so
   train / calibrate / evaluate never re-touch the backbone).
2. ``train`` — fit the 3-class linear head on the ``split=="train"`` rows only,
   with an anchor-disjoint 10% internal val set for early stopping. Writes the
   head-bundle (``.pt`` + ``.json`` sidecar, contract v1) + a ``train_log.json``.
3. ``calibrate`` — sweep the lo/hi abstain band on the held-out **calibration
   half** so the student's present/absent decisions agree with Gemini, subject to
   a coverage floor; records the band into the head-bundle sidecar + a frontier
   CSV for Phase-4 reuse.
4. ``evaluate`` — report decided-agreement / coverage / confusion on the held-out
   **report half** (disjoint from calibration), localizable by GSD tier,
   terminal-status, sub-domain, and capture-year bucket.
5. ``co-teacher`` — the D12.vii calibration instrument: student calibrated
   decision vs teacher label across ALL rows, per
   ``sub_domain × actual_zoom × terminal_status`` stratum (further split by
   ``split`` and capture-year bucket), feeding replan_v2 Phase-4's abstain band
   and escalation-k.

Head-bundle contract v1 (Writer A writes, Writer B reads — both conform exactly):
``<name>.pt`` = ``torch.save({"schema_version":1,"head_arch":"linear",
"state_dict":<nn.Linear(D,3) state_dict>})``; ``<name>.json`` sidecar carries
``schema_version`` / ``head_arch`` / ``class_order`` (FIXED
``["present","absent","unusable"]``) / ``calibration`` / ``config`` /
``provenance`` / ``head_pt_sha256``. This module loads its OWN ``.pt`` directly
(the format is fully specified here); ``dinov3_scorer.load_head_bundle`` is
Writer B's read helper for the scorer seam.

Plugin boundary: imports only sibling ``scripts.temporal.*`` seams; the frozen
encoder is reached through ``Dinov3PresenceScorer`` (constructed lazily inside a
factory so nothing here import-depends on Writer B's in-flight ``embed_chips`` /
``upscale_policy`` additions). All real artifacts land under ``~/zasolar_data/``
(gitignored); tests use ``tmp_path`` fixtures + stubbed embeddings, never real
weights.
"""

from __future__ import annotations

# sys.path bootstrap so the subrepo runs as a script (matches sibling modules).
# ruff: noqa: E402
import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

# --------------------------------------------------------------------------- #
# Contract-fixed constants (see module docstring / head-bundle contract v1).
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = 1
HEAD_ARCH = "linear"
# FIXED logit ordering: index 0/1/2 == present/absent/unusable (matches the
# ISSUE-03 scorer scaffold mapping). NEVER reorder — Writer B's scorer keys off it.
CLASS_ORDER: tuple[str, ...] = ("present", "absent", "unusable")
CLASS_INDEX: dict[str, int] = {c: i for i, c in enumerate(CLASS_ORDER)}
# The student's calibrated verdict vocabulary (ambiguous == abstain zone).
STUDENT_DECISIONS: tuple[str, ...] = ("present", "absent", "ambiguous", "unusable")

# Deterministic-split salts (anchor-hash driven, order-independent).
VAL_SALT = "dinov3_head_val_v1:"  # train -> internal early-stop val (10%)
CALIB_SALT = "dinov3_head_calib_v1:"  # heldout -> calib half / report half

# String metadata columns cached beside the feature matrix in the .npz.
FEATURE_STRING_COLS: tuple[str, ...] = (
    "anchor_id",
    "capture_date",
    "version",
    "label_3class",
    "split",
    "sub_domain",
    "actual_zoom",
    "terminal_status",
    "png_path",
)

# Defaults (all overridable on the CLI — nothing here is frozen policy).
DEFAULT_SEED = 20260705
DEFAULT_INPUT_SIZE = 256
DEFAULT_CENTER_POOL_K = 3
DEFAULT_UPSCALE_POLICY = "bilinear"
DEFAULT_BATCH_SIZE_EXTRACT = 32
DEFAULT_BATCH_SIZE_TRAIN = 256
DEFAULT_LR = 1e-3
DEFAULT_WEIGHT_DECAY = 1e-4
DEFAULT_MAX_EPOCHS = 200
DEFAULT_PATIENCE = 20
DEFAULT_COVERAGE_FLOOR = 0.90
DEFAULT_GRID_STEP = 0.01


# --------------------------------------------------------------------------- #
# small shared helpers (torch-free)
# --------------------------------------------------------------------------- #
def _year_bucket(capture_date: object) -> str:
    """Capture-year bucket: ``le_2022`` / ``ge_2023`` / ``unknown``.

    The QA spotcheck flagged old-vintage haze, so the <=2022 vs >=2023 split is a
    first-class diagnostic axis. Robust to both ``20090312`` and ``2020-06-15``
    formats (take the leading four digits as the year).
    """
    digits = "".join(c for c in str(capture_date) if c.isdigit())
    if len(digits) < 4:
        return "unknown"
    try:
        year = int(digits[:4])
    except ValueError:
        return "unknown"
    if year < 1990 or year > 2100:
        return "unknown"
    return "le_2022" if year <= 2022 else "ge_2023"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def _json_for_pt(pt_path: os.PathLike[str] | str) -> Path:
    """The sidecar ``.json`` path for a head ``.pt`` (contract: same stem)."""
    return Path(pt_path).with_suffix(".json")


def _fmt(value: object) -> str:
    """CSV/markdown float formatter — blank for ``None`` (empty-stratum safe)."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _threshold_grid(step: float) -> list[float]:
    """The inclusive ``[0, step, ..., 1.0]`` sweep grid (float-clean)."""
    if not 0.0 < step <= 1.0:
        raise ValueError(f"grid_step must be in (0,1], got {step}")
    n = int(round(1.0 / step))
    return [round(i * step, 10) for i in range(n + 1)]


def _forward_probs(features: Any, weight: Any, bias: Any) -> Any:
    """Softmax over the linear head — pure numpy (no backbone, no torch).

    ``weight`` is ``[3, D]`` and ``bias`` is ``[3]`` (``nn.Linear(D,3)`` layout);
    ``logits = features @ weight.T + bias``. Numerically-stable softmax.
    """
    import numpy as np

    features = np.asarray(features, dtype=np.float32)
    logits = features @ np.asarray(weight).T + np.asarray(bias)
    logits = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def student_decisions(
    features: Any, weight: Any, bias: Any, lo: float, hi: float
) -> tuple[list[str], Any]:
    """Map head probabilities to the calibrated student verdict per row.

    Contract mapping: ``argmax==unusable`` -> ``unusable``; else with
    ``p = P(present)``: ``p > hi`` -> ``present``, ``p < lo`` -> ``absent``,
    otherwise ``ambiguous`` (abstain). Returns ``(decisions, present_prob)``.
    """
    probs = _forward_probs(features, weight, bias)
    argm = probs.argmax(axis=1)
    present_prob = probs[:, 0]
    out: list[str] = []
    for i in range(probs.shape[0]):
        if int(argm[i]) == 2:
            out.append("unusable")
        elif float(present_prob[i]) > hi:
            out.append("present")
        elif float(present_prob[i]) < lo:
            out.append("absent")
        else:
            out.append("ambiguous")
    return out, present_prob


def _load_head_arrays(head_pt_path: os.PathLike[str] | str) -> tuple[Any, Any]:
    """Load the linear head's ``weight`` ``[3,D]`` / ``bias`` ``[3]`` as numpy.

    Reads this module's OWN ``.pt`` bundle (format fully specified here). Torch is
    needed only to deserialize; the forward pass afterwards is pure numpy.
    """
    import numpy as np
    import torch

    obj = torch.load(Path(head_pt_path), map_location="cpu")
    state = obj["state_dict"] if isinstance(obj, dict) and "state_dict" in obj else obj
    weight = np.asarray(state["weight"].detach().cpu().numpy(), dtype=np.float32)
    bias = np.asarray(state["bias"].detach().cpu().numpy(), dtype=np.float32)
    return weight, bias


def _load_npz(path: os.PathLike[str] | str) -> dict[str, Any]:
    import numpy as np

    data = np.load(Path(path), allow_pickle=False)
    return {k: data[k] for k in data.files}


def _load_calibration(head_json_path: os.PathLike[str] | str) -> tuple[float, float]:
    """Read the calibrated ``(lo, hi)`` from a head sidecar, or fail loudly.

    An un-calibrated head (``calibration`` null / missing) cannot emit
    present/absent verdicts — the caller must run ``calibrate`` first.
    """
    sidecar = json.loads(Path(head_json_path).read_text(encoding="utf-8"))
    cal = sidecar.get("calibration")
    if not cal:
        raise SystemExit(
            f"head sidecar {head_json_path} has no calibration band — run the "
            f"`calibrate` subcommand before `evaluate`/`co-teacher`."
        )
    return float(cal["lo"]), float(cal["hi"])


def _load_backbone_model_id(head_json_path: os.PathLike[str] | str) -> str | None:
    """Read ``config.backbone_model_id`` from a head sidecar (report-title provenance).

    ``None`` is a normal case (a bundle from before ISSUE-05 stamped this key, or an
    explicit ``backbone_model_id=None`` at extract-time) — the caller resolves it to
    the historical DINOv3-L-SAT default, same rule the scorer itself uses.
    """
    sidecar = json.loads(Path(head_json_path).read_text(encoding="utf-8"))
    return sidecar.get("config", {}).get("backbone_model_id")


# --------------------------------------------------------------------------- #
# head-bundle writer (contract v1)
# --------------------------------------------------------------------------- #
def save_head_bundle(
    pt_path: os.PathLike[str] | str,
    state_dict: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    provenance: Mapping[str, Any],
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``<name>.pt`` + ``<name>.json`` per head-bundle contract v1.

    The ``.pt`` wraps the ``nn.Linear`` ``state_dict``; the ``.json`` sidecar
    records the class order (FIXED), calibration band (null until calibrated),
    render/backbone config, free-form provenance, and the ``.pt``'s sha256 so a
    reader can verify the pair. Returns the sidecar dict.
    """
    import torch

    pt_path = Path(pt_path)
    pt_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"schema_version": SCHEMA_VERSION, "head_arch": HEAD_ARCH, "state_dict": dict(state_dict)},
        pt_path,
    )
    sidecar = {
        "schema_version": SCHEMA_VERSION,
        "head_arch": HEAD_ARCH,
        "class_order": list(CLASS_ORDER),
        "calibration": (dict(calibration) if calibration is not None else None),
        "config": dict(config),
        "provenance": dict(provenance),
        "head_pt_sha256": _sha256_file(pt_path),
    }
    _json_for_pt(pt_path).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar


# --------------------------------------------------------------------------- #
# 1. extract-features
# --------------------------------------------------------------------------- #
def join_manifest_provenance(
    manifest_rows: Sequence[Mapping[str, object]],
    provenance_rows: Sequence[Mapping[str, object]],
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Attach each rendered chip's ``png_path`` to its manifest label row.

    Join key is ``(anchor_id, capture_date, version)``. A manifest row with no
    matching provenance chip is skipped (``skipped_no_chip``); a matched row whose
    PNG is absent on disk is skipped (``skipped_missing_on_disk``). census2023
    rows carry ``version==""`` and never match the integer-versioned provenance,
    so they fall out as label-only (``skipped_no_chip``) — exactly the intended
    behaviour (their chips were never persisted). Both skip counts are returned.
    """
    prov: dict[tuple[str, str, str], str] = {}
    for p in provenance_rows:
        png = str(p.get("png_path", ""))
        if not png:
            continue
        key = (str(p.get("anchor_id", "")), str(p.get("capture_date", "")), str(p.get("version", "")))
        prov.setdefault(key, png)

    feature_rows: list[dict[str, str]] = []
    skipped_no_chip = 0
    skipped_missing = 0
    for m in manifest_rows:
        key = (str(m.get("anchor_id", "")), str(m.get("capture_date", "")), str(m.get("version", "")))
        png = prov.get(key)
        if png is None:
            skipped_no_chip += 1
            continue
        if not path_exists(png):
            skipped_missing += 1
            continue
        feature_rows.append(
            {
                "anchor_id": key[0],
                "capture_date": key[1],
                "version": key[2],
                "label_3class": str(m.get("label_3class", "")),
                "split": str(m.get("split", "")),
                "sub_domain": str(m.get("sub_domain", "")),
                "actual_zoom": str(m.get("actual_zoom", "")),
                "terminal_status": str(m.get("terminal_status", "")),
                "png_path": png,
            }
        )
    return feature_rows, {
        "skipped_no_chip": skipped_no_chip,
        "skipped_missing_on_disk": skipped_missing,
    }


# Marker-free student chips are rendered at a ``.nomarker.png`` sibling path
# (``gehi_common.target_crop_review_png_path(..., draw_marker=False)``); the slice-2
# LLM-review render draws a marker and lands at a plain ``.png``.
NOMARKER_PNG_SUFFIX = ".nomarker.png"


def assert_marker_free_provenance(feature_rows: Sequence[Mapping[str, object]]) -> None:
    """PRD-D4 guard: refuse to embed MARKED chips into the student feature cache.

    The slice-2 marked provenance (``chip_render_provenance.csv``) and the slice-4
    marker-free student provenance (``student_chip_render_provenance.csv``) share the
    ``(anchor_id, capture_date, version)`` join key and sit in the same directory, so
    ``extract-features --provenance chip_render_provenance.csv`` would silently embed
    the MARKED PNGs — training the frozen encoder to key off a drawn ring/cross that
    the serving-time census chips never carry (systematic, silent train/serve skew).
    PRD-D4 is binding: the student's input chips MUST be marker-free. Marker-free
    chips are rendered at a distinct ``.nomarker.png`` path; abort loudly (naming the
    offenders) when any joined chip is not marker-free. Called at the extract-features
    boundary, after the join and BEFORE the frozen backbone is ever constructed.
    """
    marked = [
        str(r.get("png_path", ""))
        for r in feature_rows
        if not str(r.get("png_path", "")).endswith(NOMARKER_PNG_SUFFIX)
    ]
    if marked:
        raise SystemExit(
            "extract-features requires MARKER-FREE student chips (PRD-D4): "
            f"{len(marked)} of {len(feature_rows)} joined chip(s) are not marker-free "
            f"(png_path does not end with {NOMARKER_PNG_SUFFIX!r}; e.g. {marked[:3]}). "
            "Pass the marker-free student provenance "
            "(student_chip_render_provenance.csv), not the slice-2 marked "
            "chip_render_provenance.csv."
        )


def _default_scorer_factory(
    *,
    input_size: int,
    center_pool_k: int,
    upscale_policy: str,
    device: str,
    backbone_model_id: str | None = None,
    weights_cache_dir: str | os.PathLike[str] | None = None,
) -> Any:
    """Build the frozen ``Dinov3PresenceScorer`` (Writer B owns ``embed_chips`` /
    ``upscale_policy``). Imported lazily so this module stays import-independent of
    B's in-flight additions and torch-free at import time.

    ``backbone_model_id=None`` resolves to the scorer's DINOv3-L-SAT module default
    (byte-identical to the historical extraction); pass
    ``vit_small_patch14_dinov2.lvd142m`` (+ a distinct ``weights_cache_dir``) to
    extract features for the ISSUE-05 DINOv2 floor through the SAME class."""
    from scripts.temporal.dinov3_scorer import Dinov3PresenceScorer

    return Dinov3PresenceScorer(
        backbone_model_id=backbone_model_id,
        input_size=input_size,
        center_pool_k=center_pool_k,
        upscale_policy=upscale_policy,
        weights_cache_dir=weights_cache_dir,
        device=device,
    )


def embed_feature_rows(
    feature_rows: Sequence[Mapping[str, object]],
    *,
    input_size: int,
    center_pool_k: int,
    upscale_policy: str,
    device: str,
    batch_size: int,
    backbone_model_id: str | None = None,
    weights_cache_dir: str | os.PathLike[str] | None = None,
    scorer_factory: Callable[..., Any] = _default_scorer_factory,
) -> tuple[Any, dict[str, Any]]:
    """Embed every rendered chip through the frozen encoder (one pass).

    Uses ``scorer.embed_chips`` — the EXACT scoring-time preprocessing — so train
    and serve stay consistent. ``scorer_factory`` is injected (stubbed in tests)
    so unit tests never construct the real backbone. ``backbone_model_id`` /
    ``weights_cache_dir`` select the encoder (default None -> the DINOv3-L-SAT
    default; the DINOv2 floor passes the ViT-S/14 id). Returns
    ``(features[N,D] float32, timing_meta)`` where ``timing_meta`` carries
    throughput, peak VRAM (cuda only), and the backbone id + patch size the scorer
    actually resolved (authoritative provenance, ISSUE-05).
    """
    import numpy as np

    chip_paths = [str(r["png_path"]) for r in feature_rows]
    scorer = scorer_factory(
        input_size=input_size,
        center_pool_k=center_pool_k,
        upscale_policy=upscale_policy,
        device=device,
        backbone_model_id=backbone_model_id,
        weights_cache_dir=weights_cache_dir,
    )
    if device == "cuda":
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    features = scorer.embed_chips(chip_paths, batch_size=batch_size)
    elapsed = time.perf_counter() - t0
    features = np.asarray(features, dtype=np.float32)

    peak_vram: int | None = None
    if device == "cuda":
        import torch

        if torch.cuda.is_available():
            peak_vram = int(torch.cuda.max_memory_allocated())
    throughput = (len(chip_paths) / elapsed) if elapsed > 0 else None
    meta = {
        "throughput_chips_per_s": throughput,
        "peak_vram_bytes": peak_vram,
        "backbone_model_id": getattr(scorer, "backbone_model_id", None),
        "patch_size": getattr(scorer, "patch_size", None),
    }
    return features, meta


def write_features_npz(
    out_path: os.PathLike[str] | str,
    feature_rows: Sequence[Mapping[str, object]],
    features: Any,
) -> Path:
    """Persist ``features[N,D]`` + aligned string metadata to an ``.npz``."""
    import numpy as np

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = {
        col: np.array([str(r.get(col, "")) for r in feature_rows])
        for col in FEATURE_STRING_COLS
    }
    np.savez(out_path, features=np.asarray(features, dtype=np.float32), **cols)
    # np.savez appends .npz if the caller omitted it.
    return out_path if out_path.suffix == ".npz" else out_path.with_suffix(".npz")


def build_extract_meta(
    *,
    variant_label: str,
    input_size: int,
    center_pool_k: int,
    upscale_policy: str,
    backbone_model_id: object,
    provenance_csvs: Sequence[str],
    feature_rows: Sequence[Mapping[str, object]],
    skip_counts: Mapping[str, int],
    throughput: object,
    peak_vram: object,
    patch_size: object = None,
) -> dict[str, Any]:
    """The ``<out>.meta.json`` sidecar for an extracted feature cache.

    ``backbone_model_id`` + ``patch_size`` are the backbone-identity provenance the
    train step copies into the head-bundle config (ISSUE-05): the head is a linear
    map on THAT backbone's embeddings at THAT patch geometry.
    """
    label_counts = Counter(str(r.get("label_3class", "")) for r in feature_rows)
    split_counts = Counter(str(r.get("split", "")) for r in feature_rows)
    return {
        "variant_label": variant_label,
        "input_size": input_size,
        "center_pool_k": center_pool_k,
        "upscale_policy": upscale_policy,
        "backbone_model_id": backbone_model_id,
        "patch_size": patch_size,
        "provenance_csvs": list(provenance_csvs),
        "row_counts": {
            "total": len(feature_rows),
            "by_label_3class": dict(label_counts),
            "by_split": dict(split_counts),
        },
        "skipped": dict(skip_counts),
        "throughput_chips_per_s": throughput,
        "peak_vram_bytes": peak_vram,
    }


# --------------------------------------------------------------------------- #
# 2. train
# --------------------------------------------------------------------------- #
def _val_anchors(train_anchors: Iterable[str]) -> list[str]:
    """The internal early-stop val anchors: first 10% by ``VAL_SALT`` hash order.

    Anchor-disjoint from the fit set by construction (a whole anchor is either fit
    or val). At least one val anchor when >=2 train anchors exist; never all of
    them (fit stays non-empty).
    """
    ordered = sorted(
        set(train_anchors),
        key=lambda a: hashlib.sha256((VAL_SALT + a).encode("utf-8")).hexdigest(),
    )
    n = len(ordered)
    if n < 2:
        return []
    k = int(n * 0.10)
    if k == 0:
        k = 1
    k = min(k, n - 1)
    return ordered[:k]


def _assert_no_heldout_leak(split_array: Sequence[str], selected_mask: Any) -> None:
    """HARD GUARD: no ``heldout`` row may enter fit or val.

    ``selected_mask`` is the boolean union of fit+val over the FULL npz row order.
    Any selected row whose split != ``train`` is a training-data-leak bug — abort
    loudly (never silently train on held-out calibration/gate anchors).
    """
    for i, selected in enumerate(selected_mask):
        if selected and str(split_array[i]) != "train":
            raise SystemExit(
                "heldout-split leak: row "
                f"{i} (split={split_array[i]!r}) entered the train fit/val set — "
                "refusing to train on held-out anchors."
            )


def _class_weights(fit_labels: Sequence[int]) -> tuple[list[float], list[int], list[str]]:
    """Inverse-frequency class weights over the fit set (normalized).

    ``w_c = (num_nonzero / sum_nonzero(1/count)) * (1/count_c)`` for a class with
    rows, else ``0.0`` (+ a recorded warning). Normalization makes the mean
    non-zero weight 1.0. Returns ``(weights, counts, warnings)`` indexed by
    ``CLASS_ORDER``.
    """
    counts = [int(sum(1 for y in fit_labels if y == c)) for c in range(len(CLASS_ORDER))]
    inv = [(1.0 / ct if ct > 0 else 0.0) for ct in counts]
    nonzero = [w for w in inv if w > 0.0]
    scale = (len(nonzero) / sum(nonzero)) if nonzero else 0.0
    weights = [w * scale for w in inv]
    warnings = [
        f"class {CLASS_ORDER[c]!r} has 0 fit rows; CrossEntropy weight set to 0.0"
        for c in range(len(CLASS_ORDER))
        if counts[c] == 0
    ]
    return weights, counts, warnings


def _macro_f1(y_true: Any, y_pred: Any, num_classes: int = 3) -> float:
    """Macro-F1 over a FIXED class set (zero_division=0), numpy-only."""
    import numpy as np

    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    f1s = []
    for c in range(num_classes):
        tp = int(((y_pred == c) & (y_true == c)).sum())
        fp = int(((y_pred == c) & (y_true != c)).sum())
        fn = int(((y_pred != c) & (y_true == c)).sum())
        denom = 2 * tp + fp + fn
        f1s.append(0.0 if denom == 0 else 2.0 * tp / denom)
    return sum(f1s) / num_classes


def train_head(
    npz: Mapping[str, Any],
    *,
    out_path: os.PathLike[str] | str,
    seed: int = DEFAULT_SEED,
    lr: float = DEFAULT_LR,
    weight_decay: float = DEFAULT_WEIGHT_DECAY,
    batch_size: int = DEFAULT_BATCH_SIZE_TRAIN,
    max_epochs: int = DEFAULT_MAX_EPOCHS,
    patience: int = DEFAULT_PATIENCE,
    config: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit the 3-class linear head on ``split=="train"`` rows; write the bundle.

    Deterministic (CPU-safe): ``torch.manual_seed(seed)`` + a seeded shuffle
    generator. Early stops on the anchor-disjoint 10% internal-val macro-F1. Emits
    ``<out>.pt`` / ``<out>.json`` (calibration null) + ``<stem>.train_log.json``.
    Returns the train-log dict.
    """
    import numpy as np
    import torch
    from torch import nn

    out_path = Path(out_path)
    split = np.asarray(npz["split"]).astype(str)
    anchors = np.asarray(npz["anchor_id"]).astype(str)
    labels = np.asarray(npz["label_3class"]).astype(str)
    features = np.asarray(npz["features"], dtype=np.float32)

    is_train = split == "train"
    if not is_train.any():
        raise SystemExit("no split=='train' rows in features npz — nothing to fit.")

    train_anchor_set = sorted(set(anchors[is_train]))
    val_set = set(_val_anchors(train_anchor_set))
    in_val = np.array([a in val_set for a in anchors])
    is_val = is_train & in_val
    is_fit = is_train & ~in_val
    # HARD GUARD before any tensor touches the optimizer.
    _assert_no_heldout_leak(split, is_fit | is_val)
    if not is_fit.any():
        raise SystemExit("fit set empty after val carve-out — need >=2 train anchors.")

    unknown = sorted({lbl for lbl in labels if lbl not in CLASS_INDEX})
    if unknown:
        raise SystemExit(
            f"unrecognized label_3class value(s) {unknown} — expected one of "
            f"{list(CLASS_ORDER)}. Refusing to silently coerce to 'unusable'."
        )
    y_all = np.array([CLASS_INDEX[lbl] for lbl in labels], dtype=np.int64)
    fit_x = features[is_fit]
    fit_y = y_all[is_fit]
    val_x = features[is_val]
    val_y = y_all[is_val]
    dim = int(features.shape[1])

    weights, counts, warnings = _class_weights(list(fit_y))

    torch.manual_seed(int(seed))
    model = nn.Linear(dim, len(CLASS_ORDER))
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32))

    x_fit_t = torch.tensor(fit_x, dtype=torch.float32)
    y_fit_t = torch.tensor(fit_y, dtype=torch.long)
    x_val_t = torch.tensor(val_x, dtype=torch.float32)
    gen = torch.Generator().manual_seed(int(seed))

    best_f1 = -1.0
    best_state: dict[str, Any] | None = None
    no_improve = 0
    val_curve: list[float] = []
    epochs_run = 0
    for epoch in range(max_epochs):
        model.train()
        perm = torch.randperm(x_fit_t.shape[0], generator=gen)
        for start in range(0, x_fit_t.shape[0], batch_size):
            idx = perm[start : start + batch_size]
            optimizer.zero_grad()
            logits = model(x_fit_t[idx])
            loss = loss_fn(logits, y_fit_t[idx])
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            if x_val_t.shape[0] > 0:
                vpred = model(x_val_t).argmax(dim=1).cpu().numpy()
                f1 = _macro_f1(val_y, vpred)
            else:  # degenerate tiny-set fallback: monitor on fit
                tpred = model(x_fit_t).argmax(dim=1).cpu().numpy()
                f1 = _macro_f1(fit_y, tpred)
        val_curve.append(f1)
        epochs_run = epoch + 1
        if f1 > best_f1 + 1e-9:
            best_f1 = f1
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                break
    if best_state is None:
        best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    cfg = dict(config or {})
    cfg.setdefault("backbone_model_id", None)
    cfg.setdefault("input_size", None)
    cfg.setdefault("center_pool_k", None)
    cfg.setdefault("upscale_policy", None)
    cfg.setdefault("patch_size", None)
    cfg.setdefault("chip_render_variant", None)
    prov = dict(provenance or {})
    prov.setdefault("trained_by", "train_dinov3_head.train")
    prov.setdefault("seed", int(seed))
    save_head_bundle(out_path, best_state, config=cfg, provenance=prov, calibration=None)

    train_log = {
        "seed": int(seed),
        "hyperparams": {
            "lr": lr,
            "weight_decay": weight_decay,
            "batch_size": batch_size,
            "max_epochs": max_epochs,
            "patience": patience,
            "embed_dim": dim,
        },
        "class_order": list(CLASS_ORDER),
        "class_counts": {CLASS_ORDER[c]: counts[c] for c in range(len(CLASS_ORDER))},
        "class_weights": {CLASS_ORDER[c]: weights[c] for c in range(len(CLASS_ORDER))},
        "warnings": warnings,
        "n_train_anchors": len(train_anchor_set),
        "n_val_anchors": len(val_set),
        "n_fit_rows": int(is_fit.sum()),
        "n_val_rows": int(is_val.sum()),
        "epochs_run": epochs_run,
        "best_val_macro_f1": best_f1,
        "val_macro_f1_curve": val_curve,
    }
    log_path = out_path.with_name(out_path.stem + ".train_log.json")
    log_path.write_text(json.dumps(train_log, indent=2), encoding="utf-8")
    return train_log


# --------------------------------------------------------------------------- #
# 3. calibrate
# --------------------------------------------------------------------------- #
def _split_heldout_halves(heldout_anchors: Iterable[str]) -> tuple[set[str], set[str]]:
    """Deterministic calib/report halves of the held-out anchors.

    Sort by ``CALIB_SALT`` hash; first ``ceil(n/2)`` -> calibration half, rest ->
    report half. Single source shared by ``calibrate`` (calib) and ``evaluate``
    (report) so the two never overlap.
    """
    ordered = sorted(
        set(heldout_anchors),
        key=lambda a: hashlib.sha256((CALIB_SALT + a).encode("utf-8")).hexdigest(),
    )
    n = len(ordered)
    n_calib = math.ceil(n / 2)
    return set(ordered[:n_calib]), set(ordered[n_calib:])


def calibrate_band(
    npz: Mapping[str, Any],
    weight: Any,
    bias: Any,
    *,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
    grid_step: float = DEFAULT_GRID_STEP,
) -> tuple[dict[str, Any], list[dict[str, float]]]:
    """Sweep the global lo/hi band on the held-out calibration half.

    On calib-half rows whose teacher label is present/absent, sweep ``lo`` and
    ``hi >= lo`` over the grid. ``decided`` = ``argmax!=unusable`` AND
    (``p>hi`` or ``p<lo``); ``agreement`` = mean(student decision == teacher) over
    decided; ``coverage`` = decided/total. Choose the feasible band
    (``coverage>=floor``; fallback = argmax-coverage set) that maximizes agreement,
    then coverage, then minimizes band width, then lo — fully deterministic.

    GUARD: refuses to run if any anchor is in both the train and heldout split
    (a leak into the calibration set). Returns ``(result, frontier_rows)``.
    """
    import numpy as np

    split = np.asarray(npz["split"]).astype(str)
    anchors = np.asarray(npz["anchor_id"]).astype(str)
    labels = np.asarray(npz["label_3class"]).astype(str)
    features = np.asarray(npz["features"], dtype=np.float32)

    train_a = set(anchors[split == "train"])
    held_a = set(anchors[split == "heldout"])
    overlap = train_a & held_a
    if overlap:
        raise SystemExit(
            f"calibration leak: {len(overlap)} anchor(s) appear in BOTH train and "
            f"heldout splits (e.g. {sorted(overlap)[:3]}) — refusing to calibrate."
        )

    held_mask = split == "heldout"
    calib_set, _report_set = _split_heldout_halves(held_a)
    in_calib = np.array([a in calib_set for a in anchors])
    is_pa = np.isin(labels, np.array(["present", "absent"]))
    row_mask = held_mask & in_calib & is_pa

    feats = features[row_mask]
    teacher = labels[row_mask]
    total = int(row_mask.sum())
    calib_anchor_count = len(set(anchors[row_mask]))

    if total == 0:
        raise SystemExit(
            "no held-out calibration-half present/absent rows to calibrate on "
            "(check the split / calib-half / label_3class columns)."
        )

    probs = _forward_probs(feats, weight, bias)
    argm = probs.argmax(axis=1)
    pp = probs[:, 0]
    not_unusable = argm != 2

    grid = _threshold_grid(grid_step)
    frontier: list[dict[str, float]] = []
    for lo in grid:
        for hi in grid:
            if hi < lo:
                continue
            decided = not_unusable & ((pp > hi) | (pp < lo))
            n_dec = int(decided.sum())
            coverage = n_dec / total
            if n_dec > 0:
                pred = np.where(pp[decided] > hi, "present", "absent")
                agreement = float((pred == teacher[decided]).mean())
            else:
                agreement = 0.0
            frontier.append(
                {"lo": round(lo, 10), "hi": round(hi, 10), "coverage": coverage, "agreement": agreement}
            )

    feasible = [f for f in frontier if f["coverage"] >= coverage_floor]
    fallback = False
    if not feasible:
        fallback = True
        max_cov = max(f["coverage"] for f in frontier)
        feasible = [f for f in frontier if f["coverage"] == max_cov]
    best = min(
        feasible,
        key=lambda f: (-f["agreement"], -f["coverage"], (f["hi"] - f["lo"]), f["lo"]),
    )

    rule = (
        "max decided-agreement s.t. coverage>=" + f"{coverage_floor:g}"
        + (" (fallback: argmax-coverage set — floor infeasible)" if fallback else "")
        + "; tie-break: max agreement, max coverage, min band width, min lo; "
        "calibrated on held-out calibration-half present/absent Gemini labels."
    )
    result = {
        "lo": best["lo"],
        "hi": best["hi"],
        "rule": rule,
        "calib_anchors": calib_anchor_count,
        "decided_agreement": best["agreement"],
        "abstain_rate": 1.0 - best["coverage"],
    }
    return result, frontier


def run_calibrate(
    features_path: os.PathLike[str] | str,
    head_path: os.PathLike[str] | str,
    *,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
    grid_step: float = DEFAULT_GRID_STEP,
) -> dict[str, Any]:
    """Calibrate + persist: update the head sidecar band + write the frontier CSV."""
    npz = _load_npz(features_path)
    weight, bias = _load_head_arrays(head_path)
    result, frontier = calibrate_band(
        npz, weight, bias, coverage_floor=coverage_floor, grid_step=grid_step
    )

    head_json = _json_for_pt(head_path)
    sidecar = json.loads(head_json.read_text(encoding="utf-8"))
    sidecar["calibration"] = result
    head_json.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    features_path = Path(features_path)
    frontier_csv = features_path.with_name(
        features_path.stem.replace(".npz", "") + ".calibration_frontier.csv"
    )
    write_csv_rows(
        frontier_csv,
        [
            {"lo": r["lo"], "hi": r["hi"], "coverage": r["coverage"], "agreement": r["agreement"]}
            for r in frontier
        ],
        ("lo", "hi", "coverage", "agreement"),
    )
    return {"calibration": result, "frontier_csv": str(frontier_csv)}


# --------------------------------------------------------------------------- #
# 4/5 shared aggregation (pure, torch-free)
# --------------------------------------------------------------------------- #
def tally(records: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """Count student decisions vs teacher labels for one group of records.

    ``pa_*`` = the present/absent decided view (evaluate's decided-agreement).
    ``full_*`` = the present/absent/unusable decided view (co-teacher's
    agree/disagree; ``ambiguous`` is abstain, never agree/disagree).
    """
    dec = Counter(r["decision"] for r in records)
    pa_decided = dec.get("present", 0) + dec.get("absent", 0)
    pa_agree = sum(
        1 for r in records if r["decision"] in ("present", "absent") and r["decision"] == r["teacher"]
    )
    full_decided = pa_decided + dec.get("unusable", 0)
    full_agree = sum(
        1
        for r in records
        if r["decision"] in ("present", "absent", "unusable") and r["decision"] == r["teacher"]
    )
    return {
        "n": len(records),
        "n_present_student": dec.get("present", 0),
        "n_absent_student": dec.get("absent", 0),
        "n_ambiguous_student": dec.get("ambiguous", 0),
        "n_unusable_student": dec.get("unusable", 0),
        "pa_decided": pa_decided,
        "pa_agree": pa_agree,
        "full_decided": full_decided,
        "full_agree": full_agree,
    }


def _agreement_view(t: Mapping[str, int]) -> dict[str, Any]:
    """Decided-agreement + coverage from a tally (empty-group safe: None rate)."""
    n = t["n"]
    return {
        "n": n,
        "decided": t["pa_decided"],
        "agree": t["pa_agree"],
        "coverage": (t["pa_decided"] / n) if n else 0.0,
        "agreement": (t["pa_agree"] / t["pa_decided"]) if t["pa_decided"] else None,
    }


def _group_agreement(
    records: Sequence[Mapping[str, str]], key: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for r in records:
        groups[r[key]].append(r)
    return {g: _agreement_view(tally(rs)) for g, rs in groups.items()}


def compute_evaluation(records: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Held-out report-half metrics: overall + confusion + 4 breakdowns.

    ``records`` each carry ``teacher`` / ``decision`` / ``actual_zoom`` /
    ``terminal_status`` / ``sub_domain`` / ``year_bucket``. Pure and hand-checkable.
    """
    confusion: dict[str, int] = {}
    conf_counter = Counter((r["teacher"], r["decision"]) for r in records)
    for t in CLASS_ORDER:
        for s in STUDENT_DECISIONS:
            confusion[f"{t}|{s}"] = conf_counter.get((t, s), 0)
    return {
        "overall": _agreement_view(tally(records)),
        "confusion": confusion,
        "by_actual_zoom": _group_agreement(records, "actual_zoom"),
        "by_terminal_status": _group_agreement(records, "terminal_status"),
        "by_sub_domain": _group_agreement(records, "sub_domain"),
        "by_year_bucket": _group_agreement(records, "year_bucket"),
    }


def co_teacher_strata(records: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """Student-vs-teacher disagreement per full stratum, one self-describing row.

    Stratum key = ``sub_domain × actual_zoom × terminal_status × split ×
    year_bucket``. ``disagreement_rate_over_decided`` uses the present/absent/
    unusable decided denominator; ``ambiguous`` counts as abstain, not
    disagreement. Empty-decided strata report rate 0.0 (no div-by-zero).
    """
    groups: dict[tuple[str, ...], list[Mapping[str, str]]] = defaultdict(list)
    for r in records:
        key = (
            r["sub_domain"],
            r["actual_zoom"],
            r["terminal_status"],
            r["split"],
            r["year_bucket"],
        )
        groups[key].append(r)

    out: list[dict[str, Any]] = []
    for key in sorted(groups):
        rs = groups[key]
        t = tally(rs)
        n = t["n"]
        full_decided = t["full_decided"]
        full_agree = t["full_agree"]
        n_disagree = full_decided - full_agree
        n_abstain = t["n_ambiguous_student"]
        out.append(
            {
                "sub_domain": key[0],
                "actual_zoom": key[1],
                "terminal_status": key[2],
                "split": key[3],
                "year_bucket": key[4],
                "n": n,
                "n_student_present": t["n_present_student"],
                "n_student_absent": t["n_absent_student"],
                "n_student_ambiguous": t["n_ambiguous_student"],
                "n_student_unusable": t["n_unusable_student"],
                "n_agree": full_agree,
                "n_disagree": n_disagree,
                "n_abstain": n_abstain,
                "disagreement_rate_over_decided": (n_disagree / full_decided) if full_decided else 0.0,
                "abstain_rate_over_total": (n_abstain / n) if n else 0.0,
                "coverage_decided_over_total": (full_decided / n) if n else 0.0,
            }
        )
    return out


def _records_from_npz(
    npz: Mapping[str, Any], mask: Any, weight: Any, bias: Any, lo: float, hi: float
) -> list[dict[str, str]]:
    """Build teacher/decision/stratum records for the masked npz rows."""
    import numpy as np

    features = np.asarray(npz["features"], dtype=np.float32)[mask]
    decisions, _ = student_decisions(features, weight, bias, lo, hi)

    def col(name: str) -> Any:
        return np.asarray(npz[name]).astype(str)[mask]

    teacher = col("label_3class")
    zoom = col("actual_zoom")
    term = col("terminal_status")
    sub = col("sub_domain")
    split = col("split")
    cdate = col("capture_date")
    records: list[dict[str, str]] = []
    for i in range(len(decisions)):
        records.append(
            {
                "teacher": str(teacher[i]),
                "decision": decisions[i],
                "actual_zoom": str(zoom[i]),
                "terminal_status": str(term[i]),
                "sub_domain": str(sub[i]),
                "split": str(split[i]),
                "year_bucket": _year_bucket(cdate[i]),
            }
        )
    return records


def _evaluate_report_title(backbone_model_id: str | None) -> str:
    """Report title derived from the bundle's backbone (ISSUE-06 misattribution fix).

    A hardcoded ``"DINOv3 head ... (ISSUE-04)"`` title on the shared writer used to
    leak onto DINOv2-floor bundle reports too (numbers/config in the body were
    always correct — only the heading lied). ``backbone_model_id=None`` (an
    un-stamped bundle) resolves to the historical DINOv3-L-SAT default, the same
    rule the scorer itself uses for a ``None`` id.
    """
    from scripts.temporal.dinov3_scorer import DEFAULT_BACKBONE_MODEL_ID

    bid = backbone_model_id or DEFAULT_BACKBONE_MODEL_ID
    if "dinov2" in bid.lower():
        return f"# DINOv2 floor head ({bid}) — held-out report-half evaluation"
    return f"# DINOv3 head ({bid}) — held-out report-half evaluation (ISSUE-04)"


def write_evaluate_outputs(
    out_dir: os.PathLike[str] | str,
    metrics: Mapping[str, Any],
    lo: float,
    hi: float,
    *,
    backbone_model_id: str | None = None,
) -> None:
    """Write ``evaluate_metrics.csv`` + ``evaluate_report.md``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []

    def add(table: str, group: str, st: Mapping[str, Any]) -> None:
        rows.append(
            {
                "table": table,
                "group": group,
                "n": st["n"],
                "decided": st["decided"],
                "agree": st["agree"],
                "coverage": _fmt(st["coverage"]),
                "agreement": _fmt(st["agreement"]),
            }
        )

    add("overall", "ALL", metrics["overall"])
    for table in ("by_actual_zoom", "by_terminal_status", "by_sub_domain", "by_year_bucket"):
        for group, st in sorted(metrics[table].items()):
            add(table, group, st)
    for cell, count in metrics["confusion"].items():
        rows.append(
            {"table": "confusion", "group": cell, "n": count, "decided": "", "agree": "", "coverage": "", "agreement": ""}
        )
    write_csv_rows(
        out_dir / "evaluate_metrics.csv",
        rows,
        ("table", "group", "n", "decided", "agree", "coverage", "agreement"),
    )

    ov = metrics["overall"]
    lines = [
        _evaluate_report_title(backbone_model_id),
        "",
        f"Calibrated band: lo={lo:g}, hi={hi:g}. Report half is disjoint from the "
        "calibration half (both from the ISSUE-02 held-out split).",
        "",
        "## Overall (decided = student present/absent)",
        f"- rows: {ov['n']}",
        f"- coverage (decided/total): {_fmt(ov['coverage'])}",
        f"- decided-agreement vs teacher: {_fmt(ov['agreement'])}",
        "",
        "## Confusion (teacher rows x student cols)",
        "| teacher \\ student | " + " | ".join(STUDENT_DECISIONS) + " |",
        "| --- | " + " | ".join("---" for _ in STUDENT_DECISIONS) + " |",
    ]
    for t in CLASS_ORDER:
        cells = " | ".join(str(metrics["confusion"][f"{t}|{s}"]) for s in STUDENT_DECISIONS)
        lines.append(f"| {t} | {cells} |")
    for table, title in (
        ("by_actual_zoom", "GSD tier (actual_zoom)"),
        ("by_terminal_status", "terminal status"),
        ("by_sub_domain", "sub-domain"),
        ("by_year_bucket", "capture-year bucket"),
    ):
        lines += ["", f"## Breakdown — {title}", "| group | n | coverage | agreement |", "| --- | --- | --- | --- |"]
        for group, st in sorted(metrics[table].items()):
            lines.append(f"| {group} | {st['n']} | {_fmt(st['coverage'])} | {_fmt(st['agreement'])} |")
    (out_dir / "evaluate_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_evaluate(
    features_path: os.PathLike[str] | str,
    head_path: os.PathLike[str] | str,
    out_dir: os.PathLike[str] | str,
) -> dict[str, Any]:
    import numpy as np

    npz = _load_npz(features_path)
    weight, bias = _load_head_arrays(head_path)
    head_json_path = _json_for_pt(head_path)
    lo, hi = _load_calibration(head_json_path)
    backbone_model_id = _load_backbone_model_id(head_json_path)
    split = np.asarray(npz["split"]).astype(str)
    anchors = np.asarray(npz["anchor_id"]).astype(str)
    _calib_set, report_set = _split_heldout_halves(set(anchors[split == "heldout"]))
    mask = (split == "heldout") & np.array([a in report_set for a in anchors])
    records = _records_from_npz(npz, mask, weight, bias, lo, hi)
    metrics = compute_evaluation(records)
    write_evaluate_outputs(out_dir, metrics, lo, hi, backbone_model_id=backbone_model_id)
    return metrics


def write_co_teacher_outputs(
    out_dir: os.PathLike[str] | str, strata: Sequence[Mapping[str, Any]], lo: float, hi: float
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = (
        "sub_domain",
        "actual_zoom",
        "terminal_status",
        "split",
        "year_bucket",
        "n",
        "n_student_present",
        "n_student_absent",
        "n_student_ambiguous",
        "n_student_unusable",
        "n_agree",
        "n_disagree",
        "n_abstain",
        "disagreement_rate_over_decided",
        "abstain_rate_over_total",
        "coverage_decided_over_total",
    )
    rows = [
        {
            **{k: s[k] for k in columns[:13]},
            "disagreement_rate_over_decided": _fmt(s["disagreement_rate_over_decided"]),
            "abstain_rate_over_total": _fmt(s["abstain_rate_over_total"]),
            "coverage_decided_over_total": _fmt(s["coverage_decided_over_total"]),
        }
        for s in strata
    ]
    write_csv_rows(out_dir / "co_teacher_disagreement.csv", rows, columns)

    total_rows = sum(s["n"] for s in strata)
    total_disagree = sum(s["n_disagree"] for s in strata)
    total_decided = sum(s["n_agree"] + s["n_disagree"] for s in strata)
    total_abstain = sum(s["n_abstain"] for s in strata)
    lines = [
        "# Co-teacher dual-scoring — student vs teacher (D12.vii)",
        "",
        f"Calibrated band: lo={lo:g}, hi={hi:g}. Measured on the FULL training "
        "cohort (train+heldout, all classes) — a calibration instrument for "
        "replan_v2 Phase-4's abstain band + escalation-k, NOT a production shape.",
        "",
        "Column semantics: `decided` = student present/absent/unusable (a class "
        "call); `ambiguous` = abstain (never agree/disagree). "
        "`disagreement_rate_over_decided` = n_disagree / decided.",
        "",
        "## Totals",
        f"- strata: {len(strata)}",
        f"- rows: {total_rows}",
        f"- decided (class call): {total_decided}",
        f"- abstain (ambiguous): {total_abstain}",
        f"- disagreements: {total_disagree}",
        f"- overall disagreement rate (over decided): "
        f"{_fmt(total_disagree / total_decided if total_decided else 0.0)}",
        "",
        "See `co_teacher_disagreement.csv` for the per-stratum table.",
    ]
    (out_dir / "co_teacher_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_co_teacher(
    features_path: os.PathLike[str] | str,
    head_path: os.PathLike[str] | str,
    out_dir: os.PathLike[str] | str,
) -> list[dict[str, Any]]:
    import numpy as np

    npz = _load_npz(features_path)
    weight, bias = _load_head_arrays(head_path)
    lo, hi = _load_calibration(_json_for_pt(head_path))
    n = np.asarray(npz["anchor_id"]).shape[0]
    mask = np.ones(n, dtype=bool)
    records = _records_from_npz(npz, mask, weight, bias, lo, hi)
    strata = co_teacher_strata(records)
    write_co_teacher_outputs(out_dir, strata, lo, hi)
    return strata


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_extract_features(args: argparse.Namespace) -> None:
    manifest_rows = read_csv_rows(Path(args.manifest))
    provenance_rows: list[dict[str, str]] = []
    for csv_path in args.provenance:
        provenance_rows.extend(read_csv_rows(Path(csv_path)))
    feature_rows, skip_counts = join_manifest_provenance(manifest_rows, provenance_rows)
    # PRD-D4: the student head must train on the SAME marker-free chips the census
    # serves — reject the slice-2 marked provenance before touching the backbone.
    # The ONLY sanctioned bypass is the D4 "marker = optional ablation" arm, which
    # must be requested explicitly and is stamped into the feature-cache meta so a
    # marked cache can never masquerade as a production-eligible one.
    if not args.allow_marked_ablation:
        assert_marker_free_provenance(feature_rows)
    features, timing = embed_feature_rows(
        feature_rows,
        input_size=args.input_size,
        center_pool_k=args.center_pool_k,
        upscale_policy=args.upscale_policy,
        device=args.device,
        batch_size=args.batch_size,
        backbone_model_id=getattr(args, "backbone_model_id", None),
        weights_cache_dir=getattr(args, "weights_cache_dir", None),
    )
    npz_path = write_features_npz(args.out, feature_rows, features)
    meta = build_extract_meta(
        variant_label=args.variant_label,
        input_size=args.input_size,
        center_pool_k=args.center_pool_k,
        upscale_policy=args.upscale_policy,
        backbone_model_id=timing["backbone_model_id"],
        patch_size=timing.get("patch_size"),
        provenance_csvs=[str(p) for p in args.provenance],
        feature_rows=feature_rows,
        skip_counts=skip_counts,
        throughput=timing["throughput_chips_per_s"],
        peak_vram=timing["peak_vram_bytes"],
    )
    meta["marker_ablation"] = bool(args.allow_marked_ablation)
    Path(str(npz_path) + ".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(
        f"extracted {len(feature_rows)} chips -> {npz_path}; "
        f"skipped_no_chip={skip_counts['skipped_no_chip']} "
        f"skipped_missing_on_disk={skip_counts['skipped_missing_on_disk']}"
    )


def _cmd_train(args: argparse.Namespace) -> None:
    npz = _load_npz(args.features)
    meta_path = Path(str(args.features) + ".meta.json")
    marker_ablation = False
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        marker_ablation = bool(meta.get("marker_ablation", False))
        config = {
            "backbone_model_id": meta.get("backbone_model_id"),
            "input_size": meta.get("input_size"),
            "center_pool_k": meta.get("center_pool_k"),
            "upscale_policy": meta.get("upscale_policy"),
            "patch_size": meta.get("patch_size"),
            "chip_render_variant": meta.get("variant_label"),
        }
    else:
        config = {}
    if marker_ablation:
        # PRD D4: a head trained on marker-bearing chips is a DIAGNOSTIC ablation
        # only. Carry the flag into the bundle provenance so a pin/rollout gate
        # (ISSUE-07) can machine-reject it, and warn loudly here.
        print(
            "WARNING: features carry marker_ablation=true (--allow-marked-ablation) "
            "— this head is a DIAGNOSTIC ablation, NOT production-eligible (PRD D4). "
            "marker_ablation is stamped into the head-bundle provenance.",
            file=sys.stderr,
        )
    log = train_head(
        npz,
        out_path=args.out,
        seed=args.seed,
        lr=args.lr,
        weight_decay=args.weight_decay,
        batch_size=args.batch_size,
        max_epochs=args.max_epochs,
        patience=args.patience,
        config=config,
        provenance={"features_npz": str(args.features), "marker_ablation": marker_ablation},
    )
    print(
        f"trained head -> {args.out}; epochs={log['epochs_run']} "
        f"best_val_macro_f1={log['best_val_macro_f1']:.4f}; warnings={log['warnings']}"
    )


def _cmd_calibrate(args: argparse.Namespace) -> None:
    result = run_calibrate(
        args.features, args.head, coverage_floor=args.coverage_floor, grid_step=args.grid_step
    )
    cal = result["calibration"]
    print(
        f"calibrated lo={cal['lo']} hi={cal['hi']} "
        f"decided_agreement={cal['decided_agreement']:.4f} "
        f"abstain_rate={cal['abstain_rate']:.4f}; frontier={result['frontier_csv']}"
    )


def _cmd_evaluate(args: argparse.Namespace) -> None:
    metrics = run_evaluate(args.features, args.head, args.out_dir)
    ov = metrics["overall"]
    print(
        f"evaluate -> {args.out_dir}; n={ov['n']} coverage={_fmt(ov['coverage'])} "
        f"agreement={_fmt(ov['agreement'])}"
    )


def _cmd_co_teacher(args: argparse.Namespace) -> None:
    strata = run_co_teacher(args.features, args.head, args.out_dir)
    print(f"co-teacher -> {args.out_dir}; strata={len(strata)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train / calibrate / co-teacher-score the DINOv3 light head (ISSUE-04)."
    )
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("extract-features", help="frozen-encoder feature cache (.npz)")
    e.add_argument("--manifest", required=True, type=Path)
    e.add_argument("--provenance", required=True, action="append", help="repeatable")
    e.add_argument("--out", required=True, type=Path)
    e.add_argument("--variant-label", required=True)
    e.add_argument(
        "--backbone-model-id",
        default=None,
        help="timm backbone id (default: the DINOv3-L-SAT scorer default, "
        "byte-identical to the historical extraction). Pass "
        "'vit_small_patch14_dinov2.lvd142m' for the ISSUE-05 DINOv2 floor "
        "(patch size is derived from the id; use a matching --input-size).",
    )
    e.add_argument(
        "--weights-cache-dir",
        type=Path,
        default=None,
        help="HF weights cache dir (default: the scorer's per-backbone default under "
        "~/zasolar_data). Pass a distinct dir for the DINOv2 floor so its 22M "
        "snapshot never collides with the 303M DINOv3-L-SAT cache.",
    )
    e.add_argument("--input-size", type=int, default=DEFAULT_INPUT_SIZE)
    e.add_argument("--center-pool-k", type=int, default=DEFAULT_CENTER_POOL_K)
    e.add_argument("--upscale-policy", choices=("bilinear", "bicubic"), default=DEFAULT_UPSCALE_POLICY)
    e.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    e.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE_EXTRACT)
    e.add_argument(
        "--allow-marked-ablation",
        action="store_true",
        help="bypass the PRD-D4 marker-free guard for the deliberate marker-ablation "
        "arm only; the cache meta is stamped marker_ablation=true and must never "
        "feed the production head",
    )
    e.set_defaults(func=_cmd_extract_features)

    t = sub.add_parser("train", help="fit the 3-class linear head (train split only)")
    t.add_argument("--features", required=True, type=Path)
    t.add_argument("--out", required=True, type=Path, help="head .pt path (.json sidecar written alongside)")
    t.add_argument("--seed", type=int, default=DEFAULT_SEED)
    t.add_argument("--lr", type=float, default=DEFAULT_LR)
    t.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    t.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE_TRAIN)
    t.add_argument("--max-epochs", type=int, default=DEFAULT_MAX_EPOCHS)
    t.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)
    t.set_defaults(func=_cmd_train)

    c = sub.add_parser("calibrate", help="sweep the global lo/hi abstain band")
    c.add_argument("--features", required=True, type=Path)
    c.add_argument("--head", required=True, type=Path, help="head .pt path")
    c.add_argument("--coverage-floor", type=float, default=DEFAULT_COVERAGE_FLOOR)
    c.add_argument("--grid-step", type=float, default=DEFAULT_GRID_STEP)
    c.set_defaults(func=_cmd_calibrate)

    v = sub.add_parser("evaluate", help="held-out report-half agreement report")
    v.add_argument("--features", required=True, type=Path)
    v.add_argument("--head", required=True, type=Path)
    v.add_argument("--out-dir", required=True, type=Path)
    v.set_defaults(func=_cmd_evaluate)

    ct = sub.add_parser("co-teacher", help="student-vs-teacher disagreement per stratum (D12.vii)")
    ct.add_argument("--features", required=True, type=Path)
    ct.add_argument("--head", required=True, type=Path)
    ct.add_argument("--out-dir", required=True, type=Path)
    ct.set_defaults(func=_cmd_co_teacher)

    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
