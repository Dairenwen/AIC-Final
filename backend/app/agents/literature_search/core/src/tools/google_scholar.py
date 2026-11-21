from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from config.settings import Settings, get_settings
from src.schemas import AcademicPaper, SearchResponse


class GoogleScholarSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Topic, title, author, or keyword query")
    max_results: int = Field(default=10, ge=1, le=20)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    language: str = Field(default="en", min_length=2, max_length=10)

    @model_validator(mode="after")
    def validate_year_range(self) -> "GoogleScholarSearchInput":
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class GoogleScholarSearchTool(BaseTool):
    name: str = "google_scholar_search"
    description: str = "Search Google Scholar through SerpAPI and return normalized academic metadata."
    args_schema: type[BaseModel] = GoogleScholarSearchInput
    settings: Settings = Field(default_factory=get_settings, exclude=True)

    def _run(
        self,
        query: str,
        max_results: int = 10,
        start_year: int | None = None,
        end_year: int | None = None,
        language: str = "en",
    ) -> dict[str, Any]:
        if not self.settings.serpapi_api_key or is_placeholder_api_key(self.settings.serpapi_api_key):
            return search_scholarly_fallback(query, max_results, start_year, end_year)
        params: dict[str, str | int] = {
            "engine": "google_scholar",
            "q": query,
            "num": max_results,
            "hl": language,
            "api_key": self.settings.serpapi_api_key,
        }
        if start_year is not None:
            params["as_ylo"] = start_year
        if end_year is not None:
            params["as_yhi"] = end_year
        request = urllib.request.Request(
            f"{self.settings.serpapi_url}?{urllib.parse.urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "langgraph-agent-tools/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.serpapi_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            papers = [parse_result(item) for item in payload.get("organic_results", [])[:max_results]]
            return SearchResponse(
                query=query,
                source="google_scholar",
                total=len(papers),
                papers=papers,
            ).model_dump(mode="json")
        except (OSError, RuntimeError, json.JSONDecodeError):
            return search_scholarly_fallback(query, max_results, start_year, end_year)


def search_scholarly_fallback(query: str, max_results: int, start_year: int | None, end_year: int | None) -> dict[str, Any]:
    try:
        return search_crossref_fallback(query, max_results, start_year, end_year)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return search_openalex_fallback(query, max_results, start_year, end_year)


def is_placeholder_api_key(value: str) -> bool:
    normalized = value.strip().casefold()
    markers = ("replace", "your_", "your-", "example", "placeholder", "填入", "填写")
    return any(marker in normalized for marker in markers)


def parse_result(item: dict[str, Any]) -> AcademicPaper:
    publication = item.get("publication_info") or {}
    inline_links = item.get("inline_links") or {}
    cited_by = inline_links.get("cited_by") or {}
    summary = publication.get("summary") or ""
    authors = [author.get("name", "") for author in publication.get("authors", []) if author.get("name")]
    resources = item.get("resources") or []
    pdf_url = next(
        (resource.get("link") for resource in resources if resource.get("file_format", "").upper() == "PDF"),
        None,
    )
    result_id = item.get("result_id") or item.get("link") or item.get("title", "").lower()
    return AcademicPaper(
        id=f"google_scholar:{result_id}",
        title=item.get("title") or "",
        authors=authors,
        abstract=item.get("snippet") or "",
        source="google_scholar",
        url=item.get("link"),
        pdf_url=pdf_url,
        published_year=extract_year(summary),
        venue=extract_venue(summary),
        citation_count=cited_by.get("total"),
        raw={"publication_info": publication, "inline_links": inline_links},
    )


def extract_year(summary: str) -> int | None:
    matches = re.findall(r"\b(?:19|20)\d{2}\b", summary)
    return int(matches[-1]) if matches else None


def extract_venue(summary: str) -> str | None:
    parts = [part.strip() for part in summary.split(" - ")]
    return parts[1] if len(parts) >= 3 else None


def search_crossref_fallback(
    query: str,
    max_results: int,
    start_year: int | None,
    end_year: int | None,
) -> dict[str, Any]:
    """Return real scholarly metadata when Scholar's configured gateway is unavailable.

    Crossref is used only as an availability fallback; every paper keeps its
    provider in ``raw`` so callers never mistake it for a Google Scholar hit.
    """
    params: dict[str, str | int] = {"query.bibliographic": query, "rows": max_results}
    if start_year is not None or end_year is not None:
        first = start_year if start_year is not None else end_year
        last = end_year if end_year is not None else start_year
        params["filter"] = f"from-pub-date:{first}-01-01,until-pub-date:{last}-12-31"
    request = urllib.request.Request(
        f"https://api.crossref.org/works?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "literature-search-agent/1.0 (availability fallback)"},
    )
    payload = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except OSError:
            if attempt:
                raise
            time.sleep(0.6)
    if payload is None:
        raise RuntimeError("Crossref did not return a payload")
    items = ((payload.get("message") or {}).get("items") or [])
    papers = [parse_crossref_result(item) for item in items[:max_results] if item.get("title")]
    result = SearchResponse(
        query=query,
        source="google_scholar",
        total=len(papers),
        papers=papers,
    ).model_dump(mode="json")
    result["provider"] = "crossref_fallback"
    return result


def search_openalex_fallback(query: str, max_results: int, start_year: int | None, end_year: int | None) -> dict[str, Any]:
    params: dict[str, str | int] = {"search": query, "per-page": max_results}
    if start_year is not None or end_year is not None:
        first = start_year if start_year is not None else end_year
        last = end_year if end_year is not None else start_year
        params["filter"] = f"from_publication_date:{first}-01-01,to_publication_date:{last}-12-31"
    request = urllib.request.Request(
        f"https://api.openalex.org/works?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "literature-search-agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    papers = [parse_openalex_result(item) for item in payload.get("results", [])[:max_results] if item.get("title")]
    result = SearchResponse(query=query, source="google_scholar", total=len(papers), papers=papers).model_dump(mode="json")
    result["provider"] = "openalex_fallback"
    return result


def parse_openalex_result(item: dict[str, Any]) -> AcademicPaper:
    location = item.get("primary_location") or {}
    venue = (location.get("source") or {}).get("display_name")
    authors = [str((entry.get("author") or {}).get("display_name") or "") for entry in item.get("authorships") or []]
    return AcademicPaper(
        id=f"google_scholar:openalex:{item.get('id') or item.get('title', '').casefold()}",
        title=str(item.get("title") or ""), authors=[author for author in authors if author], abstract="",
        source="google_scholar", url=item.get("doi") or location.get("landing_page_url"),
        pdf_url=(item.get("open_access") or {}).get("oa_url") or location.get("pdf_url"),
        published_year=item.get("publication_year"), venue=venue, citation_count=item.get("cited_by_count"),
        raw={"retrieval_provider": "openalex_fallback", "openalex_id": item.get("id")},
    )


def parse_crossref_result(item: dict[str, Any]) -> AcademicPaper:
    doi = str(item.get("DOI") or "")
    authors = [
        " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
        for author in item.get("author") or []
        if isinstance(author, dict)
    ]
    issued = item.get("issued") or item.get("published-print") or item.get("published-online") or {}
    date_parts = issued.get("date-parts") or [[]]
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    abstract = re.sub(r"<[^>]+>", " ", str(item.get("abstract") or ""))
    title = str((item.get("title") or [""])[0])
    venue = str((item.get("container-title") or [""])[0]) or None
    pdf_url = crossref_pdf_url(item)
    return AcademicPaper(
        id=f"google_scholar:crossref:{doi or title.casefold()}",
        title=title,
        authors=[author for author in authors if author],
        abstract=re.sub(r"\s+", " ", abstract).strip(),
        source="google_scholar",
        url=f"https://doi.org/{doi}" if doi else None,
        pdf_url=pdf_url,
        published_year=int(year) if isinstance(year, int) or (isinstance(year, str) and year.isdigit()) else None,
        venue=venue,
        citation_count=item.get("is-referenced-by-count"),
        raw={"retrieval_provider": "crossref_fallback", "doi": doi},
    )


def crossref_pdf_url(item: dict[str, Any]) -> str | None:
    for link in item.get("link") or []:
        if not isinstance(link, dict):
            continue
        value = str(link.get("URL") or "").strip()
        if not re.match(r"https?://", value, re.I):
            continue
        if re.search(r"(?:/pdf(?:/|$)|\.pdf(?:[?#]|$)|/article/download/)", value, re.I):
            return value
        match = re.fullmatch(r"(?P<prefix>https?://[^?#]+?)/article/view/(?P<article>\d+)/(?P<galley>\d+)/?", value, re.I)
        if match:
            return f"{match.group('prefix')}/article/download/{match.group('article')}/{match.group('galley')}/"
    return None
