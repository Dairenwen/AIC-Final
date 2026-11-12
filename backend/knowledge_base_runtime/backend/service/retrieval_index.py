from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from knowledge_base_runtime.backend.config.settings import (
    ELASTICSEARCH_ENABLED,
    ELASTICSEARCH_INDEX,
    ELASTICSEARCH_URL,
    KB_MILVUS_ENABLED,
)
from knowledge_base_runtime.backend.dao.database import get_db
from knowledge_base_runtime.backend.client.retrieval_backends import (
    build_elasticsearch_document,
    elasticsearch_available,
    elasticsearch_client,
    ensure_elasticsearch_index,
    index_chunks_milvus,
    milvus_available,
    recreate_milvus_collection,
)


LAST_REINDEX: dict[str, Any] | None = None


def get_retrieval_index_status() -> dict[str, Any]:
    status = {
        "postgres_papers": _count_table("papers"),
        "postgres_search_index": _count_table("search_index"),
        "postgres_chunks": _count_table("paper_chunks"),
        "elasticsearch_enabled": ELASTICSEARCH_ENABLED,
        "elasticsearch_url": ELASTICSEARCH_URL,
        "elasticsearch_index": ELASTICSEARCH_INDEX,
        "elasticsearch_healthy": False,
        "elasticsearch_count": None,
        "milvus_enabled": KB_MILVUS_ENABLED,
        "last_reindex": LAST_REINDEX,
    }
    if ELASTICSEARCH_ENABLED:
        status["elasticsearch_healthy"] = elasticsearch_available()
        status["elasticsearch_count"] = _elasticsearch_count()
    if KB_MILVUS_ENABLED:
        status["milvus_healthy"] = milvus_available()
    return status


def rebuild_retrieval_indexes(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    target = str(payload.get("target") or "elasticsearch").lower()
    mode = str(payload.get("mode") or "upsert").lower()
    batch_size = min(max(int(payload.get("batch_size") or 500), 1), 2000)

    if target not in {"elasticsearch", "milvus", "all"}:
        raise ValueError("target must be one of: elasticsearch, milvus, all")
    if mode not in {"upsert", "recreate"}:
        raise ValueError("mode must be one of: upsert, recreate")

    started_at = _now()
    papers = _fetch_papers()
    chunks_by_paper = _fetch_chunks_by_paper() if target in {"milvus", "all"} else {}

    result: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": None,
        "target": target,
        "mode": mode,
        "batch_size": batch_size,
        "postgres_papers": len(papers),
        "postgres_chunks": sum(len(chunks) for chunks in chunks_by_paper.values()),
        "elasticsearch_synced": 0,
        "elasticsearch_failed": 0,
        "elasticsearch_count_before": _elasticsearch_count() if target in {"elasticsearch", "all"} else None,
        "elasticsearch_count_after": None,
        "milvus_synced_papers": 0,
        "milvus_failed_papers": 0,
        "errors": [],
    }

    try:
        if target in {"elasticsearch", "all"}:
            synced, failed, errors = _bulk_upsert_elasticsearch(papers, batch_size=batch_size, recreate=mode == "recreate")
            result["elasticsearch_synced"] = synced
            result["elasticsearch_failed"] = failed
            result["errors"].extend(errors)
            result["elasticsearch_count_after"] = _elasticsearch_count()
        if target in {"milvus", "all"}:
            if mode == "recreate":
                recreate_milvus_collection()
            milvus_synced, milvus_failed = _sync_milvus(papers, chunks_by_paper)
            result["milvus_synced_papers"] = milvus_synced
            result["milvus_failed_papers"] = milvus_failed
        result["ok"] = not result["errors"] and result["elasticsearch_failed"] == 0 and result["milvus_failed_papers"] == 0
    finally:
        result["finished_at"] = _now()
        global LAST_REINDEX
        LAST_REINDEX = result
    return result


def _count_table(table: str) -> int:
    with get_db() as db:
        row = db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"] if row else 0)


def _fetch_papers() -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute("SELECT * FROM papers ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def _fetch_chunks_by_paper() -> dict[str, list[dict[str, Any]]]:
    chunks_by_paper: dict[str, list[dict[str, Any]]] = {}
    with get_db() as db:
        rows = db.execute(
            "SELECT chunk_id, paper_id, chunk_index, content, page_no FROM paper_chunks ORDER BY paper_id, chunk_index"
        ).fetchall()
    for row in rows:
        chunk = dict(row)
        chunks_by_paper.setdefault(chunk["paper_id"], []).append(chunk)
    return chunks_by_paper


def _elasticsearch_count() -> int | None:
    if not ELASTICSEARCH_ENABLED:
        return None
    try:
        response = elasticsearch_client().count(index=ELASTICSEARCH_INDEX)
        body = getattr(response, "body", response)
        return int(body.get("count") or 0)
    except Exception:
        return None


def _bulk_upsert_elasticsearch(
    papers: list[dict[str, Any]],
    *,
    batch_size: int,
    recreate: bool,
) -> tuple[int, int, list[dict[str, Any]]]:
    if not ELASTICSEARCH_ENABLED:
        raise RuntimeError("Elasticsearch is disabled")
    client = elasticsearch_client()
    if recreate and client.indices.exists(index=ELASTICSEARCH_INDEX):
        client.indices.delete(index=ELASTICSEARCH_INDEX)
    ensure_elasticsearch_index(client)

    synced = 0
    failed = 0
    errors: list[dict[str, Any]] = []
    for batch in _batched(papers, batch_size):
        operations: list[dict[str, Any]] = []
        for paper in batch:
            doc = build_elasticsearch_document(paper)
            operations.append({"index": {"_index": ELASTICSEARCH_INDEX, "_id": doc["id"]}})
            operations.append(doc)
        try:
            response = client.bulk(operations=operations, refresh=True)
            body = getattr(response, "body", response)
        except Exception as exc:
            failed += len(batch)
            errors.append({"batch_start_id": batch[0].get("id"), "error": str(exc)})
            continue

        if body.get("errors"):
            for item in body.get("items", []):
                index_result = item.get("index", {})
                if index_result.get("error"):
                    failed += 1
                    errors.append({"id": index_result.get("_id"), "error": index_result["error"]})
                else:
                    synced += 1
        else:
            synced += len(batch)
    return synced, failed, errors[:50]


def _sync_milvus(papers: list[dict[str, Any]], chunks_by_paper: dict[str, list[dict[str, Any]]]) -> tuple[int, int]:
    synced = 0
    failed = 0
    for paper in papers:
        chunks = chunks_by_paper.get(paper["id"], [])
        if not chunks:
            continue
        if index_chunks_milvus(paper["id"], chunks):
            synced += 1
        else:
            failed += 1
    return synced, failed


def _batched(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
