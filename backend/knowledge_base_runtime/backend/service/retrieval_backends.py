from __future__ import annotations

import logging
import json
import sys
import time
import urllib.error
import urllib.request
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from knowledge_base_runtime.backend.config.settings import (
    BGE_EMBED_BATCH_SIZE,
    BGE_M3_MODEL,
    BGE_RERANKER_MODEL,
    BGE_USE_FP16,
    KB_EMBEDDING_API_BASE_URL,
    KB_EMBEDDING_API_ENDPOINT,
    KB_EMBEDDING_API_KEY,
    KB_EMBEDDING_API_MAX_RETRIES,
    KB_EMBEDDING_API_MODEL,
    KB_EMBEDDING_API_TIMEOUT_SECONDS,
    KB_RERANKER_API_BASE_URL,
    KB_RERANKER_API_ENDPOINT,
    KB_RERANKER_API_KEY,
    KB_RERANKER_API_MAX_RETRIES,
    KB_RERANKER_API_MODEL,
    KB_RERANKER_API_TIMEOUT_SECONDS,
    KB_RERANKER_BACKEND,
    ELASTICSEARCH_CA_CERT,
    ELASTICSEARCH_ENABLED,
    ELASTICSEARCH_INDEX,
    ELASTICSEARCH_PASSWORD,
    ELASTICSEARCH_USERNAME,
    ELASTICSEARCH_URL,
    KB_MILVUS_COLLECTION,
    KB_MILVUS_DIM,
    KB_MILVUS_ENABLED,
    KB_MILVUS_URI,
    KB_EMBEDDING_BACKEND,
    OLLAMA_EMBED_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_EMBED_TIMEOUT_SECONDS,
)
from knowledge_base_runtime.backend.service.common import loads_list

logger = logging.getLogger(__name__)


