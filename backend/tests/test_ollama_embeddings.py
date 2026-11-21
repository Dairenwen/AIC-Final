from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from knowledge_base_runtime.backend.service import retrieval_backends


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
