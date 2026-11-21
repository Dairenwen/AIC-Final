from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from llm.gateway import ModelGateway
from paper_context.metadata import recover_first_page_metadata
from paper_context.models import MetadataProvenance
from paper_context.ports import PdfParserPort, SplitterGatewayPort
from paper_context.service import AutomatedPaperContextPreparer
from pypdf import PdfReader
from schemas.models import PaperRecord, ReadingRequest, ReadingWarning

from .context import PreparedReadingContext
from .core import PreparedPaperReadingAgent
from .execution import ExecutionMode
from .renderer import render_reading_markdown


Depth = Literal["OVERVIEW", "STANDARD", "DEEP"]

DEFAULT_FOCUS = {
    "OVERVIEW": ["RESEARCH_QUESTION", "METHOD", "INNOVATION", "LIMITATION"],
    "STANDARD": [
        "RESEARCH_QUESTION",
        "METHOD",
        "EQUATION",
        "FIGURE",
        "TABLE",
        "EXPERIMENT",
        "INNOVATION",
        "LIMITATION",
    ],
    "DEEP": [
        "RESEARCH_QUESTION",
        "METHOD",
        "EQUATION",
        "FIGURE",
        "TABLE",
        "EXPERIMENT",
        "INNOVATION",
        "LIMITATION",
        "REPRODUCIBILITY",
    ],
}

OUTPUT_TEMPLATE = {
    "OVERVIEW": "OVERVIEW_NOTE",
    "STANDARD": "STANDARD_NOTE",
    "DEEP": "DEEP_NOTE",
}


PdfReadingOutput = PreparedReadingContext


def _pdf_metadata(
    pdf_bytes: bytes,
    fallback_title: str,
) -> tuple[
    str,
    list[str],
    list[ReadingWarning],
    tuple[MetadataProvenance, ...],
]:
    warnings: list[ReadingWarning] = []
    provenance: list[MetadataProvenance] = []
    title = fallback_title.strip() or "Untitled paper"
    authors = ["Unknown"]
    try:
        metadata = PdfReader(BytesIO(pdf_bytes), strict=False).metadata
        metadata_title = str(metadata.title).strip() if metadata and metadata.title else ""
        metadata_author = str(metadata.author).strip() if metadata and metadata.author else ""
        if metadata_title:
            title = metadata_title
            provenance.append(
                MetadataProvenance(
                    field="title",
                    source="PDF_METADATA",
                    confidence="HIGH",
                )
            )
        else:
            provenance.append(
                MetadataProvenance(
                    field="title",
                    source="FILENAME",
                    confidence="LOW",
                )
            )
            warnings.append(
                ReadingWarning(
                    warning_code="TITLE_FROM_FILENAME",
                    message="PDF title metadata was unavailable; the filename was used.",
                )
            )
        if metadata_author:
            authors = [metadata_author[:300]]
            provenance.append(
                MetadataProvenance(
                    field="authors",
                    source="PDF_METADATA",
                    confidence="HIGH",
                )
            )
        else:
            warnings.append(
                ReadingWarning(
                    warning_code="AUTHORS_UNKNOWN",
                    message="PDF author metadata was unavailable.",
                )
            )
    except Exception:
        provenance.append(
            MetadataProvenance(
                field="title",
                source="FILENAME",
                confidence="LOW",
            )
        )
        warnings.append(
            ReadingWarning(
                warning_code="PDF_METADATA_UNAVAILABLE",
                message="PDF metadata could not be read; filename-based metadata was used.",
            )
        )
    return title[:1000], authors, warnings, tuple(provenance)