def _api_json_request(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
    max_retries: int = 0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
        "User-Agent": "Zhiyan-KnowledgeBase/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    attempts = max(1, int(max_retries) + 1)
    body: Any = None
    for attempt in range(attempts):
        api_request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(api_request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise RuntimeError(f"model API returned HTTP {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt + 1 >= attempts:
                reason = getattr(exc, "reason", None) or str(exc)
                raise RuntimeError(
                    f"model API unavailable after {attempts} attempts: {reason}"
                ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("model API response is not valid JSON") from exc
        time.sleep(min(0.5 * (2**attempt), 2.0))
    if not isinstance(body, dict):
        raise RuntimeError("model API response must be a JSON object")
    return body


def embed_text(text: str, dim: int = KB_MILVUS_DIM) -> list[float]:
    """Return a BGE-M3 dense embedding for Milvus semantic retrieval."""
    vector = embed_texts([text])[0]
    if dim and len(vector) != dim:
        raise RuntimeError(f"BGE embedding dimension {len(vector)} does not match KB_MILVUS_DIM={dim}")
    return vector


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if KB_EMBEDDING_BACKEND == "ollama":
        return _embed_texts_ollama(texts)
    if KB_EMBEDDING_BACKEND == "api":
        return _embed_texts_api(texts)
    if KB_EMBEDDING_BACKEND != "local":
        raise RuntimeError(f"unsupported KB_EMBEDDING_BACKEND: {KB_EMBEDDING_BACKEND}")
    model = _bge_model()
    embeddings = model.encode(
        texts,
        batch_size=max(1, BGE_EMBED_BATCH_SIZE),
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )["dense_vecs"]
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()
    return _validate_embeddings(embeddings, len(texts))


def _embed_texts_ollama(texts: list[str]) -> list[list[float]]:
    payload = json.dumps(
        {"model": OLLAMA_EMBED_MODEL, "input": texts},
        ensure_ascii=False,
    ).encode("utf-8")
    api_request = urllib.request.Request(
        f"{OLLAMA_EMBED_BASE_URL}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(api_request, timeout=OLLAMA_EMBED_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama embedding returned HTTP {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama embedding unavailable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Ollama embedding request timed out") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama embedding response is not valid JSON") from exc
    embeddings = body.get("embeddings") if isinstance(body, dict) else None
    return _validate_embeddings(embeddings, len(texts))


def _embed_texts_api(texts: list[str]) -> list[list[float]]:
    if not KB_EMBEDDING_API_BASE_URL:
        raise RuntimeError("KB_EMBEDDING_API_BASE_URL is not configured")
    body = _api_json_request(
        KB_EMBEDDING_API_BASE_URL,
        KB_EMBEDDING_API_ENDPOINT,
        {"model": KB_EMBEDDING_API_MODEL, "input": texts},
        KB_EMBEDDING_API_KEY,
        KB_EMBEDDING_API_TIMEOUT_SECONDS,
        KB_EMBEDDING_API_MAX_RETRIES,
    )
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list):
        data = sorted(data, key=lambda item: int(item.get("index", 0)))
        embeddings = [item.get("embedding") for item in data]
    else:
        embeddings = body.get("embeddings") if isinstance(body, dict) else None
    return _validate_embeddings(embeddings, len(texts))


def _validate_embeddings(embeddings: Any, expected_count: int) -> list[list[float]]:
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()
    if not isinstance(embeddings, list) or len(embeddings) != expected_count:
        raise RuntimeError(
            f"embedding response count {len(embeddings) if isinstance(embeddings, list) else 0} "
            f"does not match input count {expected_count}"
        )
    vectors = [_as_float_vector(vector) for vector in embeddings]
    invalid_dimensions = [len(vector) for vector in vectors if len(vector) != KB_MILVUS_DIM]
    if invalid_dimensions:
        raise RuntimeError(
            f"embedding dimension {invalid_dimensions[0]} does not match KB_MILVUS_DIM={KB_MILVUS_DIM}"
        )
    return vectors


def external_status() -> list[dict[str, Any]]:
    es_available = elasticsearch_available() if ELASTICSEARCH_ENABLED else True
    milvus_available_now = milvus_available() if KB_MILVUS_ENABLED else True
    embedding_available_now = embedding_available() if KB_MILVUS_ENABLED else True
    reranker_available_now = reranker_available() if KB_MILVUS_ENABLED else True
    return [
        {
            "name": "Elasticsearch",
            "healthy": es_available,
            "latency": "搜索引擎连接" if ELASTICSEARCH_ENABLED else "已禁用",
            "endpoint": "搜索引擎连接" if ELASTICSEARCH_ENABLED else "已禁用",
            "uptime": "running" if ELASTICSEARCH_ENABLED and es_available else "optional fallback",
            "url": ELASTICSEARCH_URL,
        },
        {
            "name": "Embedding",
            "healthy": embedding_available_now,
            "latency": "Embedding API" if KB_EMBEDDING_BACKEND == "api" else ("Ollama 远程向量服务" if KB_EMBEDDING_BACKEND == "ollama" else "本地 BGE-M3"),
            "endpoint": KB_EMBEDDING_API_BASE_URL if KB_EMBEDDING_BACKEND == "api" else (OLLAMA_EMBED_BASE_URL if KB_EMBEDDING_BACKEND == "ollama" else BGE_M3_MODEL),
            "uptime": "running" if KB_MILVUS_ENABLED and embedding_available_now else "optional fallback",
            "model": KB_EMBEDDING_API_MODEL if KB_EMBEDDING_BACKEND == "api" else (OLLAMA_EMBED_MODEL if KB_EMBEDDING_BACKEND == "ollama" else BGE_M3_MODEL),
        },
        {
            "name": "Reranker",
            "healthy": reranker_available_now,
            "latency": "Rerank API" if KB_RERANKER_BACKEND == "api" else "本地 BGE Reranker",
            "endpoint": KB_RERANKER_API_BASE_URL if KB_RERANKER_BACKEND == "api" else BGE_RERANKER_MODEL,
            "uptime": "running" if KB_MILVUS_ENABLED and reranker_available_now else "optional fallback",
            "model": KB_RERANKER_API_MODEL if KB_RERANKER_BACKEND == "api" else BGE_RERANKER_MODEL,
        },
        {
            "name": "Milvus",
            "healthy": milvus_available_now,
            "latency": "向量库连接" if KB_MILVUS_ENABLED else "已禁用",
            "endpoint": "向量库连接" if KB_MILVUS_ENABLED else "已禁用",
            "uptime": "running" if KB_MILVUS_ENABLED and milvus_available_now else "optional fallback",
            "uri": KB_MILVUS_URI,
        },
    ]


def embedding_available() -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        return bool(embed_text("语义检索健康检查"))
    except Exception:
        return False


def reranker_available() -> bool:
    try:
        if KB_RERANKER_BACKEND == "api":
            return bool(ApiReranker().score("健康检查", ["健康检查文档"]))
        return bool(BgeReranker().score("健康检查", ["健康检查文档"]))
    except Exception:
        return False


def elasticsearch_available() -> bool:
    if not ELASTICSEARCH_ENABLED:
        return False
    try:
        return bool(_es_client().ping())
    except Exception:
        return False


def milvus_available() -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        _ensure_milvus_collection()
        return True
    except Exception:
        return False


def index_paper_elasticsearch(paper: dict[str, Any]) -> bool:
    if not ELASTICSEARCH_ENABLED:
        return False
    try:
        client = _es_client()
        ensure_elasticsearch_index(client)
        client.index(index=ELASTICSEARCH_INDEX, id=paper["id"], document=build_elasticsearch_document(paper))
        return True
    except Exception as exc:
        logger.warning("failed to index paper %s into Elasticsearch: %s", paper.get("id"), exc)
        return False


def delete_paper_elasticsearch(paper_id: str) -> None:
    if not ELASTICSEARCH_ENABLED:
        return
    try:
        _es_client().delete(index=ELASTICSEARCH_INDEX, id=paper_id, ignore_status=[404], refresh=True)
    except Exception:
        return


def search_elasticsearch(query: str, filters: dict[str, Any], limit: int = 10000) -> dict[str, float]:
    try:
        return {hit["paper_id"]: float(hit.get("_score") or 0.0) for hit in search_elasticsearch_hits(query, filters, limit)}
    except Exception:
        return {}


def search_elasticsearch_hits(query: str, filters: dict[str, Any], limit: int = 10000) -> list[dict[str, Any]]:
    if not query or not ELASTICSEARCH_ENABLED:
        return []
    try:
        client = _es_client()
        must: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^3",
                        "abstract^2",
                        "author",
                        "keywords",
                        "tasks",
                        "methods",
                        "research_area",
                        "subfield",
                    ],
                }
            }
        ]
        filter_clauses = _es_filters(filters)
        response = client.search(
            index=ELASTICSEARCH_INDEX,
            query={"bool": {"must": must, "filter": filter_clauses}},
            size=limit,
            track_total_hits=True,
        )
        body = getattr(response, "body", response)
        hits_body = body.get("hits", {})
        hits = hits_body.get("hits", [])
        total = _es_total_value(hits_body.get("total"))
        return [
            {
                "paper_id": hit.get("_id"),
                "_score": float(hit.get("_score") or 0.0),
                "_total_hits": total,
                **(hit.get("_source") or {}),
            }
            for hit in hits
            if hit.get("_id")
        ]
    except Exception as exc:
        logger.warning("failed to search Elasticsearch: %s", exc)
        raise RuntimeError("keyword_recall_unavailable") from exc


