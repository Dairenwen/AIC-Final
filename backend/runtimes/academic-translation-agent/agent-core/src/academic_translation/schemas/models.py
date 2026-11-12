from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Precision(str, Enum):
    READING = "reading"
    SUBMISSION = "submission"


class ElementPolicy(BaseModel):
    preserve_formulas: bool = True
    preserve_figures: bool = True
    preserve_references: bool = True
    preserve_headers_footers: bool = True
    translate_figures: bool = False
    figure_min_ocr_confidence: float = 85.0


class TranslationSegment(BaseModel):
    segment_id: str
    kind: Literal["title", "heading", "paragraph", "caption", "reference", "header", "footer", "table"] = "paragraph"
    source_text: str
    translated_text: str = ""
    translatable: bool = True
    page: int | None = None
    style: str | None = None
    tokens: dict[str, str] = Field(default_factory=dict)


class TermEntry(BaseModel):
    source: str
    target: str
    confidence: float = 1.0
    origin: Literal["user", "model", "library", "fallback"] = "model"


class QualityReport(BaseModel):
    total_segments: int
    translated_segments: int
    untranslated_segment_ids: list[str] = Field(default_factory=list)
    terminology_violations: list[str] = Field(default_factory=list)
    protected_token_violations: list[str] = Field(default_factory=list)
    format_violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.untranslated_segment_ids or self.terminology_violations or self.protected_token_violations or self.format_violations)


class TranslationRequest(BaseModel):
    text: str | None = None
    input_path: Path | None = None
    source_lang: str = "en"
    target_lang: str = "zh"
    precision: Precision = Precision.READING
    element_policy: ElementPolicy = Field(default_factory=ElementPolicy)
    glossary: dict[str, str] = Field(default_factory=dict)
    domain: str = "academic"
    persist_terms: bool = False
    selected_segment_ids: list[str] = Field(default_factory=list)
    output_dir: Path | None = None
    # The bilingual Markdown repeats every source paragraph and quickly becomes
    # the largest text artifact. Keep the lean, reviewable translation by
    # default; users can opt in when side-by-side review is genuinely needed.
    include_bilingual_markdown: bool = False
    # Never hand a translated PDF back when it exceeds the requested delivery
    # ceiling.  Ten decimal MB is the default product constraint.
    max_output_bytes: int = 10_000_000
    # Two concurrent blocks saturate the local GPU without long-context queue
    # contention; higher values made full-paper translation slower in practice.
    max_parallel_segments: int = 2
    # Bound a document request so a failed layout subprocess cannot hold an
    # API worker indefinitely.
    pdf_timeout_seconds: int = 600
    preserve_pdf_layout: bool = False
    # Batch mode invokes PDFMathTranslate once for the complete document.  It
    # avoids re-translating every segment in LangGraph before the PDF engine
    # translates the same content again.  Pagewise remains available for PDFs
    # whose upstream engine cannot produce all pages in one pass.
    pdf_layout_mode: Literal["batch", "pagewise", "low_memory"] = "batch"
    # Diagnostics only: bypass PDFMathTranslate's persisted translation cache
    # so a benchmark measures real model inference instead of cache hits.
    ignore_pdf_cache: bool = False
    # PDF-only requests use the layout engine directly.  This is the fast path
    # for a final monolingual PDF; structured Markdown/DOCX exports can still
    # be requested through the full LangGraph path.
    pdf_only: bool = False
    # Request the upstream layout engine's original-plus-translation PDF while
    # retaining the PDF-only fast path.  This avoids a duplicate LangGraph pass.
    pdf_bilingual: bool = False
    # Low-memory mode can unload the dedicated local model between pages.
    # It is explicit because it trades throughput for a stable memory ceiling.
    release_model_between_pages: bool = False
    # Translate a small consecutive page chunk in each low-memory invocation.
    # Two pages cuts PDF-engine starts roughly in half while retaining a bounded
    # layout working set; callers can select one page on particularly tight RAM.
    low_memory_chunk_pages: int = 2

    @model_validator(mode="after")
    def validate_input(self) -> "TranslationRequest":
        if bool(self.text and self.text.strip()) == bool(self.input_path):
            raise ValueError("Provide exactly one of text or input_path.")
        if self.source_lang.lower() == self.target_lang.lower():
            raise ValueError("source_lang and target_lang must differ.")
        if self.input_path and not self.input_path.exists():
            raise ValueError(f"Input file not found: {self.input_path}")
        self.max_parallel_segments = max(1, min(self.max_parallel_segments, 5))
        self.low_memory_chunk_pages = max(1, min(self.low_memory_chunk_pages, 4))
        self.max_output_bytes = max(100_000, self.max_output_bytes)
        self.pdf_timeout_seconds = max(60, min(self.pdf_timeout_seconds, 3600))
        return self


class TranslationResult(BaseModel):
    task_id: str
    source_lang: str
    target_lang: str
    precision: Precision
    segments: list[TranslationSegment]
    glossary: list[TermEntry]
    quality: QualityReport
    outputs: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
