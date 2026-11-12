import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.config import Config


def preflight_module():
    source = Path(__file__).resolve().parents[2] / "deploy/huawei/preflight.py"
    spec = importlib.util.spec_from_file_location("huawei_preflight", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verifier_module():
    source = Path(__file__).resolve().parents[2] / "deploy/huawei/verify.py"
    spec = importlib.util.spec_from_file_location("huawei_verifier", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pdf_restore_binds_only_exact_existing_ids_and_empty_keys():
    source = Path(__file__).resolve().parents[2] / 'deploy/huawei/bind_restored_pdfs.py'
    spec = importlib.util.spec_from_file_location('pdf_binder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [('2504.18425', None), ('2404.16710', ''), ('2503.01879', '/pdfs/old-key.pdf'),
            ('2507.01356', None), ('2506.18248', None)]
    files = ['2504.18425.pdf', '2404.16710.pdf', '2503.01879.pdf',
             '../2507.01356.pdf', '2506.18248v2.pdf', 'unknown-paper.pdf']
    assert module.existing_pdf_bindings(rows, files) == [
        ('/pdfs/2504.18425.pdf', '2504.18425'), ('/pdfs/2404.16710.pdf', '2404.16710'),
    ]


def test_health_verifier_rejects_malformed_success_payloads():
    verifier = verifier_module()
    for live, ready in [(None, {}), ({"data": None}, {}),
                        ({"data": {"status": "up"}}, {"data": {"status": "ready", "components": None}})]:
        assert not verifier.assess(live, ready)


def test_health_verifier_allows_service_startup_before_success(monkeypatch):
    verifier = verifier_module()
    calls, sleeps = [], []
    def startup(_):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionRefusedError("service is starting")
        return {"data": {"status": "up"}}, {"data": {"status": "ready", "components": {"database": "up"}}}
    monkeypatch.setattr(verifier, "fetch_health", startup)
    monkeypatch.setattr(verifier.time, "sleep", sleeps.append)
    assert verifier.verify_health("http://127.0.0.1:9", attempts=3, interval=2)["passed"]
    assert len(calls) == 2 and sleeps == [2]


def test_health_verifier_retry_limit_still_rejects_degraded_service(monkeypatch):
    verifier = verifier_module()
    monkeypatch.setattr(verifier, "fetch_health", lambda _: (
        {"data": {"status": "up"}}, {"data": {"status": "degraded", "components": {"database": "up"}}},
    ))
    monkeypatch.setattr(verifier.time, "sleep", lambda _: None)
    result = verifier.verify_health("http://127.0.0.1:9", attempts=3)
    assert not result["passed"] and result["attempt"] == 3


def test_core_health_accepts_degraded_only_when_database_is_up():
    verifier = verifier_module()
    live = {"data": {"status": "up"}}
    ready = {"data": {"status": "degraded", "components": {"database": "up"}}}
    assert not verifier.assess(live, ready)
    assert verifier.assess(live, ready, core_only=True)
    ready["data"]["components"]["database"] = "unavailable"
    assert not verifier.assess(live, ready, core_only=True)


def test_malformed_database_url_returns_safe_failure():
    checks = preflight_module().database_checks(SimpleNamespace(
        SQLALCHEMY_DATABASE_URI="invalid credential-like-private-string",
    ))
    assert checks == [{"item": "database inspection", "ok": False, "error_type": "ArgumentError"}]
    assert "credential-like-private-string" not in str(checks)


def test_empty_database_reports_every_missing_table(monkeypatch):
    import sqlalchemy
    from app.extensions import db
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(sqlalchemy, "inspect", lambda _: SimpleNamespace(has_table=lambda *_args, **_kwargs: False))
    checks = preflight_module().database_checks(SimpleNamespace(
        SQLALCHEMY_DATABASE_URI="postgresql+psycopg://127.0.0.1/empty_test",
        KNOWLEDGE_BASE_DB_SCHEMA="knowledge_base",
    ))
    schemas = [check for check in checks if check["item"].startswith("schema ")]
    assert len(schemas) == len(db.metadata.sorted_tables)
    assert all(check["missing_table"] and not check["ok"] for check in schemas)
    assert not any(check["item"] == "database inspection" for check in checks)


def test_production_preflight_rejects_nonexistent_tls_ca(tmp_path):
    config = SimpleNamespace(**{
        key: getattr(Config, key) for key in (
            "SECRET_KEY", "AUTH_COOKIE_SECURE", "QWEN_DPO_BASE_URL",
        )
    }, SQLALCHEMY_DATABASE_URI=(
        "postgresql+psycopg://127.0.0.1/test?sslmode=verify-full&sslrootcert="
        + str(tmp_path / "missing-ca.pem")
    ))
    check = next(check for check in preflight_module().production_checks(config)
                 if check["item"] == "RDS PostgreSQL and verified TLS")
    assert not check["ok"]


def test_skill_import_json_is_optional_when_restoring_database():
    item = next(item for item in preflight_module().inspect_sources(Config)
                if item['item'] == 'skill crawl file')
    assert item['required'] is False


def test_source_preflight_does_not_require_a_tool_absent_from_deployed_catalog():
    checks = preflight_module().inspect_sources(Config)
    ppt = [item for item in checks if item['item'].startswith('literature-PPT')]
    assert len(ppt) == 3
    assert all(item['required'] is False for item in ppt)


def test_api_retrieval_does_not_require_local_model_assets():
    assert preflight_module().local_knowledge_checks({
        'KB_EMBEDDING_BACKEND': 'api', 'KB_RERANKER_BACKEND': 'api', 'KB_MILVUS_ENABLED': '0',
    }) == []


def test_local_retrieval_rejects_absent_weights_and_index():
    checks = preflight_module().local_knowledge_checks({
        'KB_EMBEDDING_BACKEND': 'local', 'KB_RERANKER_BACKEND': 'local',
        'BGE_M3_MODEL': '/nonexistent-models/bge-m3',
        'BGE_RERANKER_MODEL': '/nonexistent-models/reranker',
        'KB_MILVUS_ENABLED': '1', 'KB_MILVUS_URI': '/nonexistent-models/milvus_lite.db',
        'KB_MILVUS_COLLECTION': 'paper_chunks_bge_m3',
    })
    files = [item for item in checks if item['item'].endswith('local files')]
    assert len(files) == 2
    assert all(not item['ok'] and item['missing_files'] for item in files)
    assert not next(item for item in checks if item['item'] == 'restored local Milvus index')['ok']


def test_production_preflight_accepts_configured_api_without_platform_model():
    config = SimpleNamespace(
        SECRET_KEY='a' * 64, AUTH_COOKIE_SECURE=True, QWEN_DPO_BASE_URL='',
        USER_API_MODEL_BASE_URL='https://api.example.test/v1',
        USER_API_MODEL_NAME='existing-api-model', USER_API_MODEL_API_KEY='fixture-key',
        SQLALCHEMY_DATABASE_URI='',
    )
    for key, expected in [('fixture-key', True), ('', False)]:
        config.USER_API_MODEL_API_KEY = key
        check = next(item for item in preflight_module().production_checks(config)
                     if item['item'] == 'configured model endpoint')
        assert check['ok'] is expected


def test_database_preflight_checks_restored_skill_data_without_import_json(monkeypatch):
    import sqlalchemy
    from app.extensions import db

    class Connection:
        skill_count = 7
        ppt_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def scalar(self, query):
            if 'zhiyan.skills' in str(query):
                return self.skill_count
            return self.ppt_count if 'zhiyan.tools' in str(query) else 0

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection, dispose=lambda: None)
    inspector = SimpleNamespace(
        has_table=lambda *_args, **_kwargs: True,
        get_columns=lambda name, schema: [
            {'name': column.name} for column in db.metadata.tables[f'{schema}.{name}'].columns
        ],
    )
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(sqlalchemy, 'inspect', lambda _: inspector)
    config = SimpleNamespace(SQLALCHEMY_DATABASE_URI='postgresql+psycopg://127.0.0.1/test',
                             KNOWLEDGE_BASE_DB_SCHEMA='knowledge_base',
                             LITERATURE_PPT_RUNTIME_ROOT=Path('/nonexistent-ppt-runtime'),
                             LITERATURE_PPT_PYTHON=Path('/nonexistent-ppt-runtime/python'))
    for count in (7, 0):
        connection.skill_count = count
        checks = preflight_module().database_checks(config)
        item = next(item for item in checks if item['item'] == 'restored skill catalog')
        assert item['rows'] == count
        assert item['ok'] is bool(count)
        assert not any(item['item'].startswith('literature-PPT') for item in checks)
    connection.ppt_count = 1
    checks = preflight_module().database_checks(config)
    ppt = [item for item in checks if item['item'].startswith('literature-PPT')]
    assert len(ppt) == 3
    assert all(item['required'] and not item['ok'] for item in ppt)
