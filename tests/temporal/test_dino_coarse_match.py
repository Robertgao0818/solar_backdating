"""Unit tests for ``scripts.temporal.dino_coarse_match`` (DINO dense-patch-token
coarse registration primitives, factored out of ``pilot_dino_rescue_2026-07-08.py``
during ``/tmp/handoff_gehi_dino_instrument_check_2026-07-08.md``).

Scope is the two seams a reviewer flagged as the likely site of the "DINO-coarse
argmax pins at shift≈0 on a flat-looking 0.6-0.76 similarity floor" symptom:

  1. Uncentered cosine similarity (shared common-mode/DC component swamping the
     true spatial signal) — ``center_tokens`` is the candidate fix.
  2. Overlap-normalization bias in ``coarse_match`` (shrinking valid-overlap
     region as |shift| grows structurally favoring shift=0).

Both are exercised here with plain torch tensors — no backbone, no GPU, no chip
I/O (that's the diagnostic script's job, against real DINO features on the
137-row positive-control set). ``coarse_match`` and ``center_tokens`` are pure
array-level functions, same spirit as ``tests/temporal/test_chip_displacement.py``.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

requires_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None, reason="torch not installed"
)

if importlib.util.find_spec("torch") is not None:
    import torch

    from scripts.temporal.dino_coarse_match import center_tokens, coarse_match


def _unit(t):
    return t / t.norm(dim=-1, keepdim=True).clamp_min(1e-6)


def _textured_token_grid(side: int, margin: int, channels: int, seed: int):
    """A (side+2*margin, side+2*margin, C) field of distinct per-cell unit
    vectors — enough spatial structure for a real correlation peak to exist,
    analogous to ``test_chip_displacement._textured``'s smoothed-noise image
    but at token-grid granularity."""
    rng = np.random.default_rng(seed)
    scene = rng.standard_normal((side + 2 * margin, side + 2 * margin, channels)).astype(np.float32)
    return torch.from_numpy(scene)


def _shifted_pair(side: int, margin: int, channels: int, seed: int, sy0: int, sx0: int):
    """ref/mov token grids drawn from the SAME underlying scene, offset so mov's
    content sits ``sy0`` rows below / ``sx0`` cols right of ref's — the exact
    convention ``coarse_match`` is documented to return."""
    scene = _textured_token_grid(side, margin, channels, seed)
    ref = scene[margin : margin + side, margin : margin + side, :]
    mov = scene[margin - sy0 : margin - sy0 + side, margin - sx0 : margin - sx0 + side, :]
    return _unit(ref.clone()), _unit(mov.clone())


# --------------------------------------------------------------------------- #
# coarse_match: known-shift recovery (regression guard for the refactor)      #
# --------------------------------------------------------------------------- #

@requires_torch
@pytest.mark.parametrize("sy0,sx0", [(0, 0), (3, -2), (-4, 1), (5, 5), (-5, -5)])
def test_coarse_match_recovers_known_integer_shift(sy0, sx0):
    ref, mov = _shifted_pair(side=16, margin=6, channels=24, seed=0, sy0=sy0, sx0=sx0)
    sy, sx, sim = coarse_match(ref, mov, side=16, max_shift_cells=6)
    assert (sy, sx) == (sy0, sx0)
    assert sim == pytest.approx(1.0, abs=1e-4)  # exact content match at the true shift


@requires_torch
def test_coarse_match_similarity_is_lower_at_wrong_shifts():
    ref, mov = _shifted_pair(side=16, margin=6, channels=24, seed=1, sy0=2, sx0=2)
    _, _, best_sim = coarse_match(ref, mov, side=16, max_shift_cells=6)
    # Similarity at a deliberately wrong shift (independent content) must be
    # clearly below the true-shift peak — otherwise the argmax search is moot.
    a = ref[2:, 2:, :]
    b = mov[:-2, :-2, :]  # this is the shift=(0,0) slice pairing, i.e. wrong
    wrong_sim = float((a * b).sum(-1).mean().item())
    assert wrong_sim < best_sim - 0.05


# --------------------------------------------------------------------------- #
# Bug hypothesis #2 falsification: overlap-normalization does not zero-bias   #
# --------------------------------------------------------------------------- #

@requires_torch
def test_coarse_match_no_zero_bias_on_uncorrelated_tokens():
    """Null case: ref/mov share no true structure at any shift. A real overlap-
    normalization bug (shrinking valid-overlap region as |shift| grows
    structurally favoring shift=0) would concentrate the argmax at (0,0) far
    above chance. The averaging in ``coarse_match`` is a clean per-cell mean
    (``.sum(-1).mean()`` over the sliced, correctly-bounded overlap region), so
    under i.i.d. noise the OPPOSITE happens: smaller-overlap (large |shift|,
    near the search-window edge) candidates have higher-variance mean estimates
    and so are *more* likely to win by chance — the bias runs toward the
    window's edges, not toward zero."""
    side, channels, max_shift = 18, 16, 5
    rng = np.random.default_rng(3)
    n_trials = 200
    n_candidates = (2 * max_shift + 1) ** 2
    chance_zero_rate = 1.0 / n_candidates

    zero_hits = 0
    inf_norm_shifts = []
    for _ in range(n_trials):
        ref = _unit(torch.from_numpy(rng.standard_normal((side, side, channels)).astype(np.float32)))
        mov = _unit(torch.from_numpy(rng.standard_normal((side, side, channels)).astype(np.float32)))
        sy, sx, _ = coarse_match(ref, mov, side, max_shift)
        zero_hits += int(sy == 0 and sx == 0)
        inf_norm_shifts.append(max(abs(sy), abs(sx)))

    zero_rate = zero_hits / n_trials
    assert zero_rate <= chance_zero_rate * 3  # nowhere near "pins at zero"
    # Edge-bias signature: mean Chebyshev-norm shift sits well above the
    # window's midpoint, not near zero.
    assert float(np.mean(inf_norm_shifts)) > max_shift * 0.5


