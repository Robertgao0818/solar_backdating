"""Detector (+ optional classifier corroboration) scoring for CoJ audit chips.

Reuses the census detector building block directly (no geoai, no census-
inference registry lookups — this is a small offline accuracy-channel
pilot, not a census run):

  - `core.models.maskrcnn.build_solar_maskrcnn` — same Mask R-CNN
    ResNet50-FPN builder the census engine uses.
  - `core.inference.tile_dataset.SlidingWindowDataset` / `list_collate` —
    same sliding-window chip reader, constructed directly from a list of
    chip TIF paths (no `regions.yaml` / grid registry involved).

S = max post-NMS box score over every sliding window in the chip (0.0 if the
detector finds nothing). `score_thresh=0.05` is set low enough that S is a
faithful "best guess", independent of the eventual 0.30/0.95 margin
thresholds applied downstream in `coj_audit_join.classify_bit`.

Classifier corroboration is a best-effort, file-based subprocess call into
the sibling `solar_cls` plugin repo (`classify_chip_manifest.py`) — see that
module's docstring for the contract. This module never imports solar_cls
Python directly (cross-plugin import ban).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # solar_backdating/
ZASOLAR_ROOT = Path(
    __import__("os").environ.get("ZASOLAR_ROOT", "/home/gaosh/projects/ZAsolar")
)
if str(ZASOLAR_ROOT) not in sys.path:
    sys.path.insert(0, str(ZASOLAR_ROOT))

DEFAULT_CHECKPOINT = ZASOLAR_ROOT / "checkpoints/exp_unified_reviewall_A/best_model.pth"
SOLAR_CLS_ROOT = Path(
    __import__("os").environ.get("SOLAR_CLS_ROOT", "/home/gaosh/projects/solar_cls")
)
DEFAULT_CLS_CHECKPOINT = Path(
    "~/zasolar_data/cls/checkpoints/cls_pv_thermal_v2_dinov2_vits14_adaptive/best_cls.pth"
).expanduser()

DETECTOR_SCORE_THRESH = 0.05
DETECTOR_NMS_THRESH = 0.5
DETECTOR_DETECTIONS_PER_IMG = 300
CHIP_SIZE = 400
OVERLAP = 0.25


def load_detector(
    checkpoint_path: Path | str = DEFAULT_CHECKPOINT, *, device: str = "cuda"
) -> torch.nn.Module:
    """Build the census Mask R-CNN and load the checkpoint (raw state_dict)."""
    from core.models.maskrcnn import build_solar_maskrcnn

    dev = torch.device(device if (device != "cuda" or torch.cuda.is_available()) else "cpu")
    model = build_solar_maskrcnn(pretrained_path=str(checkpoint_path), strict_load=False)
    model.roi_heads.score_thresh = DETECTOR_SCORE_THRESH
    model.roi_heads.nms_thresh = DETECTOR_NMS_THRESH
    model.roi_heads.detections_per_img = DETECTOR_DETECTIONS_PER_IMG
    model.to(dev)
    model.eval()
    return model


def score_chip_file(
    tif_path: Path,
    model: torch.nn.Module,
    device: torch.device,
    *,
    chip_size: int = CHIP_SIZE,
    overlap: float = OVERLAP,
) -> float | None:
    """Max post-NMS box score across all sliding windows of ``tif_path``.

    Returns 0.0 if the detector finds nothing, or ``None`` if the file can't
    be read (missing/corrupt chip).
    """
    from core.inference.tile_dataset import SlidingWindowDataset, list_collate
    from torch.utils.data import DataLoader

    try:
        ds = SlidingWindowDataset(
            [tif_path], chip_size=chip_size, overlap=overlap, edge_pad=True
        )
    except Exception:
        return None

    max_score = 0.0
    try:
        loader = DataLoader(ds, batch_size=4, collate_fn=list_collate, num_workers=0)
        with torch.no_grad():
            for tensors, _metas in loader:
                tensors = [t.to(device) for t in tensors]
                outputs = model(tensors)
                for out in outputs:
                    scores = out.get("scores")
                    if scores is not None and len(scores) > 0:
                        max_score = max(max_score, float(scores.max().item()))
    finally:
        ds.close()
    return max_score


def score_manifest(
    rows: list[dict[str, Any]],
    model: torch.nn.Module,
    device: torch.device,
    *,
    chip_size: int = CHIP_SIZE,
    overlap: float = OVERLAP,
) -> list[dict[str, Any]]:
    """Score every row's ``chip_path``. Returns rows augmented with `score` /
    `detector_status` ("scored" | "missing_chip" | "unreadable")."""
    out: list[dict[str, Any]] = []
    for row in rows:
        chip_path = Path(row["chip_path"])
        result = dict(row)
        if not chip_path.exists() or chip_path.stat().st_size == 0:
            result["score"] = None
            result["detector_status"] = "missing_chip"
        else:
            score = score_chip_file(chip_path, model, device, chip_size=chip_size, overlap=overlap)
            if score is None:
                result["score"] = None
                result["detector_status"] = "unreadable"
            else:
                result["score"] = score
                result["detector_status"] = "scored"
        out.append(result)
    return out


# ---------------------------------------------------------------------------
# Classifier corroboration (file-based subprocess seam into solar_cls)
# ---------------------------------------------------------------------------


def run_classifier_manifest(
    manifest_csv: Path,
    output_csv: Path,
    *,
    checkpoint: Path = DEFAULT_CLS_CHECKPOINT,
    solar_cls_root: Path = SOLAR_CLS_ROOT,
    python_executable: str | None = None,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Invoke solar_cls's `classify_chip_manifest.py` as a subprocess.

    Never imports solar_cls Python (cross-plugin import ban) — the seam is
    strictly file paths in / file paths out. Returns a small status dict;
    raises nothing on failure (caller decides whether to demote to
    detector-only per the ISSUE-08 brief's 1-hour time-box escape hatch).
    """
    script = solar_cls_root / "scripts" / "classifier" / "classify_chip_manifest.py"
    py = python_executable or sys.executable
    cmd = [
        py, str(script),
        "--manifest", str(manifest_csv),
        "--output", str(output_csv),
        "--checkpoint", str(checkpoint),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(solar_cls_root), capture_output=True, text=True, timeout=timeout_s
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "cmd": cmd}
    return {
        "ok": proc.returncode == 0 and output_csv.exists(),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "cmd": cmd,
    }


