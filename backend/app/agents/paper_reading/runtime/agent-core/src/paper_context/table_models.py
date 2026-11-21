from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .models import ContextModel


class TableBoundingBox(ContextModel):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    @model_validator(mode="after")
    def coordinates_are_ordered(self) -> "TableBoundingBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("table bounding-box coordinates must be ordered")
        return self


class TableGridCell(ContextModel):
    cell_id: str
    row_start: int = Field(ge=0)
    row_end: int = Field(ge=1)
    column_start: int = Field(ge=0)
    column_end: int = Field(ge=1)
    cell_bbox: TableBoundingBox
    raw_text: str
    normalized_text: str
    source: Literal[
        "PDF_NATIVE_TEXT",
        "PDF_VECTOR_GEOMETRY",
        "DOCLING_TABLEFORMER",
    ]

    @model_validator(mode="after")
    def spans_are_ordered(self) -> "TableGridCell":
        if self.row_end <= self.row_start or self.column_end <= self.column_start:
            raise ValueError("table cell spans must be ordered")
        return self


class TableGrid(ContextModel):
    schema_version: Literal["table_grid_v1"] = "table_grid_v1"
    table_id: str
    label: str
    page_number: int = Field(ge=1)
    table_bbox: TableBoundingBox
    source_pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor: Literal[
        "PYMUPDF_LINES",
        "PYMUPDF_TEXT_LINES",
        "DOCLING_TABLEFORMER",
    ]
    extractor_version: str
    binding_status: Literal["MATCHED_BY_EXACT_CAPTION"]
    structure_status: Literal["EXTRACTED_CANDIDATE"]
    row_count: int = Field(ge=2)
    column_count: int = Field(ge=2)
    cells: list[TableGridCell]
    acceptance_ready: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def cells_fit_grid_and_bbox(self) -> "TableGrid":
        seen: set[str] = set()
        for cell in self.cells:
            if cell.cell_id in seen:
                raise ValueError("table cell IDs must be unique")
            seen.add(cell.cell_id)
            if cell.row_end > self.row_count or cell.column_end > self.column_count:
                raise ValueError("table cell span exceeds the grid")
            if (
                cell.cell_bbox.x0 < self.table_bbox.x0 - 0.5
                or cell.cell_bbox.y0 < self.table_bbox.y0 - 0.5
                or cell.cell_bbox.x1 > self.table_bbox.x1 + 0.5
                or cell.cell_bbox.y1 > self.table_bbox.y1 + 0.5
            ):
                raise ValueError("table cell bounding box exceeds the table bounding box")
        return self


class TableExtractionItem(ContextModel):
    document_object_id: str
    label: str
    page_number: int = Field(ge=1)
    status: Literal["EXTRACTED_CANDIDATE", "CAPTION_NOT_FOUND", "STRUCTURE_NOT_FOUND"]
    grid: TableGrid | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def grid_matches_status(self) -> "TableExtractionItem":
        if self.status == "EXTRACTED_CANDIDATE" and self.grid is None:
            raise ValueError("extracted table item requires a grid")
        if self.status != "EXTRACTED_CANDIDATE" and self.grid is not None:
            raise ValueError("unextracted table item cannot carry a grid")
        return self


class TableExtractionReport(ContextModel):
    schema_version: Literal["table_extraction_report_v1"] = "table_extraction_report_v1"
    paper_id: str
    source_pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extractor: Literal["PYMUPDF", "DOCLING_PYMUPDF_HYBRID"]
    extractor_version: str
    document_table_count: int = Field(ge=0)
    extracted_candidate_count: int = Field(ge=0)
    caption_not_found_count: int = Field(ge=0)
    structure_not_found_count: int = Field(ge=0)
    results: list[TableExtractionItem]

    @model_validator(mode="after")
    def counts_match_results(self) -> "TableExtractionReport":
        if self.document_table_count != len(self.results):
            raise ValueError("table report count does not match results")
        if len({item.document_object_id for item in self.results}) != len(self.results):
            raise ValueError("table report object IDs must be unique")
        expected = {
            "EXTRACTED_CANDIDATE": self.extracted_candidate_count,
            "CAPTION_NOT_FOUND": self.caption_not_found_count,
            "STRUCTURE_NOT_FOUND": self.structure_not_found_count,
        }
        actual = {key: 0 for key in expected}
        for item in self.results:
            actual[item.status] += 1
        if actual != expected:
            raise ValueError("table report status counts do not match results")
        return self
