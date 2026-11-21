from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Literal
from urllib.error import HTTPError, URLError

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from config.settings import Settings, get_settings
from src.schemas import AcademicPaper, SearchResponse


SearchMode = Literal["keyword", "precise", "semantic", "hybrid"]
MAX_PAPER_CONTEXT_CHARS = 8_000


class LocalKnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Knowledge-base query or keywords")
    max_results: int = Field(default=10, ge=1, le=100)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    mode: SearchMode = "hybrid"
    venue: str | None = None
    research_area: str | None = None
    source: str | None = None

    @model_validator(mode="after")
    def validate_year_range(self) -> "LocalKnowledgeSearchInput":
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class PersonalKnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Query over papers saved in the user's collections")
    max_results: int = Field(default=10, ge=1, le=100)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    collection_id: str | int | None = None

    @model_validator(mode="after")
    def validate_year_range(self) -> "PersonalKnowledgeSearchInput":
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class LocalKnowledgeSearchTool(BaseTool):
    name: str = "local_knowledge_search"
    description: str = (
        "Search the Zhiyan local paper knowledge base with keyword, semantic, or hybrid retrieval. "
        "Returns normalized paper metadata and relevant text chunks."
    )
    args_schema: type[BaseModel] = LocalKnowledgeSearchInput
    settings: Settings = Field(default_factory=get_settings, exclude=True)
    user_id: str | None = Field(default=None, exclude=True)

    def _run(
        self,
        query: str,
        max_results: int = 10,
        start_year: int | None = None,
        end_year: int | None = None,
        mode: str = "hybrid",
        venue: str | None = None,
        research_area: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        filters = compact_dict(
            {
                "year_start": start_year,
                "year_end": end_year,
                "venue": venue,
                "research_area": research_area,
                "source": source,
            }
        )
        payload = request_json(
            settings=self.settings,
            method="POST",
            path="/search",
            user_id=self.user_id,
            body={
                "query": query,
                "mode": mode,
                "filters": filters,
                "page": 1,
                "size": max_results,
            },
        )
        papers = [
            paper
            for item in response_items(payload, "list", "items", "papers")[:max_results]
            if isinstance(item, dict) and (paper := normalize_paper(item, "local_knowledge")) is not None
        ]
        result = SearchResponse(
            query=query,
            source="local_knowledge",
            total=payload_total(payload, len(papers)),
            papers=papers,
        ).model_dump(mode="json")
        result["mode"] = payload.get("mode", mode) if isinstance(payload, dict) else mode
        return result


class PersonalKnowledgeSearchTool(BaseTool):
    name: str = "personal_knowledge_search"
    description: str = (
        "Search papers saved in the current user's Zhiyan personal collections. "
        "The X-User-Id header keeps each user's collection data isolated."
    )
    args_schema: type[BaseModel] = PersonalKnowledgeSearchInput
    settings: Settings = Field(default_factory=get_settings, exclude=True)
    user_id: str | None = Field(default=None, exclude=True)

    def _run(
        self,
        query: str,
        max_results: int = 10,
        start_year: int | None = None,
        end_year: int | None = None,
        collection_id: str | int | None = None,
    ) -> dict[str, Any]:
        effective_user_id = self.user_id or self.settings.knowledge_api_user_id
        if not effective_user_id.strip():
            raise ValueError("A non-empty X-User-Id is required for personal knowledge search")

        if collection_id is None:
            collection_payload = request_json(
                settings=self.settings,
                method="GET",
                path="/collections",
                user_id=effective_user_id,
            )
            collections = response_items(collection_payload, "list", "items", "collections")
        else:
            collections = [{"id": collection_id}]

        candidates: list[dict[str, Any]] = []
        collection_errors: list[str] = []
        for collection in collections:
            if not isinstance(collection, dict):
                continue
            current_id = first_value(collection, "id", "collection_id")
            embedded = response_items(
                collection,
                "papers",
                "paper_list",
                "paper_summaries",
                "documents",
            )
            if not embedded and current_id is not None:
                try:
                    paper_payload = request_json(
                        settings=self.settings,
                        method="GET",
                        path=f"/collections/{urllib.parse.quote(str(current_id), safe='')}/papers",
                        user_id=effective_user_id,
                        params={"page": 1, "size": 100},
                    )
                    embedded = response_items(paper_payload, "list", "items", "papers")
                except RuntimeError as exc:
                    collection_errors.append(f"collection {current_id}: {exc}")
                    continue

            collection_name = first_value(collection, "collection_name", "name", "title")
            for item in embedded:
                if not isinstance(item, dict):
                    continue
                paper_data = unwrap_personal_paper(item)
                paper_data["_collection_id"] = current_id
                paper_data["_collection_name"] = collection_name
                paper_data["_collection_note"] = first_value(item, "note", "remark", "comment")
                candidates.append(paper_data)

        ranked = rank_personal_candidates(candidates, query, start_year, end_year)
        papers: list[AcademicPaper] = []
        seen: set[str] = set()
        matched_keys = {
            str(
                first_value(item, "id", "paper_id")
                or normalize_title(str(first_value(item, "title", "paper_title", "name") or ""))
            )
            for item, _ in ranked
        }
        matched_keys.discard("")
        for item, score in ranked:
            paper = normalize_paper(item, "personal_knowledge", score_override=score)
            if paper is None:
                continue
            key = str(first_value(item, "id", "paper_id") or normalize_title(paper.title))
            if key in seen:
                continue
            seen.add(key)
            papers.append(paper)
            if len(papers) >= max_results:
                break

        result = SearchResponse(
            query=query,
            source="personal_knowledge",
            total=len(matched_keys),
            papers=papers,
        ).model_dump(mode="json")
        if collection_errors:
            result["collection_errors"] = collection_errors
        return result


def request_json(
    *,
    settings: Settings,
    method: str,
    path: str,
    user_id: str | None,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    url = f"{settings.knowledge_api_base_url.rstrip('/')}/{path.lstrip('/')}"
    query = compact_dict(params or {})
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    effective_user_id = user_id if user_id is not None else settings.knowledge_api_user_id
    if effective_user_id.strip():
        headers["X-User-Id"] = effective_user_id.strip()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=settings.knowledge_api_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Knowledge API returned HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Knowledge API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Knowledge API request timed out") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Knowledge API returned invalid JSON") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Knowledge API error: {payload['error']}")
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("Knowledge API returned an unsupported JSON payload")
    return payload


def normalize_paper(
    item: dict[str, Any],
    source: Literal["local_knowledge", "personal_knowledge"],
    *,
    score_override: float | None = None,
) -> AcademicPaper | None:
    title = str(first_value(item, "title", "paper_title", "name") or "").strip()
    if not title:
        return None
    external_id = first_value(item, "id", "paper_id", "arxiv_id")
    if external_id is None:
        external_id = hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16]
    raw_score = score_override if score_override is not None else first_value(item, "score", "relevance_score")
    score = coerce_float(raw_score)
    chunks = [chunk for chunk in response_items(item, "chunks", "chunk_list") if isinstance(chunk, dict)]
    raw = dict(item)
    raw["chunks"] = chunks
    return AcademicPaper(
        id=f"{source}:{external_id}",
        title=title,
        authors=normalize_authors(first_value(item, "authors", "author", "Author")),
        abstract=paper_context(item, chunks),
        source=source,
        url=first_value(item, "url", "paper_url", "source_url"),
        pdf_url=first_value(item, "pdf_url", "pdf"),
        published_year=coerce_year(first_value(item, "year", "publish_year", "published_year")),
        venue=first_value(item, "venue", "publish_venue", "journal", "conference"),
        citation_count=coerce_int(first_value(item, "citation_count", "citations")),
        doi=first_value(item, "doi", "DOI"),
        categories=normalize_categories(item),
        retrieval_score=score,
        raw=raw,
    )


