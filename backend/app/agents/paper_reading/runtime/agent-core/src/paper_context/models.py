from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from schemas.models import DocumentIR, Id128, KnowledgeChunk, PaperRecord, Sha256


SplitterStrategy = Literal[
    "fixed_boundary_v1",
    "paragraph_sentence_v1",
    "section_parent_child_v1",
]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ExactNonBlankText = Annotated[str, StringConstraints(min_length=1)]


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceObjectSpan(ContextModel):
    object_id: Id128
    page_number: int = Field(ge=1)
    section_path: list[NonBlank] = Field(min_length=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)

    @model_validator(mode="after")
    def source_range_is_ordered(self) -> "SourceObjectSpan":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ParsedDocument(ContextModel):
    paper_id: Id128
    document_ir: DocumentIR
    clean_text: str
    source_text_sha256: Sha256
    object_spans: list[SourceObjectSpan]


class SplitterRequest(ContextModel):
    paper_id: Id128
    text: ExactNonBlankText
    source_text_sha256: Sha256
    strategy: SplitterStrategy
    profile: Literal["splitter-api-v1"] = "splitter-api-v1"
    idempotency_key: NonBlank | None = None

    @field_validator("text")
    @classmethod
    def text_has_visible_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        return value


class SplitterChunk(ContextModel):
    chunk_id: Id128
    paper_id: Id128
    chunk_index: int = Field(ge=0)
    text: ExactNonBlankText
    content_sha256: Sha256
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)
    source_span_status: Literal["EXACT"]
    source_span_ambiguous: Literal[False]
    section_name: str | None = None
    section_path: list[str]
    parent_chunk_id: Id128 | None = None
    chunk_level: Literal["flat", "child"]
    strategy: SplitterStrategy
    strategy_version: Literal["v1"]
    profile: Literal["splitter-api-v1"]
    profile_version: Literal["v1"]
    config_hash: Sha256
    source_text_sha256: Sha256

    @field_validator("text")
    @classmethod
    def text_has_visible_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace content")
        return value

    @model_validator(mode="after")
    def source_range_is_ordered(self) -> "SplitterChunk":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class SplitterResult(ContextModel):
    execution_id: Id128 = Field(validation_alias=AliasChoices("execution_id", "run_id"))
    status: Literal["COMPLETED"]
    paper_id: Id128
    strategy: SplitterStrategy
    strategy_version: Literal["v1"]
    profile: Literal["splitter-api-v1"]
    profile_version: Literal["v1"]
    source_text_sha256: Sha256
    config_hash: Sha256
    chunks: list[SplitterChunk] = Field(min_length=1)
    warnings: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def run_id(self) -> str:
        """Compatibility view for the optional legacy HTTP adapter."""
        return self.execution_id


class ChunkSet(ContextModel):
    chunk_set_id: Id128
    paper_id: Id128
    splitter_execution_id: Id128 = Field(
        validation_alias=AliasChoices("splitter_execution_id", "splitter_run_id")
    )
    strategy: SplitterStrategy
    strategy_version: Literal["v1"]
    profile: Literal["splitter-api-v1"]
    profile_version: Literal["v1"]
    source_text_sha256: Sha256
    config_hash: Sha256
    chunk_count: int = Field(ge=1)
    warnings: list[dict[str, Any]]

    @property
    def splitter_run_id(self) -> str:
        """Compatibility view for previously serialized internal state."""
        return self.splitter_execution_id


class MetadataProvenance(ContextModel):
    field: Literal["title", "authors", "year", "arxiv_id"]
    source: Literal["PDF_METADATA", "FIRST_PAGE_TEXT", "FILENAME", "SUPPLIED"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    evidence_object_id: Id128 | None = None


class PreparedPaperContext(ContextModel):
    paper_id: Id128
    paper: PaperRecord
    document_ir: DocumentIR
    chunk_set: ChunkSet
    chunks: list[KnowledgeChunk] = Field(min_length=1)
    node_trace: tuple[str, ...]
    metadata_provenance: tuple[MetadataProvenance, ...] = ()


# Compatibility name for callers that still import the former HTTP-oriented model.
SplitterRun = SplitterResult
