"""Regression tests for the final-round backend optimizations."""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

from app import create_app
from app.api.model_configs import ModelVerificationError, verify_model_by_type, verify_openai_compatible
from app.llm_stream import stream_openai_compatible_chat

CORE_ROOT = Path(__file__).resolve().parents[1] / "app" / "agents" / "literature_search" / "core"


def load_literature_core():
    """Import the vendored agent core in isolation.

    Several vendored runtimes ship top-level `config` and `src` packages, so
    the ones already imported by other tests are set aside during the import.
    """
    saved = {name: module for name, module in sys.modules.items() if name.split(".")[0] in {"config", "src"}}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(CORE_ROOT))
    try:
        nodes = importlib.import_module("src.agent.literature_nodes")
        fishbone = importlib.import_module("src.tools.annual_fishbone")
        arxiv = importlib.import_module("src.tools.arxiv")
    finally:
        sys.path.remove(str(CORE_ROOT))
        for name in [name for name in sys.modules if name.split(".")[0] in {"config", "src"}]:
            del sys.modules[name]
        sys.modules.update(saved)
    return nodes, fishbone, arxiv


_nodes, _fishbone, _arxiv = load_literature_core()
SearchResultCache = _nodes.SearchResultCache
combined_query_term = _nodes.combined_query_term
render_checkpoints = _fishbone.render_checkpoints
build_query = _arxiv.build_query


class FakeStream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def sse_body(chunks: list[dict]) -> bytes:
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode("utf-8")


def stream_app():
    return create_app({
        "TESTING": True,
        "USER_API_MODEL_BASE_URL": "https://example.test/v1",
        "USER_API_MODEL_NAME": "demo-model",
        "USER_API_MODEL_API_KEY": "demo-key",
    })


def test_stream_yields_reasoning_then_text_and_usage(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "想一下"}}]},
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
        {"choices": [], "usage": {"total_tokens": 7}},
    ]
    seen = dict()

    def fake_urlopen(api_request, timeout=None):
        seen["body"] = json.loads(api_request.data.decode("utf-8"))
        seen["auth"] = api_request.get_header("Authorization")
        return FakeStream(sse_body(chunks))

    monkeypatch.setattr("app.llm_stream.request.urlopen", fake_urlopen)
    with stream_app().app_context():
        events = list(stream_openai_compatible_chat(messages=[{"role": "user", "content": "hi"}], model="api_model"))

    assert [event["type"] for event in events] == ["reasoning", "delta", "delta", "done"]
    assert events[-1]["content"] == "你好"
    assert events[-1]["usage"] == {"total_tokens": 7}
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "demo-model"
    assert seen["auth"] == "Bearer demo-key"


def test_stream_falls_back_to_buffered_call_when_stream_has_no_text(monkeypatch):
    monkeypatch.setattr("app.llm_stream.request.urlopen", lambda *_a, **_k: FakeStream(sse_body([])))
    monkeypatch.setattr(
        "app.llm_stream.run_openai_compatible_chat",
        lambda **_kwargs: {"content": "完整回答", "usage": {"total_tokens": 3}, "model": "demo-model"},
    )
    with stream_app().app_context():
        events = list(stream_openai_compatible_chat(messages=[{"role": "user", "content": "hi"}], model="api_model"))

    assert events == [
        {"type": "delta", "text": "完整回答"},
        {"type": "done", "content": "完整回答", "usage": {"total_tokens": 3}, "model": "demo-model"},
    ]


def test_chat_verification_retries_with_minimal_request_on_strict_gateways(monkeypatch):
    from urllib import error as url_error

    bodies = []

    def fake_urlopen(api_request, timeout=None):
        body = json.loads(api_request.data.decode("utf-8"))
        bodies.append(body)
        if "chat_template_kwargs" in body:
            raise url_error.HTTPError(api_request.full_url, 400, "Bad Request", None, io.BytesIO(b"unknown field"))
        return FakeStream(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode("utf-8"))

    monkeypatch.setattr("app.api.model_configs.url_request.urlopen", fake_urlopen)
    verify_openai_compatible(base_url="https://strict.test/v1", model_name="m", api_key="k", timeout=5)

    assert len(bodies) == 2
    assert set(bodies[1]) == {"model", "messages"}


