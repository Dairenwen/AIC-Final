from __future__ import annotations

import os
import re
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import Any

from schemas.models import DocumentIR

from .table_extraction import PyMuPdfTableExtractor, TableExtractionError
from .table_models import (
    TableBoundingBox,
    TableExtractionItem,
    TableExtractionReport,
    TableGrid,
    TableGridCell,
)


@dataclass(frozen=True)
class _DoclingRuntime:
    DocumentConverter: Any
    PdfFormatOption: Any
    DocumentStream: Any
    InputFormat: Any
    PdfPipelineOptions: Any
    TableFormerMode: Any


@dataclass(frozen=True)
class _DoclingCandidate:
    table: Any
    page_number: int
    bbox: tuple[float, float, float, float]
    page_height: float


class DoclingTableExtractor:
    """Use PyMuPDF caption anchors with Docling TableFormer structure recovery."""

    def __init__(
        self,
        *,
        artifacts_path: str | Path | None = None,
        maximum_pdf_bytes: int = 50 * 1024 * 1024,
        maximum_page_count: int = 2_000,
        minimum_anchor_iou: float = 0.1,
    ) -> None:
        if min(maximum_pdf_bytes, maximum_page_count) < 1:
            raise ValueError("table extractor safety limits must be positive")
        if not 0 < minimum_anchor_iou <= 1:
            raise ValueError("minimum_anchor_iou must be in (0, 1]")
        configured_path = artifacts_path or os.getenv("DOCLING_ARTIFACTS_PATH")
        self.artifacts_path = (
            Path(configured_path).expanduser() if configured_path is not None else None
        )
        self.maximum_pdf_bytes = maximum_pdf_bytes
        self.maximum_page_count = maximum_page_count
        self.minimum_anchor_iou = minimum_anchor_iou
        self.anchor_extractor = PyMuPdfTableExtractor(
            maximum_pdf_bytes=maximum_pdf_bytes,
            maximum_page_count=maximum_page_count,
        )

    def extract(
        self,
        *,
        paper_id: str,
        pdf_bytes: bytes,
        document_ir: DocumentIR,
        source_pdf_sha256: str | None = None,
    ) -> TableExtractionReport:
        anchors = self.anchor_extractor.extract(
            paper_id=paper_id,
            pdf_bytes=pdf_bytes,
            document_ir=document_ir,
            source_pdf_sha256=source_pdf_sha256,
        )
        artifacts_path = self._required_artifacts_path()
        runtime = self._load_docling()
        docling_document = self._convert(
            runtime=runtime,
            paper_id=paper_id,
            pdf_bytes=pdf_bytes,
            artifacts_path=artifacts_path,
        )
        candidates = self._candidates(docling_document)
        used_candidates: set[int] = set()
        results: list[TableExtractionItem] = []

        for anchor in anchors.results:
            if anchor.grid is None:
                results.append(anchor)
                continue
            candidate_index = self._best_candidate_index(
                anchor=anchor.grid,
                candidates=candidates,
                used_candidates=used_candidates,
            )
            if candidate_index is None:
                results.append(self._pymupdf_fallback(anchor))
                continue
            candidate = candidates[candidate_index]
            try:
                grid = self._build_grid(
                    anchor=anchor.grid,
                    candidate=candidate,
                    source_pdf_sha256=anchors.source_pdf_sha256,
                    extractor_version=version("docling"),
                )
            except (TypeError, ValueError):
                results.append(self._pymupdf_fallback(anchor))
                continue
            used_candidates.add(candidate_index)
            results.append(anchor.model_copy(update={"grid": grid}))

        return TableExtractionReport(
            paper_id=paper_id,
            source_pdf_sha256=sha256(pdf_bytes).hexdigest(),
            extractor="DOCLING_PYMUPDF_HYBRID",
            extractor_version=version("docling"),
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

    def _required_artifacts_path(self) -> Path:
        if self.artifacts_path is None:
            raise TableExtractionError(
                "DOCLING_MODELS_NOT_CONFIGURED",
                "Set DOCLING_ARTIFACTS_PATH after downloading layout and tableformer models.",
            )
        if not self.artifacts_path.is_dir():
            raise TableExtractionError(
                "DOCLING_MODELS_NOT_FOUND",
                "The configured Docling artifacts directory does not exist.",
            )
        return self.artifacts_path

    @staticmethod
    def _load_docling() -> _DoclingRuntime:
        try:
            from docling.datamodel.base_models import DocumentStream, InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ModuleNotFoundError as exc:
            raise TableExtractionError(
                "DOCLING_NOT_INSTALLED",
                "Synchronize the locked agent-core dependencies to enable Docling extraction.",
            ) from exc
        return _DoclingRuntime(
            DocumentConverter=DocumentConverter,
            PdfFormatOption=PdfFormatOption,
            DocumentStream=DocumentStream,
            InputFormat=InputFormat,
            PdfPipelineOptions=PdfPipelineOptions,
            TableFormerMode=TableFormerMode,
        )

    def _convert(
        self,
        *,
        runtime: _DoclingRuntime,
        paper_id: str,
        pdf_bytes: bytes,
        artifacts_path: Path,
    ) -> Any:
        options = runtime.PdfPipelineOptions(
            artifacts_path=artifacts_path,
            do_ocr=False,
            do_table_structure=True,
            enable_remote_services=False,
            allow_external_plugins=False,
        )
        options.table_structure_options.mode = runtime.TableFormerMode.ACCURATE
        options.table_structure_options.do_cell_matching = True
        converter = runtime.DocumentConverter(
            format_options={
                runtime.InputFormat.PDF: runtime.PdfFormatOption(
                    pipeline_options=options
                )
            }
        )
        stream = runtime.DocumentStream(
            name=f"{paper_id}.pdf",
            stream=BytesIO(pdf_bytes),
        )
        try:
            result = converter.convert(
                stream,
                max_num_pages=self.maximum_page_count,
                max_file_size=self.maximum_pdf_bytes,
            )
        except Exception as exc:
            raise TableExtractionError(
                "DOCLING_CONVERSION_FAILED",
                "Docling could not recover table structure from the PDF.",
            ) from exc
        return result.document

    @staticmethod
    def _candidates(document: Any) -> list[_DoclingCandidate]:
        candidates: list[_DoclingCandidate] = []
        for table in document.tables:
            if not table.prov:
                continue
            provenance = table.prov[0]
            page = document.pages.get(provenance.page_no)
            if page is None:
                continue
            page_height = float(page.size.height)
            bbox = provenance.bbox.to_top_left_origin(page_height).as_tuple()
            candidates.append(
                _DoclingCandidate(
                    table=table,
                    page_number=int(provenance.page_no),
                    bbox=tuple(float(value) for value in bbox),
                    page_height=page_height,
                )
            )
        return candidates

    def _best_candidate_index(
        self,
        *,
        anchor: TableGrid,
        candidates: list[_DoclingCandidate],
        used_candidates: set[int],
    ) -> int | None:
        anchor_bbox = (
            anchor.table_bbox.x0,
            anchor.table_bbox.y0,
            anchor.table_bbox.x1,
            anchor.table_bbox.y1,
        )
        scored = [
            (self._bbox_iou(anchor_bbox, candidate.bbox), index)
            for index, candidate in enumerate(candidates)
            if index not in used_candidates and candidate.page_number == anchor.page_number
        ]
        if not scored:
            return None
        score, index = max(scored)
        return index if score >= self.minimum_anchor_iou else None

    @staticmethod
    def _bbox_iou(
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
        intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
        intersection = intersection_width * intersection_height
        left_area = (left[2] - left[0]) * (left[3] - left[1])
        right_area = (right[2] - right[0]) * (right[3] - right[1])
        union = left_area + right_area - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _pymupdf_fallback(anchor: TableExtractionItem) -> TableExtractionItem:
        assert anchor.grid is not None
        grid = anchor.grid.model_copy(
            update={
                "warnings": [
                    *anchor.grid.warnings,
                    "DOCLING_STRUCTURE_NOT_MATCHED_PYMUPDF_RETAINED",
                ]
            }
        )
        return anchor.model_copy(update={"grid": grid})

    @staticmethod
    def _build_grid(
        *,
        anchor: TableGrid,
        candidate: _DoclingCandidate,
        source_pdf_sha256: str,
        extractor_version: str,
    ) -> TableGrid:
        data = candidate.table.data
        if data.num_rows < 2 or data.num_cols < 2:
            raise ValueError("Docling table grid is too small")
        raw_cells: list[tuple[Any, tuple[float, float, float, float]]] = []
        for cell in data.table_cells:
            if cell.bbox is None:
                continue
            bbox = cell.bbox.to_top_left_origin(candidate.page_height).as_tuple()
            normalized_bbox = tuple(float(value) for value in bbox)
            if (
                normalized_bbox[2] <= normalized_bbox[0]
                or normalized_bbox[3] <= normalized_bbox[1]
            ):
                continue
            raw_cells.append((cell, normalized_bbox))
        if not raw_cells:
            raise ValueError("Docling table has no located cells")

        bounds = [
            candidate.bbox,
            *(bbox for _, bbox in raw_cells),
        ]
        table_bbox = TableBoundingBox(
            x0=max(0, min(item[0] for item in bounds)),
            y0=max(0, min(item[1] for item in bounds)),
            x1=max(item[2] for item in bounds),
            y1=max(item[3] for item in bounds),
        )
        cells: list[TableGridCell] = []
        header_cell_indexes: list[int] = []
        first_row_occupied_columns: set[int] = set()
        for cell, bbox in raw_cells:
            built = TableGridCell(
                cell_id=(
                    f"{anchor.table_id}_"
                    f"r{int(cell.start_row_offset_idx):04d}_"
                    f"c{int(cell.start_col_offset_idx):04d}"
                ),
                row_start=int(cell.start_row_offset_idx),
                row_end=int(cell.end_row_offset_idx),
                column_start=int(cell.start_col_offset_idx),
                column_end=int(cell.end_col_offset_idx),
                cell_bbox=TableBoundingBox(
                    x0=max(0, bbox[0]),
                    y0=max(0, bbox[1]),
                    x1=bbox[2],
                    y1=bbox[3],
                ),
                raw_text=str(cell.text),
                normalized_text=re.sub(r"\s+", " ", str(cell.text)).strip(),
                source="DOCLING_TABLEFORMER",
            )
            if built.row_start == 0:
                first_row_occupied_columns.update(
                    range(built.column_start, built.column_end)
                )
            if (
                bool(getattr(cell, "column_header", False))
                and built.row_start == 0
                and built.row_end == 1
                and built.column_end == built.column_start + 1
                and built.normalized_text
            ):
                header_cell_indexes.append(len(cells))
            cells.append(built)
        cells, normalized_column_count, sparse_header_normalized = (
            DoclingTableExtractor._normalize_sparse_header_columns(
                table_id=anchor.table_id,
                cells=cells,
                header_cell_indexes=header_cell_indexes,
                column_count=int(data.num_cols),
                first_row_occupied_columns=first_row_occupied_columns,
            )
        )
        warnings = [
            "PYMUPDF_EXACT_CAPTION_ANCHOR_USED",
            "DOCLING_ACCURATE_CELL_MATCHING_USED",
        ]
        if sparse_header_normalized:
            warnings.append("DOCLING_SPARSE_HEADER_COLUMNS_NORMALIZED")
        return TableGrid(
            table_id=anchor.table_id,
            label=anchor.label,
            page_number=anchor.page_number,
            table_bbox=table_bbox,
            source_pdf_sha256=source_pdf_sha256,
            extractor="DOCLING_TABLEFORMER",
            extractor_version=extractor_version,
            binding_status="MATCHED_BY_EXACT_CAPTION",
            structure_status="EXTRACTED_CANDIDATE",
            row_count=int(data.num_rows),
            column_count=normalized_column_count,
            cells=cells,
            acceptance_ready=False,
            warnings=warnings,
        )

    @staticmethod
    def _normalize_sparse_header_columns(
        *,
        table_id: str,
        cells: list[TableGridCell],
        header_cell_indexes: list[int],
        column_count: int,
        first_row_occupied_columns: set[int],
    ) -> tuple[list[TableGridCell], int, bool]:
        """Collapse bounded phantom columns around a complete single-row header."""

        headers = sorted(
            (cells[index] for index in header_cell_indexes),
            key=lambda cell: (cell.column_start, cell.cell_bbox.x0),
        )
        missing_columns = column_count - len(headers)
        header_columns = {cell.column_start for cell in headers}
        phantom_columns = set(range(column_count)) - header_columns
        nonempty_first_row = [
            cell
            for cell in cells
            if cell.row_start == 0 and cell.row_end == 1 and cell.normalized_text
        ]
        if (
            len(headers) < 3
            or missing_columns < 1
            or missing_columns > 2
            or headers[0].column_start != 0
            or len(nonempty_first_row) != len(headers)
            or len(header_columns) != len(headers)
            or bool(phantom_columns & first_row_occupied_columns)
            or any(
                not any(
                    cell.row_start > 0
                    and cell.column_start == phantom
                    and cell.column_end == phantom + 1
                    and cell.normalized_text
                    for cell in cells
                )
                for phantom in phantom_columns
            )
            or any(
                left.cell_bbox.x0 >= right.cell_bbox.x0
                for left, right in zip(headers, headers[1:])
            )
        ):
            return cells, column_count, False

        def nearest_header_index(cell: TableGridCell) -> int:
            return min(
                range(len(headers)),
                key=lambda index: (
                    abs(cell.cell_bbox.x0 - headers[index].cell_bbox.x0),
                    index,
                ),
            )

        grouped: dict[
            tuple[int, int, int, int],
            list[TableGridCell],
        ] = {}
        for cell in cells:
            covered_headers = [
                index
                for index, header in enumerate(headers)
                if cell.column_start
                <= header.column_start
                < cell.column_end
            ]
            if cell.column_end == cell.column_start + 1 or not covered_headers:
                logical_start = nearest_header_index(cell)
                logical_end = logical_start + 1
            else:
                logical_start = min(covered_headers)
                logical_end = max(covered_headers) + 1
            grouped.setdefault(
                (cell.row_start, cell.row_end, logical_start, logical_end),
                [],
            ).append(cell)

        normalized: list[TableGridCell] = []
        for row_start, row_end, column_start, column_end in sorted(grouped):
            fragments = sorted(
                grouped[(row_start, row_end, column_start, column_end)],
                key=lambda cell: (
                    cell.cell_bbox.x0,
                    cell.cell_bbox.y0,
                    cell.normalized_text,
                ),
            )
            raw_parts: list[str] = []
            normalized_parts: list[str] = []
            seen_fragments: set[
                tuple[float, float, float, float, str, str]
            ] = set()
            for fragment in fragments:
                fragment_key = (
                    fragment.cell_bbox.x0,
                    fragment.cell_bbox.y0,
                    fragment.cell_bbox.x1,
                    fragment.cell_bbox.y1,
                    fragment.raw_text,
                    fragment.normalized_text,
                )
                if fragment_key in seen_fragments:
                    continue
                seen_fragments.add(fragment_key)
                if fragment.raw_text:
                    raw_parts.append(fragment.raw_text)
                if fragment.normalized_text:
                    normalized_parts.append(fragment.normalized_text)
            normalized.append(
                TableGridCell(
                    cell_id=(
                        f"{table_id}_r{row_start:04d}_c{column_start:04d}_"
                        f"re{row_end:04d}_ce{column_end:04d}"
                    ),
                    row_start=row_start,
                    row_end=row_end,
                    column_start=column_start,
                    column_end=column_end,
                    cell_bbox=TableBoundingBox(
                        x0=min(cell.cell_bbox.x0 for cell in fragments),
                        y0=min(cell.cell_bbox.y0 for cell in fragments),
                        x1=max(cell.cell_bbox.x1 for cell in fragments),
                        y1=max(cell.cell_bbox.y1 for cell in fragments),
                    ),
                    raw_text=" ".join(raw_parts),
                    normalized_text=" ".join(normalized_parts),
                    source="DOCLING_TABLEFORMER",
                )
            )
        return normalized, len(headers), True
