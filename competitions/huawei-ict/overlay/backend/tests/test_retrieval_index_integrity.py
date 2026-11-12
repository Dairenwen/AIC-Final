from contextlib import contextmanager
import sqlite3

import pytest

from knowledge_base_runtime.backend.service import retrieval_index
from knowledge_base_runtime.backend.service import retrieval_backends
from knowledge_base_runtime.backend.service.search import HybridSearchService, SearchRequest


def test_rebuild_selects_only_active_papers(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, delete_time TEXT)")
    conn.executemany("INSERT INTO papers VALUES (?, ?)", [("active", None), ("deleted", "2026-01-01")])

    @contextmanager
    def db():
        yield conn

    monkeypatch.setattr(retrieval_index, "get_db", db)
    try:
        assert [paper["id"] for paper in retrieval_index._fetch_papers()] == ["active"]
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_stale_search_index_hits_are_excluded_before_reranking(mode):
    reranked = []

    class Reranker:
        def score(self, query, documents):
            reranked.extend(documents)
            return [1.0] * len(documents)

    service = HybridSearchService(
        semantic_search=lambda query, limit: [
            {"paper_id": "deleted", "content": "deleted vector content"},
            {"paper_id": "active", "content": "active vector content"},
            {"paper_id": "orphan", "content": "orphan vector content"},
        ],
        keyword_search=lambda query, filters, limit: [
            {"paper_id": "deleted", "title": "deleted keyword", "_total_hits": 2},
            {"paper_id": "active", "title": "active keyword", "_total_hits": 2},
        ],
        metadata_provider=lambda ids: {"active": {"id": "active", "title": "active title"}},
        reranker=Reranker(),
    )
    result = service.search(SearchRequest(query="query", mode=mode))
    assert result.error is None
    assert [item.paper_id for item in result.results] == ["active"]
    assert result.total_candidates == 1
    assert all("deleted" not in document and "orphan" not in document for document in reranked)
    if mode != "semantic":
        assert result.keyword_total is None


def test_database_lookup_failure_does_not_return_unverified_search_hits():
    def unavailable(ids):
        raise RuntimeError("database unavailable")

    service = HybridSearchService(
        keyword_search=lambda query, filters, limit: [{"paper_id": "stale", "title": "stale title"}],
        metadata_provider=unavailable,
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.search(SearchRequest(query="query", mode="keyword"))


def test_standalone_search_without_metadata_provider_remains_supported():
    service = HybridSearchService(
        keyword_search=lambda query, filters, limit: [{"paper_id": "paper", "title": "paper"}],
    )
    assert [item.paper_id for item in service.search(SearchRequest(query="q", mode="keyword")).results] == ["paper"]


class FakeMilvusSearch:
    """Mimic the index metadata shape from pymilvus 3.0.2 / Milvus Lite 3.2.2."""

    def __init__(self, metric, distances):
        self.metric = metric
        self.distances = distances
        self.searched_collections = []

    def describe_alias(self, *, alias):
        assert alias == "live_alias"
        return {"collection_name": "generation_1"}

    def list_indexes(self, *, collection_name, field_name):
        assert (collection_name, field_name) == ("generation_1", "vector")
        return ["vector"]

    def describe_index(self, *, collection_name, index_name):
        assert (collection_name, index_name) == ("generation_1", "vector")
        return {"field_name": "vector", "index_name": "vector", "metric_type": self.metric}

    def search(self, *, collection_name, data, limit, output_fields):
        self.searched_collections.append(collection_name)
        return [[{"distance": value, "entity": {"paper_id": "p", "chunk_id": str(i)}}
                 for i, value in enumerate(self.distances)]]


@pytest.fixture
def mock_milvus_search(monkeypatch):
    monkeypatch.setattr(retrieval_backends, "KB_MILVUS_ENABLED", True)
    monkeypatch.setattr(retrieval_backends, "_ensure_milvus_collection", lambda: "live_alias")
    monkeypatch.setattr(retrieval_backends, "_load_milvus_collection", lambda collection: None)
    monkeypatch.setattr(retrieval_backends, "embed_text", lambda query: [1.0])

    def configure(metric, distances):
        client = FakeMilvusSearch(metric, distances)
        monkeypatch.setattr(retrieval_backends, "_milvus_client", lambda: client)
        return client
    return configure


@pytest.mark.parametrize(("metric", "distances", "expected"), [
    ("COSINE", [0.9, 0.1, -1.0], [0.9, 0.1, -1.0]),
    ("IP", [3.0, 0.0, -2.0], [3.0, 0.0, -2.0]),
    ("L2", [0.0, 1.0, 3.0], [1.0, 0.5, 0.25]),
])
def test_milvus_scores_follow_actual_metric(mock_milvus_search, metric, distances, expected):
    client = mock_milvus_search(metric, distances)
    rows = retrieval_backends.search_milvus_hits("test")
    assert [row["score"] for row in rows] == pytest.approx(expected)
    assert client.searched_collections == ["generation_1"]


def test_milvus_aggregated_negative_similarity_is_not_clamped(mock_milvus_search):
    mock_milvus_search("COSINE", [-0.5, -0.9])
    scores, _ = retrieval_backends.search_milvus("test")
    assert scores == {"p": -0.5}


@pytest.mark.parametrize(("metric", "distance"), [
    ("UNKNOWN", 0.5), ("", 0.5), ("L2", -1.0), ("COSINE", None),
    ("COSINE", float("nan")), ("IP", float("inf")),
])
def test_milvus_invalid_metric_or_distance_is_not_silently_guessed(mock_milvus_search, metric, distance):
    mock_milvus_search(metric, [distance])
    with pytest.raises(RuntimeError, match="semantic_recall_unavailable"):
        retrieval_backends.search_milvus_hits("test")


def test_milvus_ambiguous_vector_indexes_fail_closed(mock_milvus_search, monkeypatch):
    client = mock_milvus_search("COSINE", [0.9])
    monkeypatch.setattr(client, "list_indexes", lambda **kwargs: ["vector_1", "vector_2"])
    with pytest.raises(RuntimeError, match="semantic_recall_unavailable"):
        retrieval_backends.search_milvus_hits("test")
    assert client.searched_collections == []