def test_embedding_models_are_verified_against_the_embeddings_endpoint(monkeypatch):
    urls = []

    def fake_urlopen(api_request, timeout=None):
        urls.append(api_request.full_url)
        return FakeStream(json.dumps({"data": [{"embedding": [0.1, 0.2]}]}).encode("utf-8"))

    monkeypatch.setattr("app.api.model_configs.url_request.urlopen", fake_urlopen)
    verify_model_by_type(model_type_code="embedding", base_url="https://e.test/v1", model_name="bge", api_key="k", timeout=5)

    assert urls == ["https://e.test/v1/embeddings"]


def test_auth_failure_is_reported_without_retry(monkeypatch):
    from urllib import error as url_error

    calls = []

    def fake_urlopen(api_request, timeout=None):
        calls.append(1)
        raise url_error.HTTPError(api_request.full_url, 401, "Unauthorized", None, io.BytesIO(b""))

    monkeypatch.setattr("app.api.model_configs.url_request.urlopen", fake_urlopen)
    with pytest.raises(ModelVerificationError) as caught:
        verify_openai_compatible(base_url="https://a.test/v1", model_name="m", api_key="bad", timeout=5)

    assert caught.value.code == "MODEL_AUTH_FAILED"
    assert len(calls) == 1


def test_combined_arxiv_term_keeps_short_phrases_and_relaxes_long_queries():
    assert combined_query_term("graph neural network") == '"graph neural network"'
    assert combined_query_term("retrieval-augmented generation for scientific question answering") == (
        '("retrieval-augmented" AND "generation" AND "scientific" AND "question" AND "answering")'
    )
    assert combined_query_term("检索增强生成") is None
    assert combined_query_term('"exact phrase" OR other') == '("exact phrase" OR other)'


def test_combined_arxiv_query_is_grouped_before_the_date_filter():
    combined = "(" + " OR ".join([combined_query_term("graph neural network"), combined_query_term("GNN survey")]) + ")"
    query = build_query(combined, [], [], 2023, 2026)

    assert query == '( all:"graph neural network" OR all:"GNN survey" ) AND submittedDate:[202301010000 TO 202612312359]'


def test_timeline_is_redrawn_a_bounded_number_of_times():
    assert render_checkpoints(3) == {1, 2, 3}
    checkpoints = render_checkpoints(50)
    assert len(checkpoints) <= 6
    assert 50 in checkpoints
    assert render_checkpoints(0) == set()


def test_search_cache_expires_and_ignores_empty_results():
    cache = SearchResultCache(ttl_seconds=0.0)
    cache.put("empty", [])
    assert cache.get("empty") is None
    cache.put("k", [{"title": "t"}])
    assert cache.get("k") is None  # ttl 0: already expired

    cache = SearchResultCache(ttl_seconds=60.0, max_entries=1)
    cache.put("a", [1])
    cache.put("b", [2])
    assert cache.get("a") is None
    assert cache.get("b") == [2]


def test_throttled_or_unreachable_sources_are_skipped_not_retried_per_query():
    from urllib import error as url_error

    throttled = url_error.HTTPError("https://export.arxiv.org", 429, "Too Many Requests", None, io.BytesIO(b""))
    rejected = url_error.HTTPError("https://export.arxiv.org", 400, "Bad Request", None, io.BytesIO(b""))

    assert _nodes.is_source_unavailable(throttled) is True
    assert _nodes.is_source_unavailable(TimeoutError("slow")) is True
    assert _nodes.is_source_unavailable(rejected) is False
    assert _nodes.is_source_unavailable(ValueError("bad query")) is False


def test_arxiv_fetch_gives_up_within_its_time_budget(monkeypatch):
    from urllib import error as url_error

    calls = []
    sleeps = []

    def always_throttled(api_request, timeout=None):
        calls.append(timeout)
        raise url_error.HTTPError(api_request.full_url, 429, "Too Many Requests", {"Retry-After": "600"}, io.BytesIO(b""))

    monkeypatch.setattr(_arxiv.urllib.request, "urlopen", always_throttled)
    monkeypatch.setattr(_arxiv.time, "sleep", sleeps.append)
    settings = _arxiv.get_settings()
    api_request = _arxiv.urllib.request.Request("https://export.arxiv.org/api/query?search_query=all:x")

    with pytest.raises(url_error.HTTPError):
        _arxiv.fetch_feed(api_request, settings)

    assert len(calls) == settings.arxiv_max_retries + 1
    assert sleeps and max(sleeps) <= _arxiv.ARXIV_MAX_RETRY_DELAY_SECONDS
