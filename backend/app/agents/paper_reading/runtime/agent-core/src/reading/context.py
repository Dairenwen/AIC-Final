from __future__ import annotations

from dataclasses import dataclass

from llm.gateway import ReadingAnalysis
from paper_context.models import MetadataProvenance
from schemas.models import (
    DocumentIR,
    KnowledgeChunk,
    PaperRecord,
    ReadingRequest,
    ReadingResult,
)

from .planning import ContextRouter, ReadingPlan, ReadingTaskType
from .reliability import ReliabilityRecord


@dataclass(frozen=True)
class PreparedReadingContext:
    """Core Agent input/output context after upstream parsing and chunking."""

    paper: PaperRecord
    request: ReadingRequest
    result: ReadingResult
    markdown: str
    chunks: tuple[KnowledgeChunk, ...]
    document_ir: DocumentIR
    analysis: ReadingAnalysis
    reading_plan: ReadingPlan
    context_router: ContextRouter
    reliability_records: tuple[ReliabilityRecord, ...] = ()
    metadata_provenance: tuple[MetadataProvenance, ...] = ()

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def chunks_for_task(self, task_type: ReadingTaskType) -> tuple[KnowledgeChunk, ...]:
        return self.context_router.chunks_from_plan(self.reading_plan, task_type, self.chunks)
