from __future__ import annotations

from dataclasses import replace

from agent.service import PaperReadingAgent
from llm.gateway import ModelGateway
from paper_context.models import MetadataProvenance
from schemas.models import DocumentIR, KnowledgeChunk, PaperRecord, ReadingRequest

from .context import PreparedReadingContext
from .numeric_relations import NumericRelationGuard
from .planning import ContextRouter, ReadingTaskType
from .reliability import ClaimEvidenceReliabilityGuard
from .renderer import render_reading_markdown


class PreparedPaperReadingAgent:
    """Run the core reading flow from upstream-prepared metadata, Chunks, and DocumentIR."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        *,
        context_router: ContextRouter | None = None,
        max_claim_verification_workers: int = 1,
    ) -> None:
        self.agent = PaperReadingAgent(model_gateway)
        self.context_router = context_router or ContextRouter()
        self.numeric_relation_guard = NumericRelationGuard()
        self.reliability_guard = ClaimEvidenceReliabilityGuard(
            model_gateway,
            max_semantic_workers=max_claim_verification_workers,
        )

    def read(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        chunks: list[KnowledgeChunk] | tuple[KnowledgeChunk, ...],
        document_ir: DocumentIR,
        *,
        metadata_provenance: tuple[MetadataProvenance, ...] = (),
    ) -> PreparedReadingContext:
        if document_ir.paper_id != paper.paper_id:
            raise ValueError("DocumentIR is outside the requested paper scope")
        all_chunks = tuple(chunks)
        reading_plan = self.context_router.build_plan(request, all_chunks, document_ir)
        base_chunks = self.context_router.chunks_from_plan(
            reading_plan, ReadingTaskType.BASE_READING, all_chunks
        )
        if not base_chunks:
            raise ValueError("Reading Plan produced no base-reading context")
        base_chunks = self._prepend_front_matter_when_metadata_is_incomplete(
            paper,
            base_chunks,
            all_chunks,
        )
        agent_output = self.agent.run(request, paper, list(base_chunks))
        guarded_analysis = self.numeric_relation_guard.sanitize_reading_analysis(
            agent_output.analysis
        )
        guarded_result = self.numeric_relation_guard.sanitize_reading_result(
            agent_output.result
        )
        guarded_result, reliability_records = self.reliability_guard.consolidate_reading_result(
            guarded_result
        )
        agent_output = replace(
            agent_output,
            analysis=guarded_analysis,
            result=guarded_result,
        )
        return PreparedReadingContext(
            paper=paper,
            request=request,
            result=agent_output.result,
            markdown=render_reading_markdown(agent_output.result),
            chunks=all_chunks,
            document_ir=document_ir,
            analysis=agent_output.analysis,
            reading_plan=reading_plan,
            context_router=self.context_router,
            reliability_records=reliability_records,
            metadata_provenance=metadata_provenance,
        )

    @staticmethod
    def _prepend_front_matter_when_metadata_is_incomplete(
        paper: PaperRecord,
        base_chunks: tuple[KnowledgeChunk, ...],
        all_chunks: tuple[KnowledgeChunk, ...],
    ) -> tuple[KnowledgeChunk, ...]:
        """Expose bounded, source-grounded front matter to the existing base call."""
        authors_unknown = not paper.authors or all(
            author.strip().lower() in {"unknown", "unknown author"}
            for author in paper.authors
        )
        if paper.year is not None and not authors_unknown:
            return base_chunks

        page_numbers = [chunk.page for chunk in all_chunks if chunk.page is not None]
        if not page_numbers:
            return base_chunks
        first_page = min(page_numbers)
        front_matter = sorted(
            (chunk for chunk in all_chunks if chunk.page == first_page),
            key=lambda chunk: (
                chunk.source_start is None,
                chunk.source_start if chunk.source_start is not None else 0,
                chunk.chunk_id,
            ),
        )[:2]
        existing_ids = {chunk.chunk_id for chunk in base_chunks}
        prepended = tuple(chunk for chunk in front_matter if chunk.chunk_id not in existing_ids)
        return prepended + base_chunks
