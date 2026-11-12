from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from knowledge_base_runtime.backend.service import retrieval_backends
from knowledge_base_runtime.backend.config import settings


def test_dashboard_status_never_probes_configured_services(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("dashboard status must not contact services or invoke model APIs")

    for name in ("embedding_available", "reranker_available", "elasticsearch_available", "milvus_available", "_api_json_request"):
        monkeypatch.setattr(retrieval_backends, name, forbidden)
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_ENABLED", True)
    monkeypatch.setattr(retrieval_backends, "ELASTICSEARCH_ENABLED", True)
    monkeypatch.setattr(retrieval_backends, "ELASTICSEARCH_URL", "http://es.test")
    monkeypatch.setattr(retrieval_backends, "ELASTICSEARCH_INDEX", "papers")
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_URI", "/data/index.db")
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_COLLECTION", "papers")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_BASE_URL", "https://embed.test")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_MODEL", "embedding")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_BASE_URL", "https://rerank.test")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_MODEL", "reranker")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_KEY", "test-key")

    statuses = retrieval_backends.external_status()

    assert {status["name"] for status in statuses} == {"Elasticsearch", "Embedding", "Reranker", "Milvus"}
    assert all(status["healthy"] is None and status["uptime"] == "已配置，未检测连接" for status in statuses)
    assert "test-key" not in str(statuses)


def test_dashboard_status_reports_missing_keys_without_requiring_auth(monkeypatch):
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_ENABLED", True)
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_BASE_URL", "https://embed.test")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_MODEL", "embedding")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_KEY", "")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_BASE_URL", "https://rerank.test")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_MODEL", "reranker")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_KEY", "")
    statuses = {item["name"]: item for item in retrieval_backends.external_status()}
    for name in ("Embedding", "Reranker"):
        assert statuses[name]["healthy"] is None
        assert statuses[name]["uptime"] == "未提供 API 密钥（如服务需要）；未检测连接"


def test_dashboard_status_distinguishes_missing_and_disabled_configuration(monkeypatch):
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_ENABLED", True)
    monkeypatch.setattr(retrieval_backends, "ELASTICSEARCH_ENABLED", False)
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_BASE_URL", "")
    statuses = {item["name"]: item for item in retrieval_backends.external_status()}
    assert statuses["Elasticsearch"]["healthy"] is None
    assert statuses["Elasticsearch"]["uptime"] == "已禁用"
    assert statuses["Embedding"]["healthy"] is False
    assert statuses["Embedding"]["uptime"] == "配置不完整"


def test_milvus_uri_preserves_remote_addresses_and_resolves_local_paths():
    assert settings._milvus_uri('http://milvus:19530') == 'http://milvus:19530'
    assert settings._milvus_uri('https://milvus.example:19530') == 'https://milvus.example:19530'
    assert settings._milvus_uri('/data/milvus.db') == '/data/milvus.db'
    assert settings._milvus_uri('index.db') == str(settings.BASE_DIR / 'index.db')


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_embedding_backend_sends_batch_and_validates_dimension(monkeypatch):
    captured = {}

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["body"] = json.loads(api_request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"embeddings": [[0.1] * 1024, [0.2] * 1024]})

    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "ollama")
    monkeypatch.setattr(retrieval_backends, "OLLAMA_EMBED_BASE_URL", "http://ollama.test:11434")
    monkeypatch.setattr(retrieval_backends, "OLLAMA_EMBED_MODEL", "bge-m3:latest")
    monkeypatch.setattr(retrieval_backends.urllib.request, "urlopen", fake_urlopen)

    vectors = retrieval_backends.embed_texts(["文本一", "text two"])

    assert captured["url"] == "http://ollama.test:11434/api/embed"
    assert captured["body"] == {"model": "bge-m3:latest", "input": ["文本一", "text two"]}
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024


def test_ollama_embedding_backend_rejects_wrong_dimension(monkeypatch):
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "ollama")
    monkeypatch.setattr(
        retrieval_backends.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse({"embeddings": [[0.1] * 768]}),
    )

    with pytest.raises(RuntimeError, match="does not match KB_MILVUS_DIM=1024"):
        retrieval_backends.embed_texts(["dimension test"])


