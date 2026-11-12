from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from typing import Any, Callable

from knowledge_base_runtime.backend.config.settings import BGE_RERANKER_MODEL, KB_RERANKER_BACKEND
from knowledge_base_runtime.backend.dao.database import get_db
from knowledge_base_runtime.backend.utils.common import loads_list, preview
from knowledge_base_runtime.backend.service.metadata import serialize_paper
from knowledge_base_runtime.backend.client.retrieval_backends import (
    ApiReranker,
    BgeReranker,
    get_best_chunks,
    get_paper_metadata,
    search_elasticsearch_hits,
    search_milvus_hits,
)


DEFAULT_CANDIDATE_K = 100
DEFAULT_HYBRID_CANDIDATE_K = 1000
DEFAULT_RERANK_K = 50
DEFAULT_TOP_K = 20
MAX_CHUNKS_PER_PAPER = 3
RRF_K = 60
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchRequest:
    query: str
    mode: str = "hybrid"
    filters: dict[str, Any] = field(default_factory=dict)
    top_k: int = DEFAULT_TOP_K
    candidate_k: int = DEFAULT_CANDIDATE_K
    rerank_k: int = DEFAULT_RERANK_K


@dataclass(frozen=True)
class ChunkProvenance:
    chunk_id: str
    chunk_index: int | None = None
    content: str = ""
    score: float | None = None
    page_no: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "page_no": self.page_no,
            "content": self.content,
            "score": self.score,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class SearchResult:
    paper_id: str
    score: float
    metadata: dict[str, Any]
    source_ranks: dict[str, int]
    chunks: list[ChunkProvenance]
    rerank_score: float | None = None


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_candidates: int = 0
    total_matches: int | None = None
    keyword_total: int | None = None
    retrieval_stages: list[str] = field(default_factory=list)
    degraded_reasons: list[str] = field(default_factory=list)
    error: str | None = None


