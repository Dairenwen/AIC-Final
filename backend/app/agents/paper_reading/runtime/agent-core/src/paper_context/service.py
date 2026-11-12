from __future__ import annotations

from hashlib import sha256

from schemas.models import KnowledgeChunk, PaperRecord

from .models import ChunkSet, PreparedPaperContext, SplitterRequest
from .ports import PdfParserPort, SplitterGatewayPort


PREPARATION_NODE_ORDER = (
    "validate_pdf_identity",
    "parse_pdf",
    "validate_parse_quality",
    "split_clean_text",
    "validate_splitter_lineage",
    "materialize_chunks",
    "complete",
)


class PaperContextPreparationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AutomatedPaperContextPreparer:
    """Parse one text PDF and split clean text through an injected local-first port."""

    def __init__(self, pdf_parser: PdfParserPort, splitter_gateway: SplitterGatewayPort) -> None:
        self.pdf_parser = pdf_parser
        self.splitter_gateway = splitter_gateway

    def prepare(
        self,
        paper: PaperRecord,
        pdf_bytes: bytes,
        strategy: str,
        *,
        idempotency_key: str | None = None,
        allow_parse_review: bool = False,
    ) -> PreparedPaperContext:
        pdf_sha256 = sha256(pdf_bytes).hexdigest()
        if paper.content_sha256 is not None and paper.content_sha256 != pdf_sha256:
            raise PaperContextPreparationError(
                "PDF_IDENTITY_MISMATCH", "PDF bytes do not match the paper content identity."
            )

        parsed = self.pdf_parser.parse(paper.paper_id, pdf_bytes)
        if parsed.paper_id != paper.paper_id or parsed.document_ir.paper_id != paper.paper_id:
            raise PaperContextPreparationError(
                "PARSED_PAPER_ID_MISMATCH", "Parsed document identity does not match the requested paper."
            )
        parse_status = parsed.document_ir.parse_quality.status
        if parse_status != "PASS" and not (
            parse_status == "REVIEW" and allow_parse_review
        ):
            raise PaperContextPreparationError(
                "PARSE_QUALITY_GATE_BLOCKED",
                f"Parse quality is {parse_status}; Human review is required.",
            )

        text_blocks = {item.object_id: item for item in parsed.document_ir.text_blocks}
        span_object_ids = [item.object_id for item in parsed.object_spans]
        if (
            sha256(parsed.clean_text.encode("utf-8")).hexdigest() != parsed.source_text_sha256
            or len(text_blocks) != len(parsed.document_ir.text_blocks)
            or len(span_object_ids) != len(set(span_object_ids))
            or set(span_object_ids) != set(text_blocks)
            or not parsed.clean_text
            or not parsed.object_spans
        ):
            raise PaperContextPreparationError(
                "PARSED_DOCUMENT_LINEAGE_INVALID", "Parsed text and DocumentIR lineage are inconsistent."
            )
        for span in parsed.object_spans:
            block = text_blocks[span.object_id]
            if (
                span.source_end > len(parsed.clean_text)
                or parsed.clean_text[span.source_start : span.source_end] != block.text
                or span.page_number != block.page_number
                or span.section_path != block.section_path
            ):
                raise PaperContextPreparationError(
                    "PARSED_DOCUMENT_LINEAGE_INVALID", "A parsed object span is inconsistent."
                )
        try:
            request = SplitterRequest(
                paper_id=paper.paper_id,
                text=parsed.clean_text,
                source_text_sha256=parsed.source_text_sha256,
                strategy=strategy,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise PaperContextPreparationError(
                "SPLITTER_SELECTION_INVALID", "A supported splitter strategy must be selected explicitly."
            ) from exc
        result = self.splitter_gateway.split(request)
        if (
            result.paper_id != paper.paper_id
            or result.strategy != request.strategy
            or result.profile != request.profile
            or result.source_text_sha256 != parsed.source_text_sha256
        ):
            raise PaperContextPreparationError(
                "SPLITTER_LINEAGE_MISMATCH", "Splitter output lineage does not match the parsed document."
            )

        chunks: list[KnowledgeChunk] = []
        seen_chunk_ids: set[str] = set()
        for expected_index, chunk in enumerate(result.chunks):
            if chunk.chunk_index != expected_index:
                raise PaperContextPreparationError(
                    "SPLITTER_CHUNK_ORDER_INVALID", "Splitter chunks are not contiguous and ordered."
                )
            if chunk.chunk_id in seen_chunk_ids:
                raise PaperContextPreparationError(
                    "SPLITTER_CHUNK_ID_DUPLICATE", "Splitter chunk IDs must be unique within one execution."
                )
            seen_chunk_ids.add(chunk.chunk_id)
            if (
                chunk.paper_id != paper.paper_id
                or chunk.strategy != result.strategy
                or chunk.source_text_sha256 != result.source_text_sha256
                or chunk.config_hash != result.config_hash
                or sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.content_sha256
            ):
                raise PaperContextPreparationError(
                    "SPLITTER_CHUNK_LINEAGE_INVALID", "A splitter chunk failed lineage validation."
                )
            if chunk.source_end > len(parsed.clean_text):
                raise PaperContextPreparationError(
                    "SPLITTER_SOURCE_SPAN_INVALID", "A splitter chunk points outside parsed text."
                )
            if parsed.clean_text[chunk.source_start : chunk.source_end] != chunk.text:
                raise PaperContextPreparationError(
                    "SPLITTER_SOURCE_SPAN_INVALID", "A splitter chunk does not match its source span."
                )
            source_objects = [
                span
                for span in parsed.object_spans
                if span.source_start < chunk.source_end and span.source_end > chunk.source_start
            ]
            if not source_objects:
                raise PaperContextPreparationError(
                    "SPLITTER_OBJECT_LINEAGE_MISSING", "A splitter chunk cannot be traced to DocumentIR."
                )
            section_path = source_objects[0].section_path
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk.chunk_id,
                    paper_id=paper.paper_id,
                    text=chunk.text,
                    page=source_objects[0].page_number,
                    section=section_path,
                    content_type="TEXT",
                    chunk_set_id="pending_chunk_set",
                    document_object_ids=[item.object_id for item in source_objects],
                    source_start=chunk.source_start,
                    source_end=chunk.source_end,
                    splitter_strategy=result.strategy,
                )
            )

        identity = "|".join(
            (
                paper.paper_id,
                result.execution_id,
                result.strategy,
                result.strategy_version,
                result.profile,
                result.profile_version,
                result.config_hash,
                result.source_text_sha256,
            )
        )
        chunk_set_id = f"chunkset_{sha256(identity.encode('utf-8')).hexdigest()}"
        chunks = [chunk.model_copy(update={"chunk_set_id": chunk_set_id}) for chunk in chunks]
        chunk_set = ChunkSet(
            chunk_set_id=chunk_set_id,
            paper_id=paper.paper_id,
            splitter_execution_id=result.execution_id,
            strategy=result.strategy,
            strategy_version=result.strategy_version,
            profile=result.profile,
            profile_version=result.profile_version,
            source_text_sha256=result.source_text_sha256,
            config_hash=result.config_hash,
            chunk_count=len(chunks),
            warnings=result.warnings,
        )
        return PreparedPaperContext(
            paper_id=paper.paper_id,
            paper=paper,
            document_ir=parsed.document_ir,
            chunk_set=chunk_set,
            chunks=chunks,
            node_trace=PREPARATION_NODE_ORDER,
        )
