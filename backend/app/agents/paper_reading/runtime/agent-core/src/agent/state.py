from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from llm.gateway import ReadingAnalysis
from schemas.models import (
    ArtifactReference,
    KnowledgeChunk,
    PaperRecord,
    ReadingRequest,
    ReadingResult,
    ReadingSourceContext,
)


class ReadingState(TypedDict, total=False):
    reading_run_id: str
    request: ReadingRequest
    source_context: ReadingSourceContext | None
    paper: PaperRecord
    chunks: list[KnowledgeChunk]
    context: list[dict[str, Any]]
    analysis: ReadingAnalysis
    result: ReadingResult
    artifact: ArtifactReference
    status: str
    node_trace: Annotated[list[str], operator.add]
