"""Leg-R R1 byte-contract pytest: tiny committed scan-state fixtures.

Does not load the 21k Cape Town production states. ``make repro-check`` runs
this file plus the merge/infer unit tests.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.temporal.merge_ct_scan_states import merge_scan_states

SUBREPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_STATES = Path(__file__).parent / "fixtures/repro_r1/scan_states"
# Frozen after the R1 canonicalize (scan_state_path = basename, census 2025-01-31).
# Recompute with: python -m pytest tests/temporal/test_repro_r1_contract.py -q
EXPECTED_INTERVALS_SHA256 = (
    "48ca6e2e531b01ee2459b2c2b64420f2668c074705eb8af71aa38798616c4bfc"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _infer(states_dir: Path, output_csv: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "scripts/temporal/infer_install_dates.py",
            "--scan-states-dir",
            str(states_dir),
            "--output",
            str(output_csv),
            "--census-mid-date",
            "2025-01-31",
            "--require-terminal",
            "--no-scan-state-path-sidecar",
        ],
        cwd=str(SUBREPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_repro_r1_fixture_dual_infer_is_byte_identical(tmp_path: Path) -> None:
    assert len(list(FIXTURE_STATES.glob("*.json"))) == 6
    csv_paths = []
    for label in ("run_a", "run_b"):
        states = tmp_path / label / "scan_states"
        states.mkdir(parents=True)
        for src in sorted(FIXTURE_STATES.glob("*.json")):
            shutil.copy2(src, states / src.name)
        output = tmp_path / label / "install_intervals.csv"
        proc = _infer(states, output)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        csv_paths.append(output)
    assert csv_paths[0].read_bytes() == csv_paths[1].read_bytes()
    digest = _sha256(csv_paths[0])
    if EXPECTED_INTERVALS_SHA256.startswith("PLACEHOLDER"):
        pytest.fail(f"set EXPECTED_INTERVALS_SHA256 to {digest}")
    assert digest == EXPECTED_INTERVALS_SHA256


def test_repro_r1_merge_then_infer_dual_run(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    canary = tmp_path / "canary"
    primary.mkdir()
    canary.mkdir()
    files = sorted(FIXTURE_STATES.glob("*.json"))
    for src in files[:4]:
        shutil.copy2(src, primary / src.name)
    for src in files[2:]:
        shutil.copy2(src, canary / src.name)
    # Overlap files[2], files[3] are identical bytes across scopes.
    merged = tmp_path / "merged"
    result = merge_scan_states([primary, canary], merged, expected_count=6)
    assert result.n_copied == 6
    assert len(result.identical_dups) == 2

    hashes = []
    for label in ("a", "b"):
        out = tmp_path / f"intervals_{label}.csv"
        proc = _infer(merged, out)
        assert proc.returncode == 0, proc.stderr
        hashes.append(_sha256(out))
    assert hashes[0] == hashes[1]
    if not EXPECTED_INTERVALS_SHA256.startswith("PLACEHOLDER"):
        assert hashes[0] == EXPECTED_INTERVALS_SHA256