class RealPdfReadingAgent:
    """Direct PDF-to-ReadingResult composition with no HTTP product or persistence layer."""

    def __init__(
        self,
        pdf_parser: PdfParserPort,
        splitter_gateway: SplitterGatewayPort,
        model_gateway: ModelGateway,
        *,
        max_claim_verification_workers: int = 1,
    ) -> None:
        self.preparer = AutomatedPaperContextPreparer(pdf_parser, splitter_gateway)
        self.reading_agent = PreparedPaperReadingAgent(
            model_gateway,
            max_claim_verification_workers=max_claim_verification_workers,
        )

    def read_pdf(
        self,
        pdf_path: str | Path,
        *,
        reading_goal: str,
        depth: Depth = "STANDARD",
        splitter_strategy: str,
        language: str = "zh-CN",
        focus_aspects: list[str] | None = None,
        source_type: Literal["USER_UPLOAD", "ARXIV"] = "USER_UPLOAD",
        source_uri: str | None = None,
        arxiv_id: str | None = None,
        execution_mode: ExecutionMode = "flow_first",
    ) -> PdfReadingOutput:
        if execution_mode not in {"flow_first", "strict"}:
            raise ValueError("execution_mode must be flow_first or strict")
        path = Path(pdf_path)
        pdf_bytes = path.read_bytes()
        content_sha256 = sha256(pdf_bytes).hexdigest()
        paper_id = f"paper_{content_sha256}"
        title, authors, metadata_warnings, initial_metadata_provenance = _pdf_metadata(
            pdf_bytes,
            path.stem,
        )
        paper = PaperRecord(
            paper_id=paper_id,
            source_type=source_type,
            title=title,
            authors=authors,
            year=None,
            arxiv_id=arxiv_id,
            doi=None,
            source_uri=source_uri or f"upload:{content_sha256}",
            version="1",
            content_sha256=content_sha256,
            ingest_status="IMPORTED",
        )
        request = ReadingRequest(
            request_id=f"req_{content_sha256[:32]}",
            mode="SINGLE",
            depth=depth,
            paper_sources=[
                {
                    "paper_id": paper_id,
                    "source_type": source_type,
                    "source_uri": paper.source_uri,
                }
            ],
            reading_goal=reading_goal,
            focus_aspects=focus_aspects or DEFAULT_FOCUS[depth],
            output_template=OUTPUT_TEMPLATE[depth],
            language=language,
        )
        prepared = self.preparer.prepare(
            paper,
            pdf_bytes,
            splitter_strategy,
            idempotency_key=f"read-{content_sha256}-{splitter_strategy}",
            allow_parse_review=execution_mode == "flow_first",
        )
        warning_codes = {warning.warning_code for warning in metadata_warnings}
        recovery = recover_first_page_metadata(
            paper,
            prepared.document_ir,
            recover_title="TITLE_FROM_FILENAME" in warning_codes,
            recover_authors=bool(
                {"AUTHORS_UNKNOWN", "PDF_METADATA_UNAVAILABLE"} & warning_codes
            ),
        )
        paper = recovery.paper
        recovered_fields = {item.field for item in recovery.provenance}
        metadata_provenance = (
            tuple(
                item
                for item in initial_metadata_provenance
                if item.field not in recovered_fields
            )
            + recovery.provenance
        )
        prepared = prepared.model_copy(
            update={
                "paper": paper,
                "metadata_provenance": metadata_provenance,
            }
        )
        output = self.reading_agent.read(
            request,
            paper,
            prepared.chunks,
            prepared.document_ir,
            metadata_provenance=prepared.metadata_provenance,
        )
        result = output.result
        parse_warnings: list[ReadingWarning] = []
        if prepared.document_ir.parse_quality.status == "REVIEW":
            warning_codes = ", ".join(prepared.document_ir.parse_quality.warnings)
            parse_warnings.append(
                ReadingWarning(
                    warning_code="PARSE_REVIEW_CONTINUED",
                    message=(
                        "Flow-first mode continued with partially extractable PDF text"
                        + (f" ({warning_codes})." if warning_codes else ".")
                    ),
                )
            )
        recovered_fields = {item.field for item in recovery.provenance}
        if metadata_warnings:
            resolved_title = result.basic_information.title != paper.title
            resolved_authors = result.basic_information.authors != ["Unknown"]
            unresolved_metadata_warnings = [
                warning
                for warning in metadata_warnings
                if not (
                    (
                        warning.warning_code == "TITLE_FROM_FILENAME"
                        and ("title" in recovered_fields or resolved_title)
                    )
                    or (
                        warning.warning_code == "AUTHORS_UNKNOWN"
                        and ("authors" in recovered_fields or resolved_authors)
                    )
                    or (
                        warning.warning_code == "PDF_METADATA_UNAVAILABLE"
                        and ("title" in recovered_fields or resolved_title)
                        and ("authors" in recovered_fields or resolved_authors)
                    )
                )
            ]
        else:
            unresolved_metadata_warnings = []
        recovery_warnings = [
            ReadingWarning(
                warning_code=f"{item.field.upper()}_RECOVERED_FROM_FIRST_PAGE",
                message=(
                    f"{item.field} was recovered deterministically from first-page text "
                    f"with {item.confidence.lower()} confidence."
                ),
            )
            for item in recovery.provenance
        ]
        additional_warnings = [
            *unresolved_metadata_warnings,
            *recovery_warnings,
            *parse_warnings,
        ]
        if additional_warnings:
            result = result.model_copy(
                update={"warnings": [*result.warnings, *additional_warnings]}
            )
        if result is not output.result:
            output = replace(output, result=result, markdown=render_reading_markdown(result))
        return output
