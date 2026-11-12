from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Literal
from urllib.error import HTTPError

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from src.schemas import AcademicPaper, SearchResponse


ATOM = {"atom": "http://www.w3.org/2005/Atom"}


class ArxivSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Natural-language keywords or an arXiv query")
    max_results: int = Field(default=10, ge=1, le=100)
    authors: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    sort_by: Literal["relevance", "lastUpdatedDate", "submittedDate"] = "relevance"
    sort_order: Literal["ascending", "descending"] = "descending"


class ArxivSearchTool(BaseTool):
    name: str = "arxiv_search"
    description: str = "Search arXiv preprints by keywords, authors and publication years."
    args_schema: type[BaseModel] = ArxivSearchInput
    settings: Settings = Field(default_factory=get_settings, exclude=True)

    @property
    def minimum_interval_seconds(self) -> float:
        return self.settings.arxiv_request_interval_seconds

    def _run(
        self,
        query: str,
        max_results: int = 10,
        authors: list[str] | None = None,
        years: list[int] | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> dict[str, Any]:
        if start_year and end_year and start_year > end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        search_query = build_query(query, authors or [], years or [], start_year, end_year)
        params = urllib.parse.urlencode(
            {
                "search_query": search_query,
                "start": 0,
                "max_results": max_results,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            }
        )
        request = urllib.request.Request(
            f"{self.settings.arxiv_api_url}?{params}",
            headers={"Accept": "application/atom+xml", "User-Agent": "langgraph-agent-tools/0.1"},
        )
        root = fetch_feed(request, self.settings)
        papers = [parse_entry(entry) for entry in root.findall("atom:entry", ATOM)]
        return SearchResponse(query=query, source="arxiv", total=len(papers), papers=papers).model_dump(mode="json")


def fetch_feed(request: urllib.request.Request, settings: Settings) -> ET.Element:
    for attempt in range(settings.arxiv_max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=settings.arxiv_timeout_seconds) as response:
                return ET.fromstring(response.read())
        except HTTPError as exc:
            retryable = exc.code in {429, 503}
            if not retryable or attempt >= settings.arxiv_max_retries:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else settings.arxiv_request_interval_seconds
            time.sleep(max(delay, settings.arxiv_request_interval_seconds))
    raise RuntimeError("arXiv retry loop exited unexpectedly")


def build_query(
    query: str,
    authors: list[str],
    years: list[int],
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    parts = [compile_search_expression(query)]
    parts.extend(f'au:"{escape_term(author)}"' for author in authors[:4])
    if start_year is not None or end_year is not None:
        first = start_year if start_year is not None else end_year
        last = end_year if end_year is not None else start_year
        parts.append(f"submittedDate:[{first}01010000 TO {last}12312359]")
    else:
        parts.extend(f"submittedDate:[{year}01010000 TO {year}12312359]" for year in years[:4])
    return " AND ".join(parts)


def compile_search_expression(query: str) -> str:
    """Convert natural or boolean keyword queries into arXiv's fielded syntax."""
    normalized = query.strip()
    if re.search(r"\b(?:ti|au|abs|co|jr|cat|rn|id|all):", normalized, flags=re.I):
        return normalized
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|\bAND\b|\bOR\b|[^\s()]+', normalized, flags=re.I)
    compiled: list[str] = []
    phrase_parts: list[str] = []

    def flush_phrase() -> None:
        if phrase_parts:
            compiled.append(f'all:"{escape_term(" ".join(phrase_parts))}"')
            phrase_parts.clear()

    for token in tokens:
        upper = token.upper()
        if token in {"(", ")"} or upper in {"AND", "OR"}:
            flush_phrase()
            compiled.append(token if token in {"(", ")"} else upper)
        elif token.startswith('"') and token.endswith('"'):
            flush_phrase()
            compiled.append(f'all:"{escape_term(token[1:-1])}"')
        else:
            phrase_parts.append(token)
    flush_phrase()
    return " ".join(compiled) if compiled else f'all:"{escape_term(normalized)}"'


def escape_term(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').strip()


def parse_entry(entry: ET.Element) -> AcademicPaper:
    entry_url = element_text(entry, "atom:id")
    arxiv_id = entry_url.rstrip("/").split("/")[-1]
    published = element_text(entry, "atom:published")
    year_match = re.match(r"(\d{4})", published)
    pdf_url = next(
        (
            link.attrib.get("href")
            for link in entry.findall("atom:link", ATOM)
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf"
        ),
        None,
    )
    authors = [normalize(element_text(author, "atom:name")) for author in entry.findall("atom:author", ATOM)]
    categories = [item.attrib.get("term", "") for item in entry.findall("atom:category", ATOM)]
    return AcademicPaper(
        id=f"arxiv:{arxiv_id}",
        title=normalize(element_text(entry, "atom:title")),
        authors=[author for author in authors if author],
        abstract=html.unescape(normalize(element_text(entry, "atom:summary"))),
        source="arxiv",
        url=entry_url,
        pdf_url=pdf_url,
        published_year=int(year_match.group(1)) if year_match else None,
        venue="arXiv",
        categories=[category for category in categories if category],
        raw={"published": published},
    )


def element_text(element: ET.Element, path: str) -> str:
    found = element.find(path, ATOM)
    return found.text if found is not None and found.text else ""


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
