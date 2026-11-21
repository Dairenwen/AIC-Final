from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from schemas.models import DocumentIR, LabeledObject

from .table_models import (
    TableBoundingBox,
    TableExtractionItem,
    TableExtractionReport,
    TableGrid,
    TableGridCell,
)


class TableExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _TableSegment:
    table: Any
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int


class PyMuPdfTableExtractor:
    """Extract caption-bound candidate grids without changing DocumentIR or accepting claims."""

    def __init__(
        self,
        *,
        maximum_pdf_bytes: int = 50 * 1024 * 1024,
        maximum_page_count: int = 2_000,
        maximum_caption_gap: float = 25.0,
        fallback_clip_height: float = 80.0,
    ) -> None:
        if min(maximum_pdf_bytes, maximum_page_count) < 1:
            raise ValueError("table extractor safety limits must be positive")
        if maximum_caption_gap <= 0 or fallback_clip_height <= 0:
            raise ValueError("table extractor geometry limits must be positive")
        self.maximum_pdf_bytes = maximum_pdf_bytes
        self.maximum_page_count = maximum_page_count
        self.maximum_caption_gap = maximum_caption_gap
        self.fallback_clip_height = fallback_clip_height

    def extract(
        self,
        *,
        paper_id: str,
        pdf_bytes: bytes,
        document_ir: DocumentIR,
        source_pdf_sha256: str | None = None,
    ) -> TableExtractionReport:
        pymupdf = self._load_pymupdf()
        actual_sha256 = sha256(pdf_bytes).hexdigest()
        if source_pdf_sha256 is not None and source_pdf_sha256 != actual_sha256:
            raise TableExtractionError(
                "TABLE_PDF_IDENTITY_MISMATCH",
                "Table extraction PDF bytes do not match the supplied source identity.",
            )
        if document_ir.paper_id != paper_id:
            raise TableExtractionError(
                "TABLE_DOCUMENT_SCOPE_MISMATCH",
                "Table extraction DocumentIR does not match the requested paper.",
            )
        if len(pdf_bytes) > self.maximum_pdf_bytes:
            raise TableExtractionError(
                "TABLE_PDF_TOO_LARGE", "The PDF exceeds the table extractor input limit."
            )
        if not pdf_bytes.startswith(b"%PDF-"):
            raise TableExtractionError(
                "TABLE_PDF_SIGNATURE_INVALID", "The table extractor input is not a PDF."
            )

        try:
            document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise TableExtractionError(
                "TABLE_PDF_OPEN_FAILED", "The PDF could not be opened for table extraction."
            ) from exc
        try:
            if document.is_encrypted:
                raise TableExtractionError(
                    "TABLE_PDF_ENCRYPTED", "Encrypted PDFs are not supported for table extraction."
                )
            if document.page_count > self.maximum_page_count:
                raise TableExtractionError(
                    "TABLE_PDF_PAGE_LIMIT_EXCEEDED",
                    "The PDF exceeds the table extractor page limit.",
                )
            if document.page_count != len(document_ir.pages):
                raise TableExtractionError(
                    "TABLE_PAGE_COUNT_MISMATCH",
                    "Table extraction page count does not match DocumentIR.",
                )
            results = [
                self._extract_one(
                    pymupdf=pymupdf,
                    document=document,
                    table_object=table_object,
                    source_pdf_sha256=actual_sha256,
                )
                for table_object in document_ir.tables
            ]
        finally:
            document.close()

        return TableExtractionReport(
            paper_id=paper_id,
            source_pdf_sha256=actual_sha256,
            extractor="PYMUPDF",
            extractor_version=pymupdf.__version__,
            document_table_count=len(results),
            extracted_candidate_count=sum(
                item.status == "EXTRACTED_CANDIDATE" for item in results
            ),
            caption_not_found_count=sum(item.status == "CAPTION_NOT_FOUND" for item in results),
            structure_not_found_count=sum(
                item.status == "STRUCTURE_NOT_FOUND" for item in results
            ),
            results=results,
        )

    @staticmethod
    def _load_pymupdf() -> Any:
        try:
            import pymupdf
        except ModuleNotFoundError as exc:
            raise TableExtractionError(
                "PYMUPDF_NOT_INSTALLED",
                "Synchronize the locked agent-core dependencies to enable table extraction.",
            ) from exc
        return pymupdf

    def _extract_one(
        self,
        *,
        pymupdf: Any,
        document: Any,
        table_object: LabeledObject,
        source_pdf_sha256: str,
    ) -> TableExtractionItem:
        page = document[table_object.page_number - 1]
        caption_bbox = self._find_exact_caption_bbox(page, table_object.label)
        if caption_bbox is None:
            return TableExtractionItem(
                document_object_id=table_object.object_id,
                label=table_object.label,
                page_number=table_object.page_number,
                status="CAPTION_NOT_FOUND",
                reason_code="EXACT_TABLE_CAPTION_NOT_FOUND",
            )

        line_segments = self._line_segments(page)
        selected = self._select_line_segments(line_segments, caption_bbox)
        extractor = "PYMUPDF_LINES"
        warnings: list[str] = []
        if not selected or sum(segment.row_count for segment in selected) < 2:
            selected = self._text_line_fallback(
                page=page,
                caption_bbox=caption_bbox,
            )
            extractor = "PYMUPDF_TEXT_LINES"
            if selected:
                warnings.append("TEXT_COLUMN_FALLBACK_USED")

        if not selected:
            return TableExtractionItem(
                document_object_id=table_object.object_id,
                label=table_object.label,
                page_number=table_object.page_number,
                status="STRUCTURE_NOT_FOUND",
                reason_code="NO_CAPTION_ADJACENT_TABLE_GRID",
            )

        row_count = sum(segment.row_count for segment in selected)
        column_count = selected[0].column_count
        if row_count < 2 or column_count < 2:
            return TableExtractionItem(
                document_object_id=table_object.object_id,
                label=table_object.label,
                page_number=table_object.page_number,
                status="STRUCTURE_NOT_FOUND",
                reason_code="TABLE_GRID_TOO_SMALL",
            )
        if len(selected) > 1:
            warnings.append("VERTICAL_SEGMENTS_MERGED")
        grid = self._build_grid(
            table_object=table_object,
            segments=selected,
            source_pdf_sha256=source_pdf_sha256,
            extractor=extractor,
            extractor_version=pymupdf.__version__,
            warnings=warnings,
        )
        return TableExtractionItem(
            document_object_id=table_object.object_id,
            label=table_object.label,
            page_number=table_object.page_number,
            status="EXTRACTED_CANDIDATE",
            grid=grid,
        )

    @staticmethod
    def _find_exact_caption_bbox(
        page: Any, label: str
    ) -> tuple[float, float, float, float] | None:
        pattern = re.compile(rf"^{re.escape(label)}\s*[.:]", re.IGNORECASE)
        for block in page.get_text("blocks", sort=True):
            text = re.sub(r"\s+", " ", str(block[4])).strip()
            if pattern.match(text):
                return tuple(float(value) for value in block[:4])
        return None

    @staticmethod
    def _line_segments(page: Any) -> list[_TableSegment]:
        finder = page.find_tables(strategy="lines")
        if finder is None:
            return []
        return [
            _TableSegment(
                table=table,
                bbox=tuple(float(value) for value in table.bbox),
                row_count=int(table.row_count),
                column_count=int(table.col_count),
            )
            for table in finder.tables
            if table.row_count >= 1 and table.col_count >= 2
        ]

    def _select_line_segments(
        self,
        segments: list[_TableSegment],
        caption_bbox: tuple[float, float, float, float],
    ) -> list[_TableSegment]:
        eligible = [
            segment
            for segment in segments
            if self._caption_gap(segment.bbox, caption_bbox)
            <= self.maximum_caption_gap
            and self._caption_gap(segment.bbox, caption_bbox) >= -1.0
            and self._horizontal_overlap_ratio(segment.bbox, caption_bbox) >= 0.2
        ]
        if not eligible:
            return []
        seed = min(
            eligible,
            key=lambda item: (
                self._caption_gap(item.bbox, caption_bbox),
                -(item.row_count * item.column_count),
            ),
        )
        selected = [seed]
        top = seed.bbox[1]
        while True:
            preceding = [
                item
                for item in segments
                if item not in selected
                and item.column_count == seed.column_count
                and 0 <= top - item.bbox[3] <= 25
                and abs(item.bbox[0] - seed.bbox[0]) <= 8
                and abs(item.bbox[2] - seed.bbox[2]) <= 8
            ]
            if not preceding:
                break
            next_segment = min(preceding, key=lambda item: top - item.bbox[3])
            selected.append(next_segment)
            top = next_segment.bbox[1]
        return sorted(selected, key=lambda item: item.bbox[1])

    def _text_line_fallback(
        self,
        *,
        page: Any,
        caption_bbox: tuple[float, float, float, float],
    ) -> list[_TableSegment]:
        page_bbox = tuple(float(value) for value in page.rect)
        page_width = page_bbox[2] - page_bbox[0]
        caption_width = caption_bbox[2] - caption_bbox[0]
        if caption_width >= page_width * 0.65:
            x0, x1 = page_bbox[0] + 36, page_bbox[2] - 36
        elif (caption_bbox[0] + caption_bbox[2]) / 2 < page_bbox[0] + page_width / 2:
            x0, x1 = page_bbox[0] + 36, page_bbox[0] + page_width / 2 - 5
        else:
            x0, x1 = page_bbox[0] + page_width / 2 + 5, page_bbox[2] - 36
        clip = (
            x0,
            max(page_bbox[1], caption_bbox[1] - self.fallback_clip_height),
            x1,
            caption_bbox[1],
        )
        finder = page.find_tables(
            clip=clip,
            vertical_strategy="text",
            horizontal_strategy="lines",
            min_words_vertical=2,
            min_words_horizontal=1,
        )
        if finder is None:
            return []
        candidates = [
            _TableSegment(
                table=table,
                bbox=tuple(float(value) for value in table.bbox),
                row_count=int(table.row_count),
                column_count=int(table.col_count),
            )
            for table in finder.tables
            if table.row_count >= 2
            and table.col_count >= 2
            and -1.0 <= self._caption_gap(tuple(table.bbox), caption_bbox)
            <= self.maximum_caption_gap
            and self._horizontal_overlap_ratio(tuple(table.bbox), caption_bbox) >= 0.2
        ]
        if not candidates:
            return []
        return [
            max(
                candidates,
                key=lambda item: (
                    item.row_count * item.column_count,
                    sum(
                        len(str(value or "").strip())
                        for row in item.table.extract()
                        for value in row
                    ),
                ),
            )
        ]

    @staticmethod
    def _caption_gap(
        table_bbox: tuple[float, float, float, float],
        caption_bbox: tuple[float, float, float, float],
    ) -> float:
        return caption_bbox[1] - table_bbox[3]

    @staticmethod
    def _horizontal_overlap_ratio(
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
        denominator = min(left[2] - left[0], right[2] - right[0])
        return overlap / denominator if denominator > 0 else 0.0

    @staticmethod
    def _build_grid(
        *,
        table_object: LabeledObject,
        segments: list[_TableSegment],
        source_pdf_sha256: str,
        extractor: str,
        extractor_version: str,
        warnings: list[str],
    ) -> TableGrid:
        table_bbox = TableBoundingBox(
            x0=min(item.bbox[0] for item in segments),
            y0=min(item.bbox[1] for item in segments),
            x1=max(item.bbox[2] for item in segments),
            y1=max(item.bbox[3] for item in segments),
        )
        cells: list[TableGridCell] = []
        row_offset = 0
        column_count = segments[0].column_count
        for segment in segments:
            if segment.column_count != column_count:
                raise TableExtractionError(
                    "TABLE_SEGMENT_COLUMN_MISMATCH",
                    "Merged table segments do not share a column count.",
                )
            extracted_rows = segment.table.extract()
            for local_row, row in enumerate(segment.table.rows):
                values = extracted_rows[local_row]
                for column, bbox in enumerate(row.cells):
                    if bbox is None:
                        continue
                    column_end = column + 1
                    while column_end < len(row.cells) and row.cells[column_end] is None:
                        column_end += 1
                    raw_text = str(values[column] or "")
                    cells.append(
                        TableGridCell(
                            cell_id=(
                                f"{table_object.object_id}_"
                                f"r{row_offset + local_row:04d}_c{column:04d}"
                            ),
                            row_start=row_offset + local_row,
                            row_end=row_offset + local_row + 1,
                            column_start=column,
                            column_end=column_end,
                            cell_bbox=TableBoundingBox(
                                x0=float(bbox[0]),
                                y0=float(bbox[1]),
                                x1=float(bbox[2]),
                                y1=float(bbox[3]),
                            ),
                            raw_text=raw_text,
                            normalized_text=re.sub(r"\s+", " ", raw_text).strip(),
                            source=(
                                "PDF_NATIVE_TEXT"
                                if raw_text.strip()
                                else "PDF_VECTOR_GEOMETRY"
                            ),
                        )
                    )
            row_offset += segment.row_count
        return TableGrid(
            table_id=table_object.object_id,
            label=table_object.label,
            page_number=table_object.page_number,
            table_bbox=table_bbox,
            source_pdf_sha256=source_pdf_sha256,
            extractor=extractor,
            extractor_version=extractor_version,
            binding_status="MATCHED_BY_EXACT_CAPTION",
            structure_status="EXTRACTED_CANDIDATE",
            row_count=row_offset,
            column_count=column_count,
            cells=cells,
            acceptance_ready=False,
            warnings=warnings,
        )