def index_chunks_milvus(paper_id: str, chunks: list[dict[str, Any]]) -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        client = _milvus_client()
        _ensure_milvus_collection()
        vectors = embed_texts([chunk.get("content") or "" for chunk in chunks])
        rows = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = chunk["chunk_id"]
            rows.append(
                {
                    "id": _stable_int_id(chunk_id),
                    "vector": vector,
                    "paper_id": paper_id,
                    "chunk_id": chunk_id,
                    "chunk_index": int(chunk.get("chunk_index") or 0),
                    "content": chunk.get("content") or "",
                    "page_no": chunk.get("page_no"),
                }
            )
        if rows:
            client.upsert(collection_name=KB_MILVUS_COLLECTION, data=rows)
        return bool(rows)
    except Exception as exc:
        logger.warning("failed to index paper %s chunks into Milvus: %s", paper_id, exc)
        return False


def delete_chunks_milvus(paper_id: str) -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        client = _milvus_client()
        _ensure_milvus_collection()
        client.delete(collection_name=KB_MILVUS_COLLECTION, filter=f'paper_id == "{paper_id}"')
        return True
    except Exception:
        return False


def recreate_milvus_collection() -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        client = _milvus_client()
        if client.has_collection(KB_MILVUS_COLLECTION):
            client.drop_collection(KB_MILVUS_COLLECTION)
        _ensure_milvus_collection()
        return True
    except Exception as exc:
        logger.warning("failed to recreate Milvus collection: %s", exc)
        return False


