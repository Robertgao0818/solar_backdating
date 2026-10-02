import json

import pandas as pd
import pytest

from scripts.temporal import audit_aerial_gate_score_tails as audit


def selected(tmp_path):
    return pd.DataFrame([{
        'review_id': 'R0001', 'image_sha256': 'fixed-test-image',
        'image_path': str(tmp_path / 'image.png'), 'review_group': 'low_tail',
        'dino_low': True, 'yolo_low': True,
    }])


def configure(monkeypatch, identity):
    # No network; deliberately retain a prohibited old environment default.
    monkeypatch.setattr(audit.gemini, 'load_env_file', lambda _: {
        'GOOGLE_GEMINI_BASE_URL': 'https://invalid.example',
        'GEMINI_API_KEY': 'test-only', 'GEMINI_MODEL': 'gemini-3.1-pro-high',
        'GEMINI_API_FORMAT': 'native', 'GEMINI_NATIVE_PATH': '/v1beta',
    })
    calls = []

    def post(**kwargs):
        calls.append(kwargs['model'])
        return {'modelVersion': identity}

    monkeypatch.setattr(audit.gemini, 'post_native_generate_content', post)
    monkeypatch.setattr(audit.gemini, 'native_response_text', lambda _: json.dumps({
        'observations': [{'image_index': 1, 'pv_present': False, 'confidence': .9,
                          'quality_flag': 'usable', 'scene': 'roof',
                          'evidence': 'Roof seams, no panel grid', 'notes': ''}],
    }))
    return calls


def test_retired_environment_model_cannot_override_current_default(tmp_path, monkeypatch):
    calls = configure(monkeypatch, 'gemini-3.8-flash')
    audit.review(selected(tmp_path), tmp_path, tmp_path/'unused.env', 1, 1)
    assert calls == ['gemini-3.8-flash']
    summary = json.loads((tmp_path/'gemini_summary.json').read_text())
    assert summary['requested_model'] == 'gemini-3.8-flash'
    assert summary['reviewed_n'] == 1


def test_pro_response_is_rejected_and_retained_for_diagnosis(tmp_path, monkeypatch):
    calls = configure(monkeypatch, 'gemini-pro-default')
    with pytest.raises(RuntimeError, match='Canary failed'):
        audit.review(selected(tmp_path), tmp_path, tmp_path/'unused.env', 1, 1)
    record = json.loads((tmp_path/'responses/batch_0000.json').read_text())
    assert not record['ok']
    assert record['returned_model'] == 'gemini-pro-default'
    assert calls == ['gemini-3.8-flash', 'gemini-3.8-flash']
    assert not (tmp_path/'gemini_review.csv').exists()