def load_classifier_probs(output_csv: Path) -> dict[str, float]:
    """{chip_path: pv_prob} from a classify_chip_manifest.py output CSV."""
    probs: dict[str, float] = {}
    if not output_csv.exists():
        return probs
    with open(output_csv, newline="") as f:
        for row in csv.DictReader(f):
            try:
                probs[row["chip_path"]] = float(row["pv_prob"])
            except (KeyError, ValueError, TypeError):
                continue
    return probs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-csv", type=Path, required=True, help="sample.csv with chip_path,anchor_id,year columns")
    ap.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--classifier-manifest-out", type=Path, default=None)
    ap.add_argument("--classifier-scores-out", type=Path, default=None)
    ap.add_argument("--skip-classifier", action="store_true")
    args = ap.parse_args()

    with open(args.sample_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    device = torch.device(args.device if (args.device != "cuda" or torch.cuda.is_available()) else "cpu")
    model = load_detector(args.checkpoint, device=str(device))
    scored = score_manifest(rows, model, device)

    if not args.skip_classifier and args.classifier_manifest_out and args.classifier_scores_out:
        with open(args.classifier_manifest_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["chip_path"])
            w.writeheader()
            for r in scored:
                if r.get("detector_status") == "scored":
                    w.writerow({"chip_path": r["chip_path"]})
        result = run_classifier_manifest(args.classifier_manifest_out, args.classifier_scores_out)
        print(f"[classifier] {json.dumps({k: v for k, v in result.items() if k != 'stdout' and k != 'stderr'})}")
        probs = load_classifier_probs(args.classifier_scores_out) if result["ok"] else {}
        for r in scored:
            r["classifier_pv_prob"] = probs.get(r["chip_path"])

    fieldnames = sorted({k for r in scored for k in r.keys()})
    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(scored)
    print(f"[score_coj_chips] wrote {len(scored)} rows -> {args.output}")


if __name__ == "__main__":
    main()