def search_milvus(query: str, limit: int = 100) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    try:
        hits = search_milvus_hits(query, limit)
    except Exception:
        return {}, {}
    scores: dict[str, float] = {}
    chunks: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        paper_id = hit.get("paper_id")
        if not paper_id:
            continue
        score = float(hit.get("score") or 0.0)
        scores[str(paper_id)] = max(scores.get(str(paper_id), 0.0), score)
        chunks.setdefault(str(paper_id), []).append(_chunk_from_hit(hit))
    return scores, chunks


def search_milvus_hits(query: str, limit: int = 100) -> list[dict[str, Any]]:
    if not query or not KB_MILVUS_ENABLED:
        return []
    try:
        client = _milvus_client()
        _ensure_milvus_collection()
        _load_milvus_collection()
        hits = client.search(
            collection_name=KB_MILVUS_COLLECTION,
            data=[embed_text(query)],
            limit=limit,
            output_fields=["paper_id", "chunk_id", "chunk_index", "content", "page_no"],
        )[0]
    except Exception as exc:
        logger.warning("failed to search Milvus: %s", exc)
        raise RuntimeError("semantic_recall_unavailable") from exc

    rows: list[dict[str, Any]] = []
    for hit in hits:
        entity = hit.get("entity", {})
        paper_id = entity.get("paper_id")
        if not paper_id:
            continue
        distance = float(hit.get("distance") or 0.0)
        score = 1.0 / (1.0 + distance)
        rows.append(
            _chunk_from_hit(
                {
                    "paper_id": paper_id,
                    "chunk_id": entity.get("chunk_id"),
                    "chunk_index": entity.get("chunk_index"),
                    "page_no": entity.get("page_no"),
                    "content": entity.get("content"),
                    "score": score,
                }
            )
        )
    return rows


