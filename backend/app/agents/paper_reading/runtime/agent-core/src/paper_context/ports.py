from __future__ import annotations

from typing import Protocol

from schemas.models import DocumentIR

from .models import ParsedDocument, SplitterRequest, SplitterResult
from .table_models import TableExtractionReport


class PdfParserPort(Protocol):
    def parse(self, paper_id: str, pdf_bytes: bytes) -> ParsedDocument: ...


class SplitterGatewayPort(Protocol):
    def list_strategies(self) -> list[str]: ...

    def split(self, request: SplitterRequest) -> SplitterResult: ...


class TableExtractorPort(Protocol):
    def extract(
        self,
        *,
        paper_id: str,
        pdf_bytes: bytes,
        document_ir: DocumentIR,
        source_pdf_sha256: str | None = None,
    ) -> TableExtractionReport: ...
