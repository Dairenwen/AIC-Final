from __future__ import annotations

import logging
import hashlib
import math
import json
import sys
import time
import threading
import urllib.error
import urllib.request
import uuid
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
_milvus_replace_lock = threading.RLock()


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
        if not all(isinstance(item, dict) and type(item.get("index")) is int for item in data):
            raise RuntimeError("embedding response contains invalid indexes")
        if sorted(item["index"] for item in data) != list(range(len(texts))):
            raise RuntimeError("embedding response indexes do not match inputs")
        data = sorted(data, key=lambda item: item["index"])
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
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise RuntimeError("embedding response contains non-finite values")
    invalid_dimensions = [len(vector) for vector in vectors if len(vector) != KB_MILVUS_DIM]
    if invalid_dimensions:
        raise RuntimeError(
            f"embedding dimension {invalid_dimensions[0]} does not match KB_MILVUS_DIM={KB_MILVUS_DIM}"
        )
    return vectors


def external_status() -> list[dict[str, Any]]:
    # This endpoint is loaded automatically by the dashboard.  Probing model
    # APIs here would send billable requests merely by opening that page.
    # Configuration presence says nothing about reachability or credentials.
    def state(enabled: bool, configured: bool) -> tuple[bool | None, str]:
        if not enabled:
            return None, "已禁用"
        if not configured:
            return False, "配置不完整"
        return None, "已配置，未检测连接"

    es_health, es_state = state(ELASTICSEARCH_ENABLED, bool(ELASTICSEARCH_URL and ELASTICSEARCH_INDEX))
    milvus_health, milvus_state = state(KB_MILVUS_ENABLED, bool(KB_MILVUS_URI and KB_MILVUS_COLLECTION))
    embedding_configured = (
        bool(KB_EMBEDDING_API_BASE_URL and KB_EMBEDDING_API_MODEL) if KB_EMBEDDING_BACKEND == "api"
        else bool(OLLAMA_EMBED_BASE_URL and OLLAMA_EMBED_MODEL) if KB_EMBEDDING_BACKEND == "ollama"
        else bool(BGE_M3_MODEL) if KB_EMBEDDING_BACKEND == "local" else False
    )
    reranker_configured = (
        bool(KB_RERANKER_API_BASE_URL and KB_RERANKER_API_MODEL) if KB_RERANKER_BACKEND == "api"
        else bool(BGE_RERANKER_MODEL) if KB_RERANKER_BACKEND == "local" else False
    )
    embedding_health, embedding_state = state(KB_MILVUS_ENABLED, embedding_configured)
    reranker_health, reranker_state = state(KB_MILVUS_ENABLED, reranker_configured)
    # The existing HTTP client deliberately permits unauthenticated services.
    # A missing key is therefore a warning, not proof that a service is broken.
    if KB_MILVUS_ENABLED and embedding_configured and KB_EMBEDDING_BACKEND == "api" and not KB_EMBEDDING_API_KEY:
        embedding_state = "未提供 API 密钥（如服务需要）；未检测连接"
    if KB_MILVUS_ENABLED and reranker_configured and KB_RERANKER_BACKEND == "api" and not KB_RERANKER_API_KEY:
        reranker_state = "未提供 API 密钥（如服务需要）；未检测连接"
    return [
        {
            "name": "Elasticsearch",
            "healthy": es_health,
            "latency": "搜索引擎连接" if ELASTICSEARCH_ENABLED else "已禁用",
            "endpoint": "搜索引擎连接" if ELASTICSEARCH_ENABLED else "已禁用",
            "uptime": es_state,
            "url": ELASTICSEARCH_URL,
        },
        {
            "name": "Embedding",
            "healthy": embedding_health,
            "latency": "Embedding API" if KB_EMBEDDING_BACKEND == "api" else ("Ollama 远程向量服务" if KB_EMBEDDING_BACKEND == "ollama" else "本地 BGE-M3"),
            "endpoint": KB_EMBEDDING_API_BASE_URL if KB_EMBEDDING_BACKEND == "api" else (OLLAMA_EMBED_BASE_URL if KB_EMBEDDING_BACKEND == "ollama" else BGE_M3_MODEL),
            "uptime": embedding_state,
            "model": KB_EMBEDDING_API_MODEL if KB_EMBEDDING_BACKEND == "api" else (OLLAMA_EMBED_MODEL if KB_EMBEDDING_BACKEND == "ollama" else BGE_M3_MODEL),
        },
        {
            "name": "Reranker",
            "healthy": reranker_health,
            "latency": "Rerank API" if KB_RERANKER_BACKEND == "api" else "本地 BGE Reranker",
            "endpoint": KB_RERANKER_API_BASE_URL if KB_RERANKER_BACKEND == "api" else BGE_RERANKER_MODEL,
            "uptime": reranker_state,
            "model": KB_RERANKER_API_MODEL if KB_RERANKER_BACKEND == "api" else BGE_RERANKER_MODEL,
        },
        {
            "name": "Milvus",
            "healthy": milvus_health,
            "latency": "向量库连接" if KB_MILVUS_ENABLED else "已禁用",
            "endpoint": "向量库连接" if KB_MILVUS_ENABLED else "已禁用",
            "uptime": milvus_state,
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
        collection = _ensure_milvus_collection()
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
            client.upsert(collection_name=collection, data=rows)
        return bool(rows)
    except Exception as exc:
        logger.warning("failed to index paper %s chunks into Milvus: %s", paper_id, exc)
        return False


def replace_chunks_milvus(paper_id: str, chunks: list[dict[str, Any]]) -> bool:
    """Write the replacement before removing older rows.

    IDs include the content, so a failed embedding/upsert does not overwrite
    the existing generation. This is not an atomic cross-database transaction:
    failed or interrupted writes may leave both generations until retried.
    """
    if not KB_MILVUS_ENABLED or not chunks:
        return False
    try:
        vectors = embed_texts([chunk.get("content") or "" for chunk in chunks])
        rows = []
        for chunk, vector in zip(chunks, vectors):
            content = chunk.get("content") or ""
            chunk_id = chunk["chunk_id"]
            # Existing historical IDs occupy only 31 bits. Keep replacement
            # IDs in a disjoint positive 63-bit range so old rows cannot be
            # overwritten while the new generation is being written.
            digest = hashlib.sha256(json.dumps([paper_id, chunk_id, content], ensure_ascii=False).encode("utf-8")).digest()
            row_id = (1 << 62) | (int.from_bytes(digest[:8], "big") & ((1 << 62) - 1))
            rows.append({
                "id": row_id, "vector": vector, "paper_id": paper_id,
                "chunk_id": chunk_id, "chunk_index": int(chunk.get("chunk_index") or 0),
                "content": content, "page_no": chunk.get("page_no"),
            })
        if len(rows) != len(chunks) or len({row["id"] for row in rows}) != len(rows):
            raise RuntimeError("invalid or duplicate replacement vectors")
        with _milvus_replace_lock:
            client = _milvus_client()
            collection = _ensure_milvus_collection()
            result = client.upsert(collection_name=collection, data=rows)
            if not isinstance(result, dict) or type(result.get("upsert_count")) is not int or result["upsert_count"] != len(rows):
                raise RuntimeError("Milvus did not confirm every replacement row")
            # A failed embedding/upsert never reaches deletion. Even after a
            # successful write a failed delete can leave duplicates, requiring
            # an explicit retry; it cannot justify claiming atomicity.
            ids = [row["id"] for row in rows]
            client.delete(collection_name=collection,
                          filter=f"paper_id == {json.dumps(paper_id, ensure_ascii=False)} and id not in {json.dumps(ids)}")
        return True
    except Exception as exc:
        logger.warning("failed to replace paper %s chunks in Milvus; old rows are not deliberately deleted before a confirmed write: %s", paper_id, exc)
        return False


def rebuild_milvus_safely(chunks_by_paper: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a separate generation and atomically switch the live alias.

    Requires the controller's exclusive single-host maintenance lock. Neither
    a failed stage nor a successful switch drops any physical collection.
    """
    from knowledge_base_runtime.backend.service.maintenance import maintenance_held

    if not maintenance_held():
        raise RuntimeError('Milvus 重建需要应用的知识库维护锁；请通过管理接口触发。')
    if not KB_MILVUS_ENABLED:
        raise RuntimeError('Milvus 未启用，无法重建。')
    expected = sum(len(items) for items in chunks_by_paper.values())
    if not expected:
        raise RuntimeError('数据库没有可用分块；拒绝用空集合替换现有索引。')
    client = _milvus_client()
    alias = _ensure_milvus_collection()
    old_collection = client.describe_alias(alias=alias).get('collection_name')
    if not old_collection:
        raise RuntimeError('无法确认旧向量集合，已停止重建。')
    staged = f'{alias}__v_{uuid.uuid4().hex}'
    client.create_collection(collection_name=staged, dimension=KB_MILVUS_DIM)
    written = 0
    try:
        for paper_id, chunks in chunks_by_paper.items():
            if not chunks:
                continue
            vectors = embed_texts([chunk.get('content') or '' for chunk in chunks])
            if len(vectors) != len(chunks):
                raise RuntimeError('向量数量不匹配。')
            rows = []
            for chunk, vector in zip(chunks, vectors):
                content = chunk.get('content') or ''
                chunk_id = chunk['chunk_id']
                digest = hashlib.sha256(json.dumps([paper_id, chunk_id, content], ensure_ascii=False).encode('utf-8')).digest()
                row_id = (1 << 62) | (int.from_bytes(digest[:8], 'big') & ((1 << 62) - 1))
                rows.append({'id': row_id, 'vector': vector, 'paper_id': paper_id,
                             'chunk_id': chunk_id, 'chunk_index': int(chunk.get('chunk_index') or 0),
                             'content': content, 'page_no': chunk.get('page_no')})
            if len({row['id'] for row in rows}) != len(rows):
                raise RuntimeError('分块标识重复。')
            result = client.upsert(collection_name=staged, data=rows)
            if not isinstance(result, dict) or type(result.get('upsert_count')) is not int or result['upsert_count'] != len(rows):
                raise RuntimeError('新集合未确认全部写入。')
            written += len(rows)
        stats = client.get_collection_stats(collection_name=staged)
        if not isinstance(stats, dict) or int(stats.get('row_count', -1)) != expected or written != expected:
            raise RuntimeError('新集合行数校验失败，旧索引保持启用。')
        try:
            client.alter_alias(collection_name=staged, alias=alias)
        except Exception:
            # A timeout may happen after the server changed the alias. Resolve
            # the actual result rather than assuming failure and retrying.
            if client.describe_alias(alias=alias).get('collection_name') != staged:
                raise
        if client.describe_alias(alias=alias).get('collection_name') != staged:
            raise RuntimeError('无法确认别名切换结果。')
        return {'previous_collection': old_collection, 'new_collection': staged,
                'alias': alias, 'chunks': written, 'papers': len(chunks_by_paper)}
    except Exception:
        logger.exception('Milvus rebuild failed; staged collection %s and old collection %s were retained', staged, old_collection)
        raise


def delete_chunks_milvus(paper_id: str) -> bool:
    if not KB_MILVUS_ENABLED:
        return False
    try:
        client = _milvus_client()
        collection = _ensure_milvus_collection()
        client.delete(collection_name=collection, filter=f'paper_id == {json.dumps(paper_id, ensure_ascii=False)}')
        return True
    except Exception:
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
        scores[str(paper_id)] = max(scores.get(str(paper_id), float('-inf')), score)
        chunks.setdefault(str(paper_id), []).append(_chunk_from_hit(hit))
    return scores, chunks


def _milvus_vector_metric(client: Any, collection: str) -> str:
    """Read the actual vector index metric; never assume a historical index."""
    names = client.list_indexes(collection_name=collection, field_name="vector")
    if not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str):
        raise RuntimeError("Milvus vector index is missing or ambiguous")
    info = client.describe_index(collection_name=collection, index_name=names[0])
    if not isinstance(info, dict) or info.get("field_name") != "vector":
        raise RuntimeError("Milvus vector index metadata is invalid")
    metric = str(info.get("metric_type") or "").upper()
    if metric not in {"COSINE", "IP", "L2"}:
        raise RuntimeError("Milvus vector index metric is unsupported or missing")
    return metric


def _milvus_similarity(distance: Any, metric: str) -> float:
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        raise ValueError("Milvus search result has an invalid distance")
    value = float(distance)
    if not math.isfinite(value):
        raise ValueError("Milvus search result has a non-finite distance")
    if metric in {"COSINE", "IP"}:
        return value
    if metric == "L2" and value >= 0:
        return 1.0 / (1.0 + value)
    raise ValueError("Milvus search result has an unsupported metric or distance")


def search_milvus_hits(query: str, limit: int = 100) -> list[dict[str, Any]]:
    if not query or not KB_MILVUS_ENABLED:
        return []
    try:
        client = _milvus_client()
        alias = _ensure_milvus_collection()
        alias_info = client.describe_alias(alias=alias)
        collection = alias_info.get("collection_name") if isinstance(alias_info, dict) else None
        if not isinstance(collection, str) or not collection:
            raise RuntimeError("Milvus live collection cannot be verified")
        # Resolve the alias once so an index rebuild cannot switch the metric
        # between metadata lookup and search. Old generations are retained.
        metric = _milvus_vector_metric(client, collection)
        _load_milvus_collection(collection)
        hits = client.search(
            collection_name=collection,
            data=[embed_text(query)],
            limit=limit,
            output_fields=["paper_id", "chunk_id", "chunk_index", "content", "page_no"],
        )[0]
        rows: list[dict[str, Any]] = []
        for hit in hits:
            entity = hit.get("entity", {})
            paper_id = entity.get("paper_id")
            if not paper_id:
                continue
            score = _milvus_similarity(hit.get("distance"), metric)
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
    except Exception as exc:
        logger.warning("failed to search Milvus: %s", exc)
        raise RuntimeError("semantic_recall_unavailable") from exc


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
            scores = [None] * len(documents)
            for item in results:
                if not isinstance(item, dict) or type(item.get("index")) is not int:
                    raise RuntimeError("rerank response contains invalid indexes")
                index = item["index"]
                if not 0 <= index < len(scores) or scores[index] is not None:
                    raise RuntimeError("rerank response contains duplicate or out-of-range indexes")
                value = item.get("relevance_score", item.get("score"))
                if value is None or not math.isfinite(float(value)):
                    raise RuntimeError("rerank response contains invalid score")
                scores[index] = float(value)
            if any(score is None for score in scores):
                raise RuntimeError("rerank response does not include every document")
            return scores
        scores = body.get("scores") if isinstance(body, dict) else None
        if isinstance(scores, list):
            converted = [float(score) for score in scores]
            if len(converted) != len(documents) or not all(math.isfinite(score) for score in converted):
                raise RuntimeError("rerank response contains invalid scores")
            return converted
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
    # The first installation creates an alias together with its physical index
    # in one request. A deterministic index name makes concurrent creation
    # converge without adding multiple physical indices to the same alias.
    initial_index = f"{ELASTICSEARCH_INDEX}--initial"
    try:
        create_elasticsearch_index(client, initial_index, alias=ELASTICSEARCH_INDEX)
    except Exception:
        # A simultaneous creator may have completed the same atomic request.
        # Do not treat any other error as a successful initialization.
        if not client.indices.exists_alias(name=ELASTICSEARCH_INDEX):
            raise


def create_elasticsearch_index(client, index: str, *, alias: str | None = None) -> None:
    options = {"aliases": {alias: {"is_write_index": True}}} if alias else {}
    client.indices.create(
        index=index,
        **options,
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


def _milvus_live_alias() -> str:
    digest = hashlib.sha256(KB_MILVUS_COLLECTION.encode('utf-8')).hexdigest()[:24]
    return f'zhiyan_live_{digest}'


def _ensure_milvus_collection() -> str:
    """Attach a separate live alias without renaming or deleting old data."""
    client = _milvus_client()
    alias = _milvus_live_alias()
    with _milvus_replace_lock:
        if not client.has_collection(alias):
            # Once a generation has been staged, loss of the live alias is
            # ambiguous: recreating it against the original collection might
            # silently roll the application back to stale data.
            if any(name.startswith(f'{alias}__v_') for name in client.list_collections()):
                raise RuntimeError('Milvus 活动别名缺失但存在历史重建集合；请先检查并恢复别名。')
            if not client.has_collection(KB_MILVUS_COLLECTION):
                client.create_collection(collection_name=KB_MILVUS_COLLECTION, dimension=KB_MILVUS_DIM)
            try:
                client.create_alias(collection_name=KB_MILVUS_COLLECTION, alias=alias)
            except Exception:
                # Another cooperating process may have created the alias.
                # Never silently fall back to the old collection on errors.
                if not client.has_collection(alias):
                    raise
        description = client.describe_alias(alias=alias)
        if not isinstance(description, dict) or not description.get('collection_name'):
            raise RuntimeError('Milvus live alias cannot be verified')
    return alias


def _load_milvus_collection(collection: str) -> None:
    try:
        _milvus_client().load_collection(collection)
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
