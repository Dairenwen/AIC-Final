from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from .tools import AcademicPaper, PaperSource


class QueryPlanDraft(BaseModel):
    intent_summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    start_year: int | None = None
    end_year: int | None = None
    queries: list[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    intent_summary: str
    keywords: list[str] = Field(min_length=1, max_length=12)
    start_year: int = Field(ge=1900, le=2100)
    end_year: int = Field(ge=1900, le=2100)
    queries: list[str] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_year_range(self) -> "QueryPlan":
        if self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class LiteratureSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    start_year: int = Field(ge=1900, le=2100)
    end_year: int = Field(ge=1900, le=2100)
    max_results: int = Field(default=10, ge=1, le=100)


class RetrievalBatch(BaseModel):
    source: PaperSource
    query: str
    papers: list[AcademicPaper] = Field(default_factory=list)


class RetrievalError(BaseModel):
    source: PaperSource
    query: str | None = None
    message: str


class LiteratureReport(BaseModel):
    paper_count: int
    selected_paper_ids: list[str] = Field(default_factory=list)
    markdown: str


class LiteratureListItem(BaseModel):
    sequence: int = Field(ge=1, serialization_alias="序号")
    title: str = Field(min_length=1, serialization_alias="标题名")
    year: int | None = Field(default=None, serialization_alias="年份")
    venue: str = Field(default="未知", serialization_alias="会议")

    def to_display_dict(self) -> dict[str, int | str | None]:
        return self.model_dump(mode="json", by_alias=True)


@runtime_checkable
class LiteratureRetriever(Protocol):
    """Future knowledge-base adapters only need to expose this invoke contract."""

    def invoke(self, input: dict[str, Any]) -> dict[str, Any]: ...
