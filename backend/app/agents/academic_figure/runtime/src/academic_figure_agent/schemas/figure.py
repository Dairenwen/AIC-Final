from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from config.constants import (
    SUPPORTED_CODE_FORMATS,
    SUPPORTED_EXPORT_FORMATS,
    SUPPORTED_LANGUAGES,
)

FigureType = Literal["auto", "line", "bar", "scatter", "box", "heatmap", "flowchart", "image_panel"]


class LocalizedText(BaseModel):
    zh: str = ""
    en: str = ""

    def get(self, language: str) -> str:
        return getattr(self, language, "") or self.en or self.zh


class DiagramNode(BaseModel):
    id: str
    label: LocalizedText
    group: str | None = None


class DiagramEdge(BaseModel):
    source: str
    target: str
    label: LocalizedText = Field(default_factory=LocalizedText)


class FigureRequest(BaseModel):
    prompt: str = Field(min_length=3)
    data_files: list[Path] = Field(default_factory=list)
    context_files: list[Path] = Field(default_factory=list)
    sketch_files: list[Path] = Field(default_factory=list)
    output_dir: Path | None = None
    figure_type: FigureType = "auto"
    export_formats: list[str] = Field(default_factory=lambda: ["pdf", "svg", "png"])
    code_formats: list[str] = Field(default_factory=lambda: ["python", "r", "latex", "mermaid"])
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    offline: bool = False

    @field_validator("export_formats")
    @classmethod
    def validate_export_formats(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.lower() for value in values))
        unsupported = set(normalized) - SUPPORTED_EXPORT_FORMATS
        if unsupported:
            raise ValueError(f"Unsupported export formats: {sorted(unsupported)}")
        return normalized

    @field_validator("code_formats")
    @classmethod
    def validate_code_formats(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.lower() for value in values))
        unsupported = set(normalized) - SUPPORTED_CODE_FORMATS
        if unsupported:
            raise ValueError(f"Unsupported code formats: {sorted(unsupported)}")
        if "python" not in normalized:
            normalized.insert(0, "python")
        return normalized

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.lower() for value in values))
        unsupported = set(normalized) - SUPPORTED_LANGUAGES
        if unsupported:
            raise ValueError(f"Unsupported languages: {sorted(unsupported)}")
        if not normalized:
            raise ValueError("At least one output language is required")
        return normalized


class DatasetSummary(BaseModel):
    normalized_path: Path | None = None
    source_files: list[str] = Field(default_factory=list)
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    numeric_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    missing_values: dict[str, int] = Field(default_factory=dict)
    preview: list[dict[str, object]] = Field(default_factory=list)
    sha256: str | None = None


class FigureSpec(BaseModel):
    figure_type: FigureType
    title: LocalizedText
    x: str | None = None
    y: str | None = None
    series: str | None = None
    error: str | None = None
    xlabel: LocalizedText = Field(default_factory=LocalizedText)
    ylabel: LocalizedText = Field(default_factory=LocalizedText)
    width_inches: float = Field(default=6.5, ge=3, le=16)
    height_inches: float = Field(default=4.2, ge=2.5, le=12)
    dpi: int = Field(default=300, ge=150, le=1200)
    palette: list[str] = Field(default_factory=list)
    legend: bool = True
    grid: bool = True
    caption_focus: LocalizedText = Field(default_factory=LocalizedText)
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class CaptionSet(BaseModel):
    zh: str = ""
    en: str = ""


class QualityCheck(BaseModel):
    name: str
    status: Literal["passed", "warning", "failed"]
    message: str


class QualityReport(BaseModel):
    passed: bool
    checks: list[QualityCheck]
    generated_files: list[str] = Field(default_factory=list)
    revision: int = 0


class ArtifactManifest(BaseModel):
    output_dir: Path
    figures: dict[str, dict[str, str]] = Field(default_factory=dict)
    code: dict[str, str] = Field(default_factory=dict)
    captions: dict[str, str] = Field(default_factory=dict)
    data_file: str | None = None
    config_file: str
    quality_report_file: str
    manifest_file: str | None = None


class FigureResult(BaseModel):
    request: FigureRequest
    spec: FigureSpec
    dataset: DatasetSummary
    captions: CaptionSet
    quality_report: QualityReport
    artifacts: ArtifactManifest
    warnings: list[str] = Field(default_factory=list)
