from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


PaperSource = Literal["arxiv", "google_scholar", "local_knowledge", "personal_knowledge"]


class AcademicPaper(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    source: PaperSource
    sources: list[PaperSource] = Field(default_factory=list)
    url: str | None = None
    pdf_url: str | None = None
    published_year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    categories: list[str] = Field(default_factory=list)
    retrieval_score: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_sources(self) -> "AcademicPaper":
        if self.source not in self.sources:
            self.sources.insert(0, self.source)
        self.sources = list(dict.fromkeys(self.sources))
        return self


class SearchResponse(BaseModel):
    query: str
    source: PaperSource
    total: int
    papers: list[AcademicPaper]
