from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Document:
    id: str
    title: str
    abstract: str = ""
    publish_year: int | None = None
    publish_venue: str = ""
    research_area: str = ""
    key_words: list[str] = field(default_factory=list)
    authors: str = ""
    pdf_url: str = ""
    source_url: str = ""
    github_url: str = ""
    file_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_record(cls, record: dict[str, Any], file_path: str = "") -> "Document":
        authors = record.get("Author") or record.get("authors") or record.get("author") or ""
        if isinstance(authors, list):
            authors = ", ".join(str(item) for item in authors if item)

        year = record.get("publish_year", record.get("year"))
        try:
            publish_year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            publish_year = None

        keywords = record.get("key_words") or record.get("keywords") or record.get("tasks") or []
        if isinstance(keywords, str):
            keywords = [keywords]

        return cls(
            id=str(record.get("id") or record.get("arxiv_id") or record.get("source_url") or ""),
            title=str(record.get("title") or "").strip(),
            abstract=str(record.get("abstract") or "").strip(),
            publish_year=publish_year,
            publish_venue=str(record.get("publish_venue") or record.get("conference") or "").strip(),
            research_area=str(record.get("research_area") or "").strip(),
            key_words=[str(item).strip() for item in keywords if str(item).strip()],
            authors=str(authors).strip(),
            pdf_url=str(record.get("pdf_url") or "").strip(),
            source_url=str(record.get("source_url") or record.get("arxiv_url") or record.get("url") or "").strip(),
            github_url=str(record.get("github_url") or "").strip(),
            file_path=file_path,
            raw=record,
        )

    def evidence_dict(self, snippet_chars: int = 260) -> dict[str, Any]:
        snippet = self.abstract[:snippet_chars].strip()
        if len(self.abstract) > snippet_chars:
            snippet += "..."
        return {
            "id": self.id,
            "title": self.title,
            "year": self.publish_year,
            "venue": self.publish_venue,
            "keywords": self.key_words,
            "source_url": self.source_url,
            "pdf_url": self.pdf_url,
            "snippet": snippet,
        }


@dataclass
class InnovationRequest:
    research_domain: str
    keywords: list[str] = field(default_factory=list)
    seed_ideas: list[str] = field(default_factory=list)
    time_range: str | None = None
    mode: str = "full"
    constraints: dict[str, Any] = field(default_factory=dict)
    top_k: int = 5
    language: str = "zh"
    additional_context: str = ""
    corpus_dir: str = "data/raw"
    max_documents: int = 80

    def normalized_mode(self) -> str:
        mode = (self.mode or "full").strip().lower()
        return mode if mode in {"full", "evaluate", "expand"} else "full"


@dataclass
class InnovationState:
    request: InnovationRequest
    user_input: str = ""
    research_domain: str = ""
    seed_ideas: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    literature_corpus: list[Document] = field(default_factory=list)
    knowledge_graph: dict[str, Any] = field(default_factory=dict)
    citation_network: dict[str, Any] = field(default_factory=dict)
    research_trends: list[dict[str, Any]] = field(default_factory=list)
    research_gaps: list[dict[str, Any]] = field(default_factory=list)
    candidate_innovations: list[dict[str, Any]] = field(default_factory=list)
    evaluated_innovations: list[dict[str, Any]] = field(default_factory=list)
    refined_proposals: list[dict[str, Any]] = field(default_factory=list)
    evidence_map: dict[str, list[str]] = field(default_factory=dict)
    feedback: list[str] = field(default_factory=list)
    current_step: str = "init"
    iteration_count: int = 0
    workflow_trace: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: InnovationRequest) -> "InnovationState":
        return cls(
            request=request,
            user_input=request.additional_context,
            research_domain=request.research_domain,
            seed_ideas=list(request.seed_ideas),
            constraints=dict(request.constraints),
            metadata={
                "agent": "Innovation Mining",
                "created_at": utc_now_iso(),
                "mode": request.normalized_mode(),
                "top_k": request.top_k,
                "language": request.language,
            },
        )


def dataclass_to_dict(value: Any) -> Any:
    if isinstance(value, Document):
        return value.evidence_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return value