class HybridSearchService:
    def __init__(
        self,
        semantic_search: Callable[[str, int], list[dict[str, Any]]] | None = None,
        keyword_search: Callable[[str, dict[str, Any], int], list[dict[str, Any]]] | None = None,
        metadata_provider: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
        chunk_provider: Callable[[list[str], int], dict[str, list[dict[str, Any]]]] | None = None,
        reranker: Any | None = None,
    ) -> None:
        self.semantic_search = semantic_search
        self.keyword_search = keyword_search
        self.metadata_provider = metadata_provider
        self.chunk_provider = chunk_provider
        self.reranker = reranker

    def search(self, request: SearchRequest) -> SearchResponse:
        query = request.query.strip()
        if not query:
            return SearchResponse(query=request.query, error="query_must_not_be_empty")
        mode = _normalize_mode(request.mode)
        if mode not in {"hybrid", "semantic", "keyword"}:
            return SearchResponse(query=query, error="unsupported_search_mode")

        candidate_k = max(1, min(int(request.candidate_k), 1000))
        top_k = max(1, min(int(request.top_k), 1000))
        use_semantic = mode in {"hybrid", "semantic"}
        use_keyword = mode in {"hybrid", "keyword"}
        if use_semantic and self.semantic_search is None:
            return SearchResponse(query=query, error="semantic_recall_unavailable")
        if use_keyword and self.keyword_search is None:
            if mode == "keyword":
                return SearchResponse(query=query, error="keyword_recall_unavailable")
            use_keyword = False

        semantic_hits: list[dict[str, Any]] = []
        keyword_hits: list[dict[str, Any]] = []
        keyword_total: int | None = None
        stages: list[str] = []
        degraded: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            semantic_future = executor.submit(self.semantic_search, query, candidate_k) if use_semantic else None
            keyword_future = executor.submit(self.keyword_search, query, request.filters, candidate_k) if use_keyword else None
            if semantic_future is not None:
                try:
                    semantic_hits = list(semantic_future.result() or [])
                    stages.append("semantic")
                except Exception as exc:
                    LOGGER.warning("semantic recall unavailable: %s", exc)
                    return SearchResponse(query=query, error="semantic_recall_unavailable")
            if keyword_future is not None:
                try:
                    keyword_hits = list(keyword_future.result() or [])
                    keyword_total = _keyword_total_hits(keyword_hits)
                    stages.append("keyword")
                except Exception as exc:
                    LOGGER.warning("keyword recall unavailable: %s", exc)
                    if mode == "keyword":
                        return SearchResponse(query=query, error="keyword_recall_unavailable")
                    degraded.append("keyword_recall_unavailable")

        candidates = self._fuse(semantic_hits, keyword_hits)
        stages.append("rrf")
        metadata = self._metadata_for(candidates, keyword_hits)
        candidates = [candidate for candidate in candidates if _matches_filters(metadata.get(candidate["paper_id"], {}), request.filters)]
        results = [self._to_result(candidate, metadata.get(candidate["paper_id"], {"id": candidate["paper_id"]})) for candidate in candidates]
        self._enrich_chunk_provenance(results)

        rerank_k = max(0, min(int(request.rerank_k), len(results)))
        if self.reranker is not None and rerank_k:
            try:
                self._rerank(query, results, rerank_k)
                stages.append("rerank")
            except Exception as exc:
                LOGGER.warning("reranker unavailable; returning RRF results: %s", exc)
                degraded.append("reranker_unavailable")

        candidate_total = len(candidates)
        total_matches = candidate_total
        if mode == "keyword" and keyword_total is not None:
            total_matches = keyword_total
        elif mode == "hybrid" and keyword_total is not None:
            total_matches = max(keyword_total, candidate_total)

        return SearchResponse(
            query=query,
            results=results[:top_k],
            total_candidates=candidate_total,
            total_matches=total_matches,
            keyword_total=keyword_total,
            retrieval_stages=stages,
            degraded_reasons=degraded,
        )

    def _fuse(self, semantic_hits: list[dict[str, Any]], keyword_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        for source, hits in (("semantic", semantic_hits), ("keyword", keyword_hits)):
            for rank, hit in enumerate(hits, start=1):
                paper_id = str(hit.get("paper_id") or hit.get("id") or "").strip()
                if not paper_id:
                    continue
                candidate = candidates.setdefault(
                    paper_id,
                    {"paper_id": paper_id, "score": 0.0, "source_ranks": {}, "chunks": []},
                )
                if source in candidate["source_ranks"]:
                    continue
                candidate["source_ranks"][source] = rank
                candidate["score"] += 1.0 / (RRF_K + rank)
                if source == "semantic":
                    candidate["chunks"].append(_to_chunk(hit))
        for candidate in candidates.values():
            candidate["chunks"] = candidate["chunks"][:MAX_CHUNKS_PER_PAPER]
        return sorted(
            candidates.values(),
            key=lambda item: (-item["score"], min(item["source_ranks"].values()), item["paper_id"]),
        )

    def _metadata_for(self, candidates: list[dict[str, Any]], keyword_hits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        paper_ids = [candidate["paper_id"] for candidate in candidates]
        metadata = {str(hit.get("paper_id") or hit.get("id")): _normalize_metadata(dict(hit)) for hit in keyword_hits}
        if self.metadata_provider is not None and paper_ids:
            fetched = self.metadata_provider(paper_ids) or {}
            for paper_id, document in fetched.items():
                metadata.setdefault(str(paper_id), {}).update(_normalize_metadata(dict(document or {})))
        for paper_id in paper_ids:
            metadata.setdefault(paper_id, {"id": paper_id})
        return metadata

    @staticmethod
    def _to_result(candidate: dict[str, Any], metadata: dict[str, Any]) -> SearchResult:
        return SearchResult(
            paper_id=candidate["paper_id"],
            score=round(float(candidate["score"]), 8),
            metadata=metadata,
            source_ranks=dict(candidate["source_ranks"]),
            chunks=list(candidate["chunks"]),
        )

    def _enrich_chunk_provenance(self, results: list[SearchResult]) -> None:
        if self.chunk_provider is None:
            return
        missing = [result.paper_id for result in results if not result.chunks]
        if not missing:
            return
        try:
            provided = self.chunk_provider(missing, MAX_CHUNKS_PER_PAPER) or {}
        except Exception as exc:
            LOGGER.warning("chunk provenance unavailable: %s", exc)
            return
        for result in results:
            if result.chunks:
                continue
            result.chunks = [_to_chunk(chunk) for chunk in provided.get(result.paper_id, [])][:MAX_CHUNKS_PER_PAPER]

    def _rerank(self, query: str, results: list[SearchResult], rerank_k: int) -> None:
        selected = results[:rerank_k]
        documents = [_rerank_document(result) for result in selected]
        scores = list(self.reranker.score(query, documents))
        if len(scores) != len(selected):
            raise RuntimeError("reranker returned an unexpected number of scores")
        for result, score in zip(selected, scores):
            result.rerank_score = float(score)
        selected.sort(key=lambda item: (-float(item.rerank_score or 0.0), -item.score, item.paper_id))
        results[:rerank_k] = selected


def payload_to_request(payload: dict[str, Any]) -> SearchRequest:
    page = max(_int_payload(payload, "page", 1), 1)
    size = min(max(_int_payload(payload, "size", _int_payload(payload, "top_k", DEFAULT_TOP_K)), 1), 100)
    top_k = _int_payload(payload, "top_k", page * size)
    top_k = max(top_k, page * size)
    mode = _normalize_mode(str(payload.get("mode") or "hybrid"))
    candidate_default = DEFAULT_HYBRID_CANDIDATE_K if mode == "hybrid" else DEFAULT_CANDIDATE_K
    candidate_k = max(_int_payload(payload, "candidate_k", candidate_default), top_k)
    return SearchRequest(
        query=str(payload.get("query") or ""),
        mode=mode,
        filters=dict(payload.get("filters") or {}),
        top_k=top_k,
        candidate_k=candidate_k,
        rerank_k=_int_payload(payload, "rerank_k", DEFAULT_RERANK_K),
    )


def search_papers(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    mode = _normalize_mode(str(payload.get("mode") or "hybrid"))
    filters = dict(payload.get("filters") or {})
    page = max(_int_payload(payload, "page", 1), 1)
    size = min(max(_int_payload(payload, "size", 20), 1), 100)
    if not query:
        return _list_filtered_papers(filters, page, size, mode)

    request = payload_to_request({**payload, "mode": mode})
    response = build_search_service().search(request)
    if response.error:
        return {
            "total": 0,
            "candidate_total": 0,
            "total_matches": 0,
            "keyword_total": None,
            "page": page,
            "size": size,
            "mode": mode,
            "list": [],
            "error": response.error,
            "retrieval_stages": response.retrieval_stages,
            "degraded_reasons": response.degraded_reasons,
        }

    offset = (page - 1) * size
    page_items = response.results[offset : offset + size]
    return {
        "total": response.total_matches if response.total_matches is not None else response.total_candidates,
        "candidate_total": response.total_candidates,
        "total_matches": response.total_matches,
        "keyword_total": response.keyword_total,
        "page": page,
        "size": size,
        "mode": mode,
        "list": [_to_frontend_result(result) for result in page_items],
        "retrieval_stages": response.retrieval_stages,
        "degraded_reasons": response.degraded_reasons,
    }


def build_search_service() -> HybridSearchService:
    reranker = None
    if KB_RERANKER_BACKEND == "api":
        try:
            reranker = ApiReranker()
        except Exception as exc:
            LOGGER.warning("API reranker is not configured: %s", exc)
    elif BGE_RERANKER_MODEL:
        try:
            reranker = BgeReranker()
        except Exception as exc:
            LOGGER.warning("reranker is not configured: %s", exc)
    return HybridSearchService(
        semantic_search=search_milvus_hits,
        keyword_search=search_elasticsearch_hits,
        metadata_provider=get_paper_metadata,
        chunk_provider=get_best_chunks,
        reranker=reranker,
    )


def _list_filtered_papers(filters: dict[str, Any], page: int, size: int, mode: str) -> dict[str, Any]:
    with get_db() as db:
        rows = _load_filtered_papers(db, filters)
        total = len(rows)
        offset = (page - 1) * size
        papers = [serialize_paper(dict(row)) for row in rows[offset : offset + size]]
    return {
        "total": total,
        "candidate_total": total,
        "total_matches": total,
        "keyword_total": None,
        "page": page,
        "size": size,
        "mode": mode,
        "list": [_paper_to_frontend_result(paper, 0.0, []) for paper in papers],
        "retrieval_stages": [],
        "degraded_reasons": [],
    }


def _load_filtered_papers(db, filters: dict[str, Any]):
    sql = ["SELECT * FROM papers WHERE 1=1", "AND delete_time IS NULL"]
    params: list[Any] = []
    if filters.get("year_start"):
        sql.append("AND publish_year >= ?")
        params.append(int(filters["year_start"]))
    if filters.get("year_end"):
        sql.append("AND publish_year <= ?")
        params.append(int(filters["year_end"]))
    if filters.get("venue"):
        sql.append("AND publish_venue = ?")
        params.append(filters["venue"])
    if filters.get("publish_venue"):
        sql.append("AND publish_venue = ?")
        params.append(filters["publish_venue"])
    if filters.get("research_area"):
        sql.append("AND research_area = ?")
        params.append(filters["research_area"])
    if filters.get("subfield"):
        sql.append("AND subfield = ?")
        params.append(filters["subfield"])
    if filters.get("source"):
        sql.append("AND source = ?")
        params.append(filters["source"])
    sql.append("ORDER BY COALESCE(publish_year, 0) DESC, created_at DESC")
    return db.execute(" ".join(sql), params).fetchall()


def _to_frontend_result(result: SearchResult) -> dict[str, Any]:
    return _paper_to_frontend_result(
        _normalize_metadata(dict(result.metadata)),
        result.score,
        [chunk.to_dict() for chunk in result.chunks],
        source_ranks=result.source_ranks,
        rerank_score=result.rerank_score,
    )


def _paper_to_frontend_result(
    paper: dict[str, Any],
    score: float,
    chunks: list[dict[str, Any]],
    *,
    source_ranks: dict[str, int] | None = None,
    rerank_score: float | None = None,
) -> dict[str, Any]:
    return {
        "id": paper.get("id") or paper.get("paper_id"),
        "title": paper.get("title") or "",
        "authors": paper.get("author") or paper.get("authors") or [],
        "author": paper.get("author") or paper.get("authors") or [],
        "venue": paper.get("publish_venue") or paper.get("venue"),
        "year": paper.get("publish_year") or paper.get("year"),
        "abstract": paper.get("abstract") or "",
        "abstract_preview": preview(paper.get("abstract")),
        "keywords": paper.get("keywords") or [],
        "citation_count": paper.get("citation_count") or paper.get("citations") or 0,
        "pdf_url": paper.get("pdf_url"),
        "score": round(float(score), 6),
        "chunks": [_frontend_chunk(chunk) for chunk in chunks[:MAX_CHUNKS_PER_PAPER]],
        "source_ranks": source_ranks or {},
        "rerank_score": rerank_score,
    }


def _frontend_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id"),
        "chunk_index": chunk.get("chunk_index"),
        "page_no": chunk.get("page_no") or chunk.get("page_start"),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "char_start": chunk.get("char_start"),
        "char_end": chunk.get("char_end"),
        "score": chunk.get("score"),
        "content": preview(chunk.get("content"), 320),
    }


def _to_chunk(hit: dict[str, Any]) -> ChunkProvenance:
    return ChunkProvenance(
        chunk_id=str(hit.get("chunk_id") or ""),
        chunk_index=_int_or_none(hit.get("chunk_index")),
        content=str(hit.get("content") or ""),
        score=float(hit["score"]) if hit.get("score") is not None else None,
        page_no=_int_or_none(hit.get("page_no")),
        page_start=_int_or_none(hit.get("page_start") if hit.get("page_start") is not None else hit.get("page_no")),
        page_end=_int_or_none(hit.get("page_end") if hit.get("page_end") is not None else hit.get("page_no")),
        char_start=_int_or_none(hit.get("char_start")),
        char_end=_int_or_none(hit.get("char_end")),
    )


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    year = metadata.get("publish_year") or metadata.get("year")
    if filters.get("year_start") is not None and (year is None or int(year) < int(filters["year_start"])):
        return False
    if filters.get("year_end") is not None and (year is None or int(year) > int(filters["year_end"])):
        return False
    field_pairs = [
        ("venue", "publish_venue"),
        ("publish_venue", "publish_venue"),
        ("research_area", "research_area"),
        ("subfield", "subfield"),
        ("ccf_level", "ccf_level"),
    ]
    for filter_name, metadata_name in field_pairs:
        requested = filters.get(filter_name)
        if requested is not None and requested != "" and metadata.get(metadata_name) != requested:
            return False
    return True


def _normalize_metadata(row: dict[str, Any]) -> dict[str, Any]:
    for name in ("keywords", "related_papers", "tasks", "methods", "author"):
        if name in row:
            row[name] = row.get(name) if isinstance(row.get(name), list) else loads_list(row.get(name))
    row["authors"] = row.get("author") or row.get("authors") or []
    row["year"] = row.get("publish_year") or row.get("year")
    row["venue"] = row.get("publish_venue") or row.get("venue")
    return row


def _keyword_total_hits(keyword_hits: list[dict[str, Any]]) -> int | None:
    for hit in keyword_hits:
        if hit.get("_total_hits") is not None:
            return int(hit["_total_hits"])
    return None


def _rerank_document(result: SearchResult) -> str:
    title = str(result.metadata.get("title") or "")
    content = "\n".join(chunk.content for chunk in result.chunks)
    abstract = str(result.metadata.get("abstract") or "")
    return f"{title}\n{abstract}\n{content}".strip()


def _normalize_mode(mode: str) -> str:
    return str(mode or "hybrid").lower()


def _int_payload(payload: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(payload.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