def test_api_embedding_backend_uses_openai_embeddings_contract(monkeypatch):
    captured = {}

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["headers"] = dict(api_request.header_items())
        captured["body"] = json.loads(api_request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {"data": [{"index": 1, "embedding": [0.2] * 1024}, {"index": 0, "embedding": [0.1] * 1024}]}
        )

    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_BASE_URL", "https://embed.test/v1")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_ENDPOINT", "/embeddings")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_KEY", "secret")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_MODEL", "embed-model")
    monkeypatch.setattr(retrieval_backends.urllib.request, "urlopen", fake_urlopen)

    vectors = retrieval_backends.embed_texts(["文本一", "文本二"])

    assert captured["url"] == "https://embed.test/v1/embeddings"
    assert captured["body"] == {"model": "embed-model", "input": ["文本一", "文本二"]}
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert vectors[0][0] == 0.1
    assert vectors[1][0] == 0.2


def test_api_reranker_maps_indexed_scores(monkeypatch):
    captured = {}

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["body"] = json.loads(api_request.data.decode("utf-8"))
        return FakeResponse(
            {"results": [{"index": 1, "relevance_score": 0.2}, {"index": 0, "relevance_score": 0.9}]}
        )

    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_BASE_URL", "https://rerank.test/v1")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_ENDPOINT", "/rerank")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_KEY", "secret")
    monkeypatch.setattr(retrieval_backends, "KB_RERANKER_API_MODEL", "rerank-model")
    monkeypatch.setattr(retrieval_backends.urllib.request, "urlopen", fake_urlopen)

    scores = retrieval_backends.ApiReranker().score("query", ["doc a", "doc b"])

    assert captured["url"] == "https://rerank.test/v1/rerank"
    assert captured["body"]["top_n"] == 2
    assert scores == [0.9, 0.2]


@pytest.mark.parametrize('results', [
    [{'index': 0, 'relevance_score': 0.4}],
    [{'index': 0, 'relevance_score': 0.4}, {'index': 0, 'relevance_score': 0.2}],
    [{'index': 0, 'relevance_score': float('nan')}, {'index': 1, 'relevance_score': 0.2}],
])
def test_reranker_rejects_incomplete_or_invalid_results(monkeypatch, results):
    monkeypatch.setattr(retrieval_backends, 'KB_RERANKER_API_BASE_URL', 'https://rerank.test/v1')
    monkeypatch.setattr(retrieval_backends, '_api_json_request', lambda *_args: {'results': results})
    with pytest.raises(RuntimeError):
        retrieval_backends.ApiReranker().score('query', ['a', 'b'])


def test_embedding_rejects_duplicate_indexes(monkeypatch):
    monkeypatch.setattr(retrieval_backends, 'KB_EMBEDDING_API_BASE_URL', 'https://embed.test/v1')
    monkeypatch.setattr(retrieval_backends, '_api_json_request', lambda *_args: {
        'data': [{'index': 0, 'embedding': [0.1] * 1024}, {'index': 0, 'embedding': [0.2] * 1024}]
    })
    with pytest.raises(RuntimeError, match='indexes'):
        retrieval_backends._embed_texts_api(['a', 'b'])


def test_submission_recommendation_uses_configured_remote_bge(monkeypatch):
    import importlib.util
    import logging
    import sys
    from pathlib import Path
    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace(ModelConfig=object))
    monkeypatch.setitem(sys.modules, 'utils.logger', SimpleNamespace(get_logger=lambda *_: logging.getLogger('test')))
    path = Path(__file__).resolve().parents[1] / 'app/agents/contribution_recommendation/models/__init__.py'
    spec = importlib.util.spec_from_file_location('recommendation_model_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(retrieval_backends, 'KB_EMBEDDING_BACKEND', 'api')
    monkeypatch.setattr(retrieval_backends, 'KB_RERANKER_BACKEND', 'api')
    monkeypatch.setattr(retrieval_backends, 'embed_texts', lambda texts: [[1.0] for _ in texts])
    monkeypatch.setattr(retrieval_backends, 'ApiReranker', lambda: SimpleNamespace(score=lambda query, docs: [0.1, 0.9]))
    service = module.ModelService(config=object())
    assert service.embed(['a']) == [[1.0]]
    assert service.rerank('q', ['a', 'b'], top_k=1) == [{'index': 1, 'score': 0.9, 'text': 'b'}]


def test_api_embedding_retries_connection_reset(monkeypatch):
    attempts = 0

    def flaky_urlopen(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(10053, "connection aborted")
        return FakeResponse({"data": [{"index": 0, "embedding": [0.1] * 1024}]})

    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_BACKEND", "api")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_BASE_URL", "https://embed.test/v1")
    monkeypatch.setattr(retrieval_backends, "KB_EMBEDDING_API_MAX_RETRIES", 2)
    monkeypatch.setattr(retrieval_backends.urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(retrieval_backends.time, "sleep", lambda _seconds: None)

    vectors = retrieval_backends.embed_texts(["retry"])

    assert attempts == 2
    assert len(vectors[0]) == 1024