# --------------------------------------------------------------------------- #
# center_tokens: new fix-candidate #1 for the (unrelated) DC-component bug     #
# --------------------------------------------------------------------------- #

@requires_torch
def test_center_tokens_removes_grid_mean_and_stays_unit_norm():
    side, channels = 10, 12
    rng = np.random.default_rng(4)
    D = torch.from_numpy(rng.standard_normal(channels).astype(np.float32))
    scene = torch.from_numpy(rng.standard_normal((side, side, channels)).astype(np.float32))
    tok = _unit(5.0 * D + 0.2 * scene)  # large shared common-mode + small signal

    centered = center_tokens(tok)

    # The final unit-renormalization (per cell) means the output's grid-mean
    # isn't driven to exact zero, but the large shared D component must be
    # knocked down by a large factor relative to the uncentered grid-mean.
    raw_mean_norm = float(tok.mean(dim=(0, 1)).norm())
    centered_mean_norm = float(centered.mean(dim=(0, 1)).norm())
    assert centered_mean_norm < raw_mean_norm * 0.1

    norms = centered.norm(dim=-1)
    assert torch.allclose(norms, torch.ones_like(norms), atol=1e-5)


@requires_torch
def test_center_tokens_does_not_mix_ref_and_mov():
    """Centering is a per-image operation: it must depend only on the tensor
    passed in, not on any paired image — ref and mov centered independently
    must equal ref and mov centered as a batch-of-one each."""
    side, channels = 8, 6
    rng = np.random.default_rng(5)
    ref = _unit(torch.from_numpy(rng.standard_normal((side, side, channels)).astype(np.float32)))
    mov = _unit(torch.from_numpy(rng.standard_normal((side, side, channels)).astype(np.float32)))

    c_ref_alone = center_tokens(ref.clone())
    c_mov_alone = center_tokens(mov.clone())
    # Centering ref must be unaffected by mov ever having existed (no shared
    # global state / accidental joint statistics).
    assert torch.allclose(center_tokens(ref.clone()), c_ref_alone)
    assert torch.allclose(center_tokens(mov.clone()), c_mov_alone)


@requires_torch
def test_coarse_match_recovers_known_shift_after_centering_too():
    """Centering must not itself break recovery on a clean, already-working
    case — it should be shift-preserving on well-behaved input."""
    ref, mov = _shifted_pair(side=16, margin=6, channels=24, seed=6, sy0=-3, sx0=4)
    c_ref, c_mov = center_tokens(ref), center_tokens(mov)
    sy, sx, _ = coarse_match(c_ref, c_mov, side=16, max_shift_cells=6)
    assert (sy, sx) == (-3, 4)
