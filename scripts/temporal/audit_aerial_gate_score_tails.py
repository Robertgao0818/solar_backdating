#!/usr/bin/env python3
"""Read paired aerial gate scores and blind-review reference-positive tails.

This diagnostic never changes detector labels, model weights, or thresholds.
Data products are written outside git. Input labels are detector-derived,
not ground truth; largest adjacent gaps are exploratory review boundaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.validation import gemini_solar_image_review as gemini

# Owner decision 2026-10-02: Pro reviews are retired; never silently route
# fresh reviews to Pro or to the legacy environment-file model.
REVIEW_MODELS = ('gemini-3.8-flash', 'gemini-flash-3.5-lite')
DEFAULT_REVIEW_MODEL = REVIEW_MODELS[0]

PROMPT = """Review these {count} high-resolution aerial images INDEPENDENTLY.
They are different windows and may show different locations. Images are in
input order 1..{count}. No marker or outline is present. Decide whether ANY
solar photovoltaic modules are visible anywhere in EACH entire image,
including ground-mounted solar farms and partially clipped arrays at edges.
Do not infer one image's label from another. Do not count roof stripes,
skylights, thermal water heaters, HVAC, shadows, vegetation, or repeated
round objects as PV. Module grids and repeated rectangular panel borders
are supporting evidence. Use null if the image is not interpretable or the
visual evidence is insufficient; do not force a binary guess. Confidence
is your confidence in the stated decision, not probability of PV presence.
Return ONLY one JSON object with an observations array of exactly {count}
objects in input order. Each object contains image_index (integer),
pv_present (true/false/null), confidence (number 0..1), quality_flag
(usable/ambiguous/unusable), scene (roof/ground_solar/nonroof/mixed/unclear),
evidence (specific observed visual features and location within the image),
notes (brief uncertainty or edge-clipping caveat)."""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(root: Path, out: Path):
    inputs = []
    scored = []
    boundaries = {}
    for arm in ['vit30', 'yolo30']:
        files = sorted((root / 'train/scores').glob(f'{arm}_fold[0-3].csv'))
        if len(files) != 4:
            raise ValueError(f'Expected four existing folds for {arm}')
        inputs += files
        frames = [pd.read_csv(p).assign(fold=int(p.stem[-1])) for p in files]
        frame = pd.concat(frames, ignore_index=True)
        if frame.duplicated(['grid_id', 'chip_index']).any():
            raise ValueError('Duplicate chip keys')
        if not frame.score.between(0, 1).all():
            raise ValueError('Missing or invalid score')
        scored.append(frame.rename(columns={'score': arm}))
    combined = scored[0].merge(scored[1], on=['grid_id', 'chip_index', 'fold'],
                              validate='one_to_one', how='outer', indicator=True)
    if not combined['_merge'].eq('both').all():
        raise ValueError('The paired score populations differ')
    manifests = []
    for grid in sorted(combined.grid_id.unique()):
        path = root / 'chips' / grid / 'manifest.csv'
        inputs.append(path)
        manifests.append(pd.read_csv(path))
    combined = combined.drop(columns='_merge').merge(
        pd.concat(manifests), on=['grid_id', 'chip_index'],
        validate='one_to_one', how='left')
    for label in ['y_pre', 'y_post']:
        if combined[label].isna().any():
            raise ValueError('Missing reference label')
        combined[label] = combined[label].astype(str).str.lower().eq('true')
    gaps = []
    for arm in ['vit30', 'yolo30']:
        for population, mask in [('all', np.ones(len(combined), dtype=bool)),
                                 ('reference_post_positive', combined.y_post),
                                 ('reference_pre_positive', combined.y_pre)]:
            values = np.sort(combined.loc[mask, arm].to_numpy())
            diffs = np.diff(values)
            for i in np.argsort(-diffs, kind='stable')[:20]:
                gaps.append(dict(arm=arm, population=population,
                                 lower=float(values[i]), upper=float(values[i+1]),
                                 gap=float(diffs[i]), count_below=int(i+1),
                                 population_n=int(len(values))))
            if population == 'reference_post_positive':
                i = int(np.argmax(diffs))
                boundaries[arm] = dict(lower=float(values[i]), upper=float(values[i+1]),
                                       midpoint=float((values[i]+values[i+1])/2),
                                       gap=float(diffs[i]), count_below=int(i+1))
    pd.DataFrame(gaps).to_csv(out / 'score_gaps.csv', index=False)
    combined['dino_low'] = combined.y_post & (combined.vit30 < boundaries['vit30']['midpoint'])
    combined['yolo_low'] = combined.y_post & (combined.yolo30 < boundaries['yolo30']['midpoint'])
    selected = combined[combined.dino_low | combined.yolo_low].copy()
    selected['review_group'] = 'low_tail'
    controls = []
    for arm in ['vit30', 'yolo30']:
        c = combined[combined.y_post & (combined[arm] >= boundaries[arm]['upper'])].nsmallest(6, arm).copy()
        c['review_group'] = f'{arm}_above_gap'
        controls.append(c)
    for label in [True, False]:
        c = combined[(combined.y_post == label) & ~(combined.dino_low | combined.yolo_low)].sample(6, random_state=20261002).copy()
        c['review_group'] = 'reference_positive_control' if label else 'reference_negative_control'
        controls.append(c)
    selected = pd.concat([selected, *controls]).drop_duplicates(['grid_id', 'chip_index'])
    selected['image_path'] = [str(root / 'chips' / row.grid_id / 'images' / f'{row.chip_index:05d}.png') for row in selected.itertuples()]
    if not all(Path(p).is_file() for p in selected.image_path):
        raise ValueError('A selected image is missing')
    # Fixed shuffled order obscures reference and tail/control membership.
    selected = selected.sample(frac=1, random_state=20261002).reset_index(drop=True)
    selected['review_id'] = [f'R{i+1:04d}' for i in range(len(selected))]
    selected['image_sha256'] = [digest(Path(p)) for p in selected.image_path]
    selected.to_csv(out / 'review_manifest.csv', index=False)
    combined.to_csv(out / 'paired_scores.csv', index=False)
    summary = dict(scope='Cape Town only, paired folds 0-3',
                   chips_n=len(combined), grids_n=int(combined.grid_id.nunique()),
                   post_positive_n=int(combined.y_post.sum()), boundaries=boundaries,
                   dino_low_n=int(combined.dino_low.sum()), yolo_low_n=int(combined.yolo_low.sum()),
                   low_union_n=int((combined.dino_low | combined.yolo_low).sum()),
                   controls_n=int((selected.review_group != 'low_tail').sum()),
                   review_n=len(selected), prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
                   inputs=[dict(path=str(p), sha256=digest(p)) for p in inputs],
                   caveats=['Adjacent gaps are exploratory, not validated cutoffs.',
                            'Only reference-positive tails are exhaustively reviewed; not every low-scoring empty chip.',
                            'Early stopping used the scored validation fold; these are not untouched holdout results.',
                            'YOLO zero is censored below detector confidence floor 0.001.',
                            'Gemini labels are auxiliary model judgments, not human truth.'])
    (out / 'selection_summary.json').write_text(json.dumps(summary, indent=2))
    return selected, summary


def validate(parsed, count):
    items = parsed.get('observations')
    if not isinstance(items, list) or len(items) != count:
        raise ValueError('Wrong observation count')
    indexed = {}
    for item in items:
        i = item.get('image_index')
        if type(i) is not int or i < 1 or i > count or i in indexed:
            raise ValueError('Invalid or duplicate image index')
        if item.get('pv_present') is not None and type(item['pv_present']) is not bool:
            raise ValueError('Invalid PV state')
        if 'pv_present' not in item or type(item.get('confidence')) not in (float, int) or not 0 <= item['confidence'] <= 1:
            raise ValueError('Invalid confidence or missing state')
        if item.get('quality_flag') not in ['usable', 'ambiguous', 'unusable']:
            raise ValueError('Invalid quality')
        if not isinstance(item.get('evidence'), str) or not item['evidence'].strip():
            raise ValueError('Missing visual evidence')
        if item.get('scene') not in ['roof', 'ground_solar', 'nonroof', 'mixed', 'unclear']:
            raise ValueError('Invalid scene')
        indexed[i] = item
    return [indexed[i] for i in range(1, count+1)]


def review(selected, out, env_path, workers, batch_size, model_override=None, max_tokens=8192):
    env = gemini.load_env_file(env_path)
    config = {name: gemini.env_value(env, name) for name in ['GOOGLE_GEMINI_BASE_URL', 'GEMINI_API_KEY', 'GEMINI_MODEL', 'GEMINI_API_FORMAT', 'GEMINI_NATIVE_PATH']}
    model = model_override or DEFAULT_REVIEW_MODEL
    if model not in REVIEW_MODELS:
        raise ValueError(f'Retired or unsupported review model: {model}; use {REVIEW_MODELS}')
    records = selected.to_dict('records')
    groups = [records[i:i+batch_size] for i in range(0, len(records), batch_size)]
    cache = out / 'responses'
    cache.mkdir(exist_ok=True)
    limiter = gemini.RateLimiter(2)

    def one(number, group):
        path = cache / f'batch_{number:04d}.json'
        keys = [r['review_id'] for r in group]
        hashes = [r['image_sha256'] for r in group]
        if path.exists():
            old = json.loads(path.read_text())
            if old.get('review_ids') != keys or old.get('image_sha256') != hashes or old.get('model') != model:
                raise ValueError('Cached batch provenance mismatch')
            if old.get('ok'):
                validate(old['parsed'], len(group))
                return old
            history = cache / 'failed_attempts'
            history.mkdir(exist_ok=True)
            (history / f'{path.stem}_{time.time_ns()}.json').write_text(path.read_text())
        result = dict(review_ids=keys, image_sha256=hashes, model=model,
                      max_output_tokens=max_tokens, ok=False)
        started = time.time()
        for attempt in range(1, 3):
            try:
                kwargs = dict(base_url=config['GOOGLE_GEMINI_BASE_URL'], api_key=config['GEMINI_API_KEY'],
                              model=model, prompt=PROMPT.format(count=len(group)),
                              image_paths=[Path(r['image_path']) for r in group],
                              max_tokens=max_tokens, timeout=120, limiter=limiter, max_transport_attempts=2)
                if config['GEMINI_API_FORMAT'] == 'native':
                    raw = gemini.post_native_generate_content(**kwargs, native_path=config['GEMINI_NATIVE_PATH'] or '/v1beta', response_mime_type='application/json')
                    response = gemini.native_response_text(raw)
                    identity = raw.get('modelVersion')
                elif config['GEMINI_API_FORMAT'] == 'openai':
                    raw = gemini.post_chat_completion(**kwargs)
                    response = gemini.response_text(raw)
                    identity = raw.get('model')
                else:
                    raise ValueError('Unsupported configured transport')
                result.update(raw=raw, response_text=response, returned_model=identity)
                if identity and 'gemini' not in identity.lower():
                    raise ValueError('Response model is not Gemini')
                if identity and 'pro' in identity.lower():
                    raise ValueError('Pro is retired: gateway returned a prohibited model family')
                parsed = gemini.extract_json_object(response)
                validate(parsed, len(group))
                result.update(parsed=parsed, ok=True, attempt=attempt)
                break
            except Exception as exc:
                # Do not retain URL/key or server-body errors in logs.
                result.update(error_type=type(exc).__name__, attempt=attempt)
        result['elapsed_sec'] = round(time.time() - started, 2)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        tmp.replace(path)
        return result

    # One real batch checks credentials, model transport, and schema before fan-out.
    first = one(0, groups[0])
    if not first['ok']:
        raise RuntimeError(f"Canary failed: {first.get('error_type')}; saved response metadata")
    print(f'CANARY_OK model={model} images={len(groups[0])}', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, i, group) for i, group in enumerate(groups[1:], 1)]
        for done, future in enumerate(as_completed(futures), 2):
            result = future.result()
            print(f'BATCH {done}/{len(groups)} ok={result["ok"]}', flush=True)
    observations = []
    failures = []
    for i, group in enumerate(groups):
        result = json.loads((cache / f'batch_{i:04d}.json').read_text())
        if not result['ok']:
            failures.extend(result['review_ids'])
            continue
        for source, judgment in zip(group, validate(result['parsed'], len(group))):
            observations.append({**source, **judgment, 'requested_model': model,
                                 'returned_model': result.get('returned_model'), 'batch_id': i})
    labels = pd.DataFrame(observations)
    labels.to_csv(out / 'gemini_review.csv', index=False)
    summary = dict(requested_model=model, selected_n=len(selected), reviewed_n=len(labels), failed_ids=failures, groups={})
    for name, frame in labels.groupby('review_group'):
        summary['groups'][name] = dict(n=len(frame), pv_present=int(frame.pv_present.eq(True).sum()),
                                      pv_absent=int(frame.pv_present.eq(False).sum()), uncertain=int(frame.pv_present.isna().sum()))
    for column in ['dino_low', 'yolo_low']:
        frame = labels[labels[column]]
        summary[column] = dict(n=len(frame), pv_present=int(frame.pv_present.eq(True).sum()),
                               pv_absent=int(frame.pv_present.eq(False).sum()), uncertain=int(frame.pv_present.isna().sum()))
    (out / 'gemini_summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    if failures:
        raise RuntimeError('Incomplete review; rerun to retry failed batches')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/home/gao/zasolar_data/gate_pilot_2026-09/learning_curve_2026-09-28'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=REPO / '.env.gemini.local')
    parser.add_argument('--review', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--batch-size', type=int, default=6)
    parser.add_argument('--max-tokens', type=int, default=8192,
                        help='Output budget; increase on resumption to retry truncated failed batches')
    parser.add_argument('--model', choices=REVIEW_MODELS, default=DEFAULT_REVIEW_MODEL,
                        help='Review model (Pro retired); ignores legacy environment-file model')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    selected, summary = build_manifest(args.root, args.out)
    print(json.dumps({k: v for k, v in summary.items() if k != 'inputs'}, indent=2), flush=True)
    if args.review:
        review(selected, args.out, args.env_file, args.workers, args.batch_size,
               args.model, args.max_tokens)


if __name__ == '__main__':
    main()
