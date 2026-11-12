from app import create_app


def test_ready_accepts_verified_default_api_without_platform_model(monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from app.api import health
    from app.services.model_credentials import encrypt_api_key

    app = Flask(__name__)
    app.config.update(SECRET_KEY='fixture-secret', MODEL_CONFIG_ENCRYPTION_KEY='',
                      AGENT_READINESS_CONNECT_TIMEOUT_SECONDS=0.1)
    app.register_blueprint(health.bp, url_prefix='/health')
    with app.app_context():
        encrypted, nonce, version = encrypt_api_key('fixture-key')
    item = SimpleNamespace(encrypted_api_key=encrypted, key_nonce=nonce,
                           key_version=version, base_url='https://api.example.test/v1')
    released = []
    monkeypatch.setattr(health, 'db', SimpleNamespace(session=SimpleNamespace(
        execute=lambda *_: None,
        scalars=lambda *_: SimpleNamespace(all=lambda: [item]),
        rollback=lambda: released.append(True),
    )))
    seen = []
    def probe(url, timeout):
        assert released == [True]
        seen.append(url)
        return True
    monkeypatch.setattr(health, 'endpoint_reachable', probe)
    monkeypatch.setattr(health, 'agent_readiness', lambda app, code, has_personal_model=False: {
        'readiness': 'READY' if has_personal_model else 'UNAVAILABLE',
    })
    response = app.test_client().get('/health/ready').get_json()['data']
    assert response['status'] == 'ready'
    assert seen == ['https://api.example.test/v1']


def test_ready_rejects_unrestored_api_credentials(monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from app.api import health

    app = Flask(__name__)
    app.config.update(SECRET_KEY='fixture-secret', MODEL_CONFIG_ENCRYPTION_KEY='',
                      AGENT_READINESS_CONNECT_TIMEOUT_SECONDS=0.1)
    app.register_blueprint(health.bp, url_prefix='/health')
    invalid = SimpleNamespace(encrypted_api_key=b'old-ciphertext', key_nonce=b'x' * 12,
                              key_version='aesgcm-v1', base_url='https://api.example.test/v1')
    items = []
    monkeypatch.setattr(health, 'db', SimpleNamespace(session=SimpleNamespace(
        execute=lambda *_: None,
        scalars=lambda *_: SimpleNamespace(all=lambda: items),
        rollback=lambda: None,
    )))
    monkeypatch.setattr(health, 'endpoint_reachable', lambda *_: (_ for _ in ()).throw(
        AssertionError('Invalid credentials must not trigger a provider connection')))
    monkeypatch.setattr(health, 'agent_readiness', lambda app, code, has_personal_model=False: {
        'readiness': 'READY' if has_personal_model else 'UNAVAILABLE',
    })
    for models in ([], [invalid]):
        items[:] = models
        response = app.test_client().get('/health/ready').get_json()['data']
        assert response['status'] == 'degraded'


def test_live_health():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "up"


def test_admin_requires_login():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/api/v1/admin/overview")

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTH_REQUIRED"
