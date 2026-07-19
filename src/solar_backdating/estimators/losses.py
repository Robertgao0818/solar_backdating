"""Interval-marginal decode loss + frame calibration regularizer + the T0/T1
paired-diagnostic TVD statistic (DESIGN-phase0-emission-extension §4.1/§4.2/§5.3).

Pure Python floats, zero numpy/torch — same style as the rest of
``estimators/`` so a future training script can import these without dragging a
tensor dependency into the decode package. The training loop itself (autograd,
LoRA, backbone) is out of scope here; these are the closed-form per-anchor /
per-frame scalars it will target.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solar_backdating.estimators.emissions import FrameEmission

_EPS_FLOOR = 1e-12


def interval_marginal_nll(posterior: Sequence[float], k_i: frozenset[int] | set[int]) -> float:
    """``-log Σ_{k∈K_i} posterior[k]`` (design §4.1, the L_i main term).

    Only the TOTAL mass on ``K_i`` matters, not how it is distributed inside
    ``K_i`` — redistributing mass among the ``K_i`` cells (sum preserved) leaves
    the loss unchanged. ``len(K_i) == 1`` degenerates to the ordinary
    ``-log(posterior[k])`` NLL.
    """
    mass = sum(posterior[k] for k in k_i)
    return -math.log(max(mass, _EPS_FLOOR))


def frame_calibration_loss(predicted: "FrameEmission", teacher_symbol: str) -> float:
    """L_frame — the §4.2 auxiliary per-frame calibration regularizer.

    - ``teacher_symbol == "abstain"``: only calibrate ``q`` toward 0 (no state
      supervision signal), ``-log(1 - q)``. ``e0``/``e1`` are NOT penalized.
    - ``"present"`` / ``"absent"``: cross-entropy on ``e(teacher_state)``
      (``e1`` for present, ``e0`` for absent) PLUS a "teacher deemed this frame
      usable" binary calibration of ``q`` toward 1 — ``-log(e(state)) - log(q)``.
    """
    q = predicted.q
    if teacher_symbol == "abstain":
        return -math.log(max(1.0 - q, _EPS_FLOOR))
    if teacher_symbol == "present":
        e = predicted.e1
    elif teacher_symbol == "absent":
        e = predicted.e0
    else:
        raise ValueError(
            f"teacher_symbol={teacher_symbol!r} not in ('present', 'absent', 'abstain')"
        )
    return -math.log(max(e, _EPS_FLOOR)) - math.log(max(q, _EPS_FLOOR))


def _tvd(a: Mapping, b: Mapping) -> float:
    """Total-variation distance between two histograms: ``0.5 * Σ|share_a -
    share_b|``. Independent re-implementation of ``eval.metrics.tvd`` (identical
    formula), cross-checked in the test suite so the T0/T1 replay never drifts
    from the D3/AC5 caliber."""
    na = sum(a.values()) or 1
    nb = sum(b.values()) or 1
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / na - b.get(k, 0) / nb) for k in keys)


def pairwise_tvd(histograms: Sequence[Mapping]) -> list[float]:
    """All ``C(n, 2)`` pairwise hard-MAP year-histogram TVDs (design §5.3 T0/T1
    statistic), in ``itertools.combinations`` order. Order-dependent as a LIST
    (combinations order), but the MULTISET of values is invariant to permuting
    the input reps — the property the diagnostic actually relies on."""
    return [_tvd(a, b) for a, b in itertools.combinations(histograms, 2)]