def get_best_chunks(paper_ids: list[str], per_paper: int = 3) -> dict[str, list[dict[str, Any]]]:
    unique_ids = [paper_id for paper_id in dict.fromkeys(str(item) for item in paper_ids if item)]
    if not unique_ids:
        return {}
    with _local_db() as db:
        placeholders = ",".join("?" for _ in unique_ids)
        rows = db.execute(
            f"""
            SELECT chunk_id, paper_id, chunk_index, content, page_no
            FROM paper_chunks
            WHERE paper_id IN ({placeholders})
            ORDER BY paper_id, chunk_index
            """,
            unique_ids,
        ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        paper_id = str(row["paper_id"])
        result.setdefault(paper_id, [])
        if len(result[paper_id]) < per_paper:
            result[paper_id].append(_chunk_from_hit(dict(row)))
    return result


def get_paper_metadata(paper_ids: list[str]) -> dict[str, dict[str, Any]]:
    unique_ids = [paper_id for paper_id in dict.fromkeys(str(item) for item in paper_ids if item)]
    if not unique_ids:
        return {}
    with _local_db() as db:
        placeholders = ",".join("?" for _ in unique_ids)
        rows = db.execute(
            f"SELECT * FROM papers WHERE id IN ({placeholders}) AND delete_time IS NULL",
            unique_ids,
        ).fetchall()
    return {str(row["id"]): _normalize_metadata(dict(row)) for row in rows}


class BgeReranker:
    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or BGE_RERANKER_MODEL
        if not self.model_name:
            raise RuntimeError("BGE_RERANKER_MODEL is not configured")

    def score(self, query: str, documents: list[str]) -> list[float]:
        model = _bge_reranker(self.model_name)
        scores = model.compute_score([[query, document] for document in documents])
        if isinstance(scores, (int, float)):
            return [float(scores)]
        return [float(score) for score in scores]


class ApiReranker:
    def __init__(self) -> None:
        if not KB_RERANKER_API_BASE_URL:
            raise RuntimeError("KB_RERANKER_API_BASE_URL is not configured")

    def score(self, query: str, documents: list[str]) -> list[float]:
        body = _api_json_request(
            KB_RERANKER_API_BASE_URL,
            KB_RERANKER_API_ENDPOINT,
            {
                "model": KB_RERANKER_API_MODEL,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "return_documents": False,
            },
            KB_RERANKER_API_KEY,
            KB_RERANKER_API_TIMEOUT_SECONDS,
            KB_RERANKER_API_MAX_RETRIES,
        )
        results = body.get("results") if isinstance(body, dict) else None
        if isinstance(results, list):
            scores = [0.0] * len(documents)
            for item in results:
                index = int(item.get("index", -1))
                if 0 <= index < len(scores):
                    scores[index] = float(item.get("relevance_score", item.get("score", 0.0)))
            return scores
        scores = body.get("scores") if isinstance(body, dict) else None
        if isinstance(scores, list):
            return [float(score) for score in scores]
        raise RuntimeError("rerank API response does not contain results or scores")


@lru_cache(maxsize=1)
def _es_client():
    from elasticsearch import Elasticsearch

    kwargs: dict[str, Any] = {"request_timeout": 10}
    if ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD:
        kwargs["basic_auth"] = (ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD)
    if ELASTICSEARCH_CA_CERT:
        kwargs["ca_certs"] = ELASTICSEARCH_CA_CERT
    else:
        kwargs["verify_certs"] = False
    return Elasticsearch(ELASTICSEARCH_URL, **kwargs)


def elasticsearch_client():
    return _es_client()


def build_elasticsearch_document(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": paper["id"],
        "title": paper.get("title") or "",
        "abstract": paper.get("abstract") or "",
        "author": loads_list(paper.get("author")),
        "keywords": loads_list(paper.get("keywords")),
        "tasks": loads_list(paper.get("tasks")),
        "methods": loads_list(paper.get("methods")),
        "publish_year": paper.get("publish_year"),
        "publish_venue": paper.get("publish_venue"),
        "research_area": paper.get("research_area"),
        "subfield": paper.get("subfield"),
        "citation_count": paper.get("citation_count") or 0,
        "arxiv_url": paper.get("arxiv_url"),
        "project_url": paper.get("project_url"),
        "source_url": paper.get("source_url"),
    }


def ensure_elasticsearch_index(client=None) -> None:
    client = client or _es_client()
    if client.indices.exists(index=ELASTICSEARCH_INDEX):
        return
    client.indices.create(
        index=ELASTICSEARCH_INDEX,
        mappings={
            "properties": {
                "id": {"type": "keyword"},
                "title": {"type": "text"},
                "abstract": {"type": "text"},
                "author": {"type": "text"},
                "keywords": {"type": "keyword"},
                "tasks": {"type": "keyword"},
                "methods": {"type": "keyword"},
                "publish_year": {"type": "integer"},
                "publish_venue": {"type": "keyword"},
                "research_area": {"type": "keyword"},
                "subfield": {"type": "keyword"},
                "citation_count": {"type": "integer"},
                "arxiv_url": {"type": "keyword", "index": False},
                "project_url": {"type": "keyword", "index": False},
                "source_url": {"type": "keyword", "index": False},
            }
        },
    )


def _ensure_es_index(client) -> None:
    ensure_elasticsearch_index(client)


@lru_cache(maxsize=1)
def _milvus_client():
    _prepare_local_milvus_imports()
    from pymilvus import MilvusClient

    if "://" not in KB_MILVUS_URI:
        Path(KB_MILVUS_URI).parent.mkdir(parents=True, exist_ok=True)
    return MilvusClient(KB_MILVUS_URI)


def _prepare_local_milvus_imports() -> None:
    local_packages = Path(__file__).resolve().parents[1] / ".local_packages"
    if local_packages.exists() and str(local_packages) not in sys.path:
        sys.path.insert(0, str(local_packages))
    # pandas imports these optional accelerators while pymilvus loads. The
    # system copies can be ABI-incompatible with the bundled NumPy, so make
    # pandas treat them as unavailable instead of emitting noisy tracebacks.
    for module_name in ("numexpr", "bottleneck"):
        sys.modules.setdefault(module_name, None)


def _ensure_milvus_collection() -> None:
    client = _milvus_client()
    if client.has_collection(KB_MILVUS_COLLECTION):
        return
    client.create_collection(collection_name=KB_MILVUS_COLLECTION, dimension=KB_MILVUS_DIM)


def _load_milvus_collection() -> None:
    try:
        _milvus_client().load_collection(KB_MILVUS_COLLECTION)
    except Exception:
        return


def _es_filters(filters: dict[str, Any]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    if filters.get("year_start") or filters.get("year_end"):
        range_body: dict[str, Any] = {}
        if filters.get("year_start"):
            range_body["gte"] = int(filters["year_start"])
        if filters.get("year_end"):
            range_body["lte"] = int(filters["year_end"])
        clauses.append({"range": {"publish_year": range_body}})
    for key, field in [
        ("venue", "publish_venue"),
        ("research_area", "research_area"),
        ("subfield", "subfield"),
    ]:
        if filters.get(key):
            clauses.append({"term": {field: filters[key]}})
    return clauses


def _stable_int_id(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0x7FFFFFFF


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())


@lru_cache(maxsize=1)
def _bge_model():
    _prepare_local_milvus_imports()
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError(
            "FlagEmbedding is not installed. Install semantic dependencies before enabling BGE-M3."
        ) from exc
    return BGEM3FlagModel(BGE_M3_MODEL, use_fp16=BGE_USE_FP16)


@lru_cache(maxsize=2)
def _bge_reranker(model_name: str):
    _prepare_local_milvus_imports()
    try:
        from FlagEmbedding import FlagReranker
    except ImportError as exc:
        raise RuntimeError("FlagEmbedding is not installed. Install semantic dependencies before enabling rerank.") from exc
    return FlagReranker(model_name, use_fp16=BGE_USE_FP16)


def _as_float_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _es_total_value(total: Any) -> int:
    if isinstance(total, dict):
        return int(total.get("value") or 0)
    if isinstance(total, int):
        return total
    return 0


def _chunk_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    chunk = {
        "paper_id": hit.get("paper_id"),
        "chunk_id": hit.get("chunk_id"),
        "chunk_index": hit.get("chunk_index"),
        "page_no": hit.get("page_no"),
        "content": hit.get("content"),
    }
    if hit.get("score") is not None:
        chunk["score"] = float(hit.get("score") or 0.0)
    if hit.get("page_start") is not None:
        chunk["page_start"] = hit.get("page_start")
    elif hit.get("page_no") is not None:
        chunk["page_start"] = hit.get("page_no")
    if hit.get("page_end") is not None:
        chunk["page_end"] = hit.get("page_end")
    elif hit.get("page_no") is not None:
        chunk["page_end"] = hit.get("page_no")
    if hit.get("char_start") is not None:
        chunk["char_start"] = hit.get("char_start")
    if hit.get("char_end") is not None:
        chunk["char_end"] = hit.get("char_end")
    return chunk


def _normalize_metadata(row: dict[str, Any]) -> dict[str, Any]:
    for name in ("keywords", "related_papers", "tasks", "methods", "author"):
        if name in row:
            row[name] = row.get(name) if isinstance(row.get(name), list) else loads_list(row.get(name))
    row["authors"] = row.get("author") or []
    row["year"] = row.get("publish_year")
    row["venue"] = row.get("publish_venue")
    return row


def _local_db():
    from knowledge_base_runtime.backend.dao.database import get_db

    return get_db()
