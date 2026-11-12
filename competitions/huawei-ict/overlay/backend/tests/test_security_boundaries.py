"""Offline security regressions; no database, model calls, or fixture files."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import validate_json_object
from app.api.model_configs import normalize_base_url
from app.api.tasks import task_artifact
from app.services.agent_readiness import endpoint_reachable


@pytest.mark.parametrize("body", ['[1]', '[]', '"value"', 'true', '42', 'null', '{broken'])
def test_non_object_json_rejected_before_route(body):
    app = Flask(__name__)
    app.before_request(validate_json_object)
    app.add_url_rule('/api/v1/auth/login', view_func=lambda: 'unexpected', methods=['POST'])
    response = app.test_client().post('/api/v1/auth/login', data=body, content_type='application/json')
    assert response.status_code == 400
    assert response.json['error']['code'] == 'INVALID_JSON_OBJECT'


@pytest.mark.parametrize("path,body,content_type", [
    ('/api/v1/auth/login', '{}', 'application/json'),
    ('/api/v1/knowledge-base/batch', '[1]', 'application/json'),
    ('/api/v1/uploads/papers', 'file', 'multipart/form-data'),
])
def test_existing_payload_contracts_preserved(path, body, content_type):
    app = Flask(__name__)
    with app.test_request_context(path, method='POST', data=body, content_type=content_type):
        assert validate_json_object() is None


@pytest.mark.parametrize('value', [
    'http://[broken', 'https://host:invalid', 'https://host:65536',
    'https://host/v1?key=secret', 'https://host/v1#fragment',
    'https://ho\nst/v1', 'https://user:secret@host/v1',
])
def test_invalid_model_url_returns_validation_failure(value):
    assert normalize_base_url(value) is None


@pytest.mark.parametrize('value', ['https://api.deepseek.com', 'http://127.0.0.1:9000/v1'])
def test_existing_public_and_local_model_urls_preserved(value):
    assert normalize_base_url(value + '/') == value


@pytest.mark.parametrize('value', ['http://[broken', 'https://host:invalid', 'https://host:65536'])
def test_bad_legacy_url_does_not_crash_readiness(value):
    assert endpoint_reachable(value, 0.01) is False


@pytest.mark.parametrize('kind', ['figure-zh-svg', 'figure-en-svg'])
def test_svg_preview_is_sandboxed_without_changing_download_url(monkeypatch, kind):
    fixture = Path(__file__).resolve()
    app = Flask(__name__)
    app.config['ACADEMIC_FIGURE_DATA_DIR'] = fixture.parent
    task = SimpleNamespace(output_json={'artifacts': {kind: str(fixture)}})
    monkeypatch.setattr('app.api.tasks.find_task', lambda _: task)
    with app.test_request_context():
        response = task_artifact('owned-task', kind)
        try:
            assert response.status_code == 200
            assert response.mimetype == 'image/svg+xml'
            assert response.headers['Content-Disposition'].startswith('inline')
            policy = response.headers['Content-Security-Policy']
            assert 'sandbox;' in policy
            assert 'allow-scripts' not in policy
            assert 'allow-same-origin' not in policy
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        finally:
            response.close()
