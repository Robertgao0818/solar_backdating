"""Adapter: production ``scan_state.json`` -> seam ``VintageObservation`` sequences.

Used by ``scripts/validation/estimator_endtoend_decode.py`` (ISSUE-02 end-to-end
TVD) to feed a registered estimator (``pava``/``fpd``/``sustained``/eventually
``changepoint``) with the SAME per-frame verdicts the adaptive scan orchestrator
recorded for a given anchor, without going through the ``long_all.csv`` /
``panel_io.load_panel`` flattening path.

READ-ONLY. Never rewrites a scan state — "consumes production scan states
without modification" is an acceptance criterion for the end-to-end decode
script. Nothing in this module opens a file for writing.

Schema (verified 2026-07-03 against
``~/zasolar_data/geid_temporal/llm_endtoend_20260623/rep{1,2,3}/{L0,L1}/scan_states/*.json``,
463 files total across L0+L1 for rep1 alone): top-level keys ``anchor_id``,
``region_key``, ``grid_id``, ``status``, ``rounds``, ``next_action``,
``started_at``, ``updated_at``, ``spec_version``, ``notes``. Each round has
``round_id``, ``round_type``, ``window_start_date``, ``window_end_date``,
``picks``, ``results``, ``completed``, ``failed``, ``notes``. Each
``results[]`` entry: ``chip_index``, ``capture_date``, ``version``,
``pv_present`` (JSON ``true``/``false``/``null``), ``confidence``,
``quality_flag`` (one of ``scripts.temporal.scan_state.QUALITY_FLAGS`` =
``{"usable", "ambiguous", "unusable"}``), ``decision_source``, ``evidence``,
``notes``, ``chip_path``, ``actual_zoom``.

Duplicate-``capture_date`` handling — DELIBERATE divergence from
``solar_backdating.eval.panel_io.load_panel``'s CSV last-wins dedup, and the
reason is worth spelling out because the two loaders read different layers of
the same pipeline:

* ``panel_io.load_panel`` reads ``long_all.csv``, a FLATTENED artifact where
  ``load_long`` (the banked-panel writer, upstream of this repo) already
  collapsed same-``capture_date`` rows within a (unit, rep) to one row
  (last-wins) before the CSV was ever written. Deduping again at read time
  there is a no-op in practice but documented as belt-and-braces.
* Production's install-date INFER layer never dedupes by date at all.
  ``scripts/temporal/scan_decision.py::collect_all_results`` /
  ``usable_observations`` (lines 105-110) and
  ``scripts/temporal/infer_install_dates.py::_all_results`` / ``_usable``
  (lines 166-171) flatten every round's ``RoundResult`` list verbatim — a
  chip re-scanned in a later round on the same calendar date stays a SEPARATE
  entry. Duplicate entries are harmless downstream only because
  ``infer_one`` (infer_install_dates.py:276-278, 356-357) selects extrema
  (``min``/``max`` over ``capture_date``) rather than counting occurrences,
  so a duplicate date can only ever be redundant, never double-weighted.

Because this adapter reads the RAW scan-state JSON (the same input production's
infer layer reads), it mirrors THAT behavior — one ``VintageObservation`` per
``RoundResult``, in stable ``(round_idx, chip_index)`` enumeration order, with
NO date-based collapsing. Deduping here would silently diverge from what
production's own status derivation sees. Downstream epoch collapsing
(``solar_backdating.estimators.epochs.collapse_epochs``, used by ``pava`` and
the ISSUE-02 decoder) still reduces same/near-date duplicates to one epoch
verdict at decode time — majority-vote per epoch — so keeping every raw
``RoundResult`` here does not double-count evidence for any seam-registered
estimator; it only matters for estimators (``fpd``/``sustained``) that treat
every distinct-date row independently, exactly as production's case machine
and dip-repair do.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from solar_backdating.estimators import VintageObservation

# JSON booleans decode to Python True/False; JSON null decodes to None.
_PRESENT_MAP: dict[bool | None, str] = {True: "1", False: "0", None: ""}


def _parse_capture_date(raw: object) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def load_scan_observations(path: Path) -> list[VintageObservation]:
    """Flatten one ``scan_state.json``'s rounds into ``VintageObservation``s.

    Read-only (never rewrites ``path``). Every scored/abstain ``RoundResult``
    with a parseable ``capture_date`` becomes one observation, in
    ``(round_idx, chip_index-within-round)`` order — see the module docstring
    for why this does NOT dedupe by date. Rows with an unparseable/blank
    ``capture_date`` are dropped (mirrors ``panel_io.parse_iso_date`` returning
    ``None`` -> caller skips). Returns ``[]`` for a missing/round-less file.

    ``VintageObservation.confidence`` carries the raw ``confidence`` field
    through unchanged for provenance/debugging; per ISSUE-02's hard
    constraint #4, no estimator built on this seam may use it in the emission
    model or decoding.
    """
    data = json.loads(Path(path).read_text())
    observations: list[VintageObservation] = []
    row = 0
    for rnd in data.get("rounds", []) or []:
        for result in rnd.get("results", []) or []:
            d = _parse_capture_date(result.get("capture_date"))
            if d is None:
                continue
            pv_present = result.get("pv_present")
            observations.append(
                VintageObservation(
                    capture_date=d,
                    pv_present=_PRESENT_MAP.get(pv_present, ""),
                    confidence=result.get("confidence"),
                    quality_flag=result.get("quality_flag", "usable") or "usable",
                    source_row=row,
                )
            )
            row += 1
    return observations


def anchor_id_of(path: Path) -> str:
    """Read just the top-level ``anchor_id`` (join sanity-checks / diagnostics)."""
    return json.loads(Path(path).read_text()).get("anchor_id", "")
