"""Shared DINO dense-patch-token coarse-registration primitives.

Factored out of ``pilot_dino_rescue_2026-07-08.py`` (session 1, 2026-07-08) so a
second script can import this logic without the ``importlib`` /
``sys.modules`` workaround a hyphenated-filename module forces on callers. This
module owns only the "DINO half": backbone loading, image->token-grid encoding,
and the integer-shift argmax coarse matcher. Population/join-table/CLI logic
stays in each caller script.

``center_tokens`` is new (session 2, 2026-07-08) — a candidate fix for the
suspected "uncentered cosine similarity" bug diagnosed in
``/tmp/handoff_gehi_dino_instrument_check_2026-07-08.md``: ViT patch tokens
(DINO especially) can share a large common-mode/DC component, so *any* two
patches score cosine-similarity well above zero regardless of content — the
true spatial signal is a small ripple on top of that shared floor. Mean-
centering each image's own token set before matching removes the per-image
constant term; ``coarse_match`` itself is unchanged (frozen verbatim from the
pilot) so the raw/centered comparison isolates exactly this one lever.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


# --------------------------------------------------------------------------- #
# Backbone (weight-loading pattern reused from dinov3_scorer.py; NOT its       #
# scorer/classification logic).                                               #
# --------------------------------------------------------------------------- #

def load_backbone(model_id: str, cache_dir: Path, device: str):
    import os

    import timm
    import torch

    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["HF_HUB_CACHE"] = str(cache_dir)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_dir)

    encoder = timm.create_model(
        model_id, pretrained=True, num_classes=0, cache_dir=str(cache_dir), dynamic_img_size=True
    )
    encoder.requires_grad_(False)
    encoder.eval()
    encoder.to(device)

    data_cfg = timm.data.resolve_model_data_config(encoder)
    mean = torch.tensor(data_cfg["mean"], dtype=torch.float32).view(3, 1, 1).to(device)
    std = torch.tensor(data_cfg["std"], dtype=torch.float32).view(3, 1, 1).to(device)
    return encoder, mean, std


def gray_to_tensor(gray: np.ndarray, input_size: int, mean, std, device: str):
    import torch
    from PIL import Image

    u8 = (np.clip(gray, 0.0, 1.0) * 255.0).astype(np.uint8)
    img = Image.fromarray(u8, mode="L").resize((input_size, input_size), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0  # HW in [0,1]
    rgb = np.stack([arr, arr, arr], axis=0)  # fake-RGB: gray duplicated 3x (gain-neutral input)
    t = torch.from_numpy(rgb).unsqueeze(0).to(device)
    return (t - mean) / std


def patch_token_grid(encoder, tensor):
    """[1,3,H,W] -> L2-normalized (side, side, C) patch-token grid."""
    import torch

    with torch.inference_mode():
        feats = encoder.forward_features(tensor)  # [1, N, C]
    prefix = int(getattr(encoder, "num_prefix_tokens", 0))
    tok = feats[:, prefix:, :]
    n = tok.shape[1]
    side = int(round(n**0.5))
    if side * side != n:
        raise ValueError(f"patch-token count {n} is not a perfect square")
    tok = tok.reshape(side, side, -1)
    tok = tok / tok.norm(dim=-1, keepdim=True).clamp_min(1e-6)
    return tok, side


def center_tokens(tok):
    """Remove this image's own per-grid mean token (the shared common-mode /
    DC component), then re-normalize each cell back to a unit vector.

    ``tok`` is a ``(side, side, C)`` torch tensor, already L2-normalized by
    ``patch_token_grid``. Centering is done PER IMAGE (mean over that image's
    own ``side*side`` cells only) — this is the "mean-center each image's patch-
    token set before correlating" fix candidate, applied independently to ref
    and mov before ``coarse_match`` ever compares them.
    """
    mean_tok = tok.mean(dim=(0, 1), keepdim=True)
    centered = tok - mean_tok
    norm = centered.norm(dim=-1, keepdim=True).clamp_min(1e-6)
    return centered / norm


def coarse_match(ref_tok, mov_tok, side: int, max_shift_cells: int):
    """Argmax mean-cosine-similarity integer patch shift of mov relative to ref.

    Same row/col displacement convention as ``chip_displacement.ShiftResult``:
    a returned (sy, sx) means mov's content sits ``sy`` rows below / ``sx``
    cols right of ref's content, in patch-cell units.
    """
    best_sim = -1e9
    best_sy = best_sx = 0
    for sy in range(-max_shift_cells, max_shift_cells + 1):
        i0, i1 = max(0, -sy), min(side, side - sy)
        if i1 <= i0:
            continue
        for sx in range(-max_shift_cells, max_shift_cells + 1):
            j0, j1 = max(0, -sx), min(side, side - sx)
            if j1 <= j0:
                continue
            a = ref_tok[i0:i1, j0:j1, :]
            b = mov_tok[i0 + sy : i1 + sy, j0 + sx : j1 + sx, :]
            sim = float((a * b).sum(-1).mean().item())
            if sim > best_sim:
                best_sim, best_sy, best_sx = sim, sy, sx
    return best_sy, best_sx, best_sim