def rank_personal_candidates(
    candidates: list[dict[str, Any]],
    query: str,
    start_year: int | None,
    end_year: int | None,
) -> list[tuple[dict[str, Any], float]]:
    terms = query_terms(query)
    ranked: list[tuple[dict[str, Any], float]] = []
    for item in candidates:
        year = coerce_year(first_value(item, "year", "publish_year", "published_year"))
        if start_year is not None and (year is None or year < start_year):
            continue
        if end_year is not None and (year is None or year > end_year):
            continue
        title = str(first_value(item, "title", "paper_title", "name") or "").casefold()
        abstract = str(first_value(item, "abstract", "abstract_preview", "summary") or "").casefold()
        note = str(first_value(item, "_collection_note", "note", "remark") or "").casefold()
        score = coerce_float(first_value(item, "score", "relevance_score")) or 0.0
        if terms:
            title_hits = sum(term in title for term in terms)
            abstract_hits = sum(term in abstract for term in terms)
            note_hits = sum(term in note for term in terms)
            score += (2.0 * title_hits + abstract_hits + 1.5 * note_hits) / len(terms)
            if score <= 0:
                continue
        ranked.append((item, score))
    ranked.sort(
        key=lambda pair: (
            pair[1],
            coerce_year(first_value(pair[0], "year", "publish_year", "published_year")) or 0,
        ),
        reverse=True,
    )
    return ranked


def response_items(payload: Any, *keys: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, (dict, list)):
        return response_items(data, *keys)
    return []


def unwrap_personal_paper(item: dict[str, Any]) -> dict[str, Any]:
    nested = item.get("paper")
    if isinstance(nested, dict):
        return {**item, **nested}
    return dict(item)


def paper_context(item: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    abstract = first_value(item, "abstract", "abstract_preview", "summary")
    if abstract:
        sections.append(str(abstract).strip())
    for chunk in chunks:
        content = first_value(chunk, "content", "text", "chunk_text")
        normalized = str(content or "").strip()
        if normalized and normalized not in sections:
            sections.append(normalized)
    return "\n\n".join(sections)[:MAX_PAPER_CONTEXT_CHARS]


def query_terms(query: str) -> list[str]:
    terms = {token.casefold() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]+", query)}
    for sequence in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(sequence) <= 2:
            terms.add(sequence)
        else:
            terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return sorted(term for term in terms if len(term) > 1)


def normalize_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        result = []
        for item in value:
            name = first_value(item, "name", "author_name") if isinstance(item, dict) else item
            if str(name or "").strip():
                result.append(str(name).strip())
        return result
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;；、]", value) if part.strip()]
    return []


def normalize_categories(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("research_area", "domain", "categories", "key_words", "keywords"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(entry).strip() for entry in value if str(entry).strip())
        elif value:
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def payload_total(payload: Any, fallback: int) -> int:
    if isinstance(payload, dict):
        value = first_value(payload, "total", "count")
        parsed = coerce_int(value)
        if parsed is not None:
            return parsed
        data = payload.get("data")
        if isinstance(data, dict):
            return payload_total(data, fallback)
    return fallback


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != ""}


def first_value(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        item = value.get(key)
        if item is not None and item != "":
            return item
    return None


def coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_year(value: Any) -> int | None:
    parsed = coerce_int(value)
    return parsed if parsed is not None and 1900 <= parsed <= 2100 else None


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.casefold())
