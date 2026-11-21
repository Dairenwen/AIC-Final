from __future__ import annotations

import re
import unicodedata
from hashlib import sha256
from io import BytesIO

from pypdf import PdfReader

from schemas.models import (
    DocumentIR,
    LabeledObject,
    Page,
    ParseQuality,
    ReferenceObject,
    Section,
    TextBlock,
)

from .models import ParsedDocument, SourceObjectSpan


NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*)[.)]?\s+(?P<title>.+)$",
    re.IGNORECASE,
)
APPENDIX_HEADING_PATTERN = re.compile(
    r"^(?P<number>[A-H](?:\.\d+)*)(?P<root_dot>\.)?\s+(?P<title>.+)$",
)
NAMED_HEADING_PATTERN = re.compile(
    r"^(?:abstract|introduction|related work|methods?|methodology|experiments?|results?|"
    r"discussion|conclusions?|references)$",
    re.IGNORECASE,
)
FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:Figure|Fig\.?)\s+(\d+[A-Za-z]?)\s*[.:]\s*",
    re.IGNORECASE,
)
TABLE_CAPTION_PATTERN = re.compile(r"^Table\s+(\d+[A-Za-z]?)\s*[.:]\s*", re.IGNORECASE)
CAPTION_REFERENCE_SENTENCE_PATTERN = re.compile(
    r"^(?:Figure|Fig\.?|Table)\s+\d+[A-Za-z]?\.\s+In\s+this\s+(?:figure|table)\b",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(r"^(?:\[(\d+)\]|(\d+)\.\s+)")
EQUATION_NUMBER_PATTERN = re.compile(r"\((\d+[A-Za-z]?)\)\s*$")
MATH_MARKER_PATTERN = re.compile(
    r"(?:=|∑|√|\bsoftmax\b|\bAttention\s*\(|\bmin\s*\(|\bmax\s*\(|\bPE_)",
    re.IGNORECASE,
)


class PdfParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _normalize_page_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        character
        for character in text
        if character in {"\n", "\t"} or unicodedata.category(character) != "Cc"
    )
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _is_heading(text: str) -> bool:
    value = text.strip()
    if len(value) > 160 or "\n" in value:
        return False
    if NAMED_HEADING_PATTERN.fullmatch(value) is not None:
        return True
    match = NUMBERED_HEADING_PATTERN.fullmatch(value)
    if match is not None:
        top_level = int(match.group("number").split(".", 1)[0])
        title = match.group("title").strip()
        sentence_like = re.search(
            r"\b(?:is|are|was|were|has|have|shows|denotes|contains)\b",
            title,
        )
        return (
            1 <= top_level <= 20
            and bool(title)
            and title[0].isupper()
            and sentence_like is None
        )
    appendix_match = APPENDIX_HEADING_PATTERN.fullmatch(value)
    if appendix_match is None:
        return False
    appendix_number = appendix_match.group("number")
    root_dot = appendix_match.group("root_dot")
    title = appendix_match.group("title").strip()
    if not title or not title[0].isupper():
        return False
    if "." in appendix_number:
        return len(title) >= 3
    if root_dot:
        return len(title) >= 4 and " " in title and "," not in title and "." not in title
    return len(title) >= 4 and " " in title


def _heading_level(text: str) -> int:
    match = re.match(r"^(\d+(?:\.\d+)*)", text.strip())
    if match:
        return match.group(1).count(".") + 1
    appendix_match = APPENDIX_HEADING_PATTERN.fullmatch(text.strip())
    return appendix_match.group("number").count(".") + 1 if appendix_match else 1


def _section_path(current: list[str], heading: str) -> list[str]:
    level = _heading_level(heading)
    if level <= 1:
        return [heading]
    parents = current[: level - 1]
    return [*parents, heading]


def _is_special_line(text: str) -> bool:
    value = text.strip()
    return bool(
        _is_heading(value)
        or FIGURE_CAPTION_PATTERN.match(value)
        or TABLE_CAPTION_PATTERN.match(value)
        or REFERENCE_PATTERN.match(value)
        or (EQUATION_NUMBER_PATTERN.search(value) and MATH_MARKER_PATTERN.search(value))
    )


def _text_block_spans(text: str) -> list[tuple[int, int]]:
    """Split normalized page text into exact paragraph-like spans without rewriting source text."""

    lines = list(re.finditer(r"[^\n]+", text))
    spans: list[tuple[int, int]] = []
    active_start: int | None = None
    active_end: int | None = None
    for index, match in enumerate(lines):
        value = match.group(0).strip()
        if not value:
            continue
        special = _is_special_line(value)
        if special:
            if active_start is not None and active_end is not None:
                spans.append((active_start, active_end))
            spans.append((match.start(), match.end()))
            active_start = None
            active_end = None
            continue
        if active_start is None:
            active_start = match.start()
        active_end = match.end()
        next_value = lines[index + 1].group(0).strip() if index + 1 < len(lines) else ""
        accumulated_length = active_end - active_start
        paragraph_end = bool(re.search(r"[.!?。！？]\s*$", value)) and bool(
            re.match(r"[A-Z0-9\u4e00-\u9fff]", next_value)
        )
        if accumulated_length >= 1800 or paragraph_end:
            spans.append((active_start, active_end))
            active_start = None
            active_end = None
    if active_start is not None and active_end is not None:
        spans.append((active_start, active_end))
    return spans


def _numbered_equation_spans(text: str) -> list[tuple[int, int, str]]:
    lines = list(re.finditer(r"[^\n]+", text))
    found: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        if not MATH_MARKER_PATTERN.search(line.group(0)):
            continue
        for candidate in lines[index : index + 6]:
            label_match = EQUATION_NUMBER_PATTERN.search(candidate.group(0).strip())
            if label_match:
                found.append((line.start(), candidate.end(), label_match.group(1)))
                break
    unique: dict[str, tuple[int, int, str]] = {}
    for item in found:
        unique.setdefault(item[2], item)
    return list(unique.values())


class PypdfTextParser:
    """Text-PDF adapter behind a parser-neutral port; OCR and layout reconstruction are excluded."""

    def __init__(
        self,
        minimum_text_characters: int = 80,
        maximum_pdf_bytes: int = 50 * 1024 * 1024,
        maximum_page_count: int = 2_000,
        maximum_clean_text_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        if minimum_text_characters < 1:
            raise ValueError("minimum_text_characters must be positive")
        if min(maximum_pdf_bytes, maximum_page_count, maximum_clean_text_bytes) < 1:
            raise ValueError("parser safety limits must be positive")
        self.minimum_text_characters = minimum_text_characters
        self.maximum_pdf_bytes = maximum_pdf_bytes
        self.maximum_page_count = maximum_page_count
        self.maximum_clean_text_bytes = maximum_clean_text_bytes

    def parse(self, paper_id: str, pdf_bytes: bytes) -> ParsedDocument:
        if len(pdf_bytes) > self.maximum_pdf_bytes:
            raise PdfParseError("PDF_TOO_LARGE", "The PDF exceeds the parser input limit.")
        if not pdf_bytes.startswith(b"%PDF-"):
            raise PdfParseError("PDF_SIGNATURE_INVALID", "The input is not a PDF document.")
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise PdfParseError("PDF_ENCRYPTED", "The PDF requires a password.")
            raw_pages = list(reader.pages)
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("PDF_PARSE_FAILED", "The PDF could not be parsed.") from exc
        if not raw_pages:
            raise PdfParseError("PDF_HAS_NO_PAGES", "The PDF has no pages.")
        if len(raw_pages) > self.maximum_page_count:
            raise PdfParseError("PDF_PAGE_LIMIT_EXCEEDED", "The PDF exceeds the parser page limit.")

        extracted: list[str] = []
        for page in raw_pages:
            try:
                extracted.append(_normalize_page_text(page.extract_text() or ""))
            except Exception as exc:
                raise PdfParseError("PDF_TEXT_EXTRACTION_FAILED", "PDF text extraction failed.") from exc

        non_empty_page_count = sum(bool(text) for text in extracted)
        coverage = non_empty_page_count / len(extracted)
        total_characters = sum(len(text) for text in extracted)
        warnings: list[str] = []
        if non_empty_page_count == 0:
            status = "FAILED"
            warnings.append("NO_EXTRACTABLE_TEXT")
        elif coverage < 1:
            status = "REVIEW"
            warnings.append("EMPTY_TEXT_PAGE_DETECTED")
        elif total_characters < self.minimum_text_characters:
            status = "REVIEW"
            warnings.append("EXTRACTED_TEXT_TOO_SHORT")
        else:
            status = "PASS"

        clean_text = "\n\n".join(extracted)
        page_offsets: list[int] = []
        cursor = 0
        for index, text in enumerate(extracted):
            if index:
                cursor += 2
            page_offsets.append(cursor)
            cursor += len(text)

        pages: list[Page] = []
        text_blocks: list[TextBlock] = []
        sections: list[Section] = []
        equations: list[LabeledObject] = []
        figures: list[LabeledObject] = []
        tables: list[LabeledObject] = []
        references: list[ReferenceObject] = []
        spans: list[SourceObjectSpan] = []
        current_section = ["Document"]
        seen_sections: set[tuple[str, ...]] = set()
        seen_figure_labels: set[str] = set()
        seen_table_labels: set[str] = set()

        for page_number, text in enumerate(extracted, start=1):
            contained_ids: list[str] = []
            for block_index, (local_start, local_end) in enumerate(
                _text_block_spans(text), start=1
            ):
                block_text = text[local_start:local_end]
                stripped = block_text.strip()
                is_reference = bool(
                    REFERENCE_PATTERN.match(stripped)
                    and current_section[-1].lower().startswith("references")
                )
                if _is_heading(stripped) and not is_reference:
                    current_section = _section_path(current_section, stripped)
                    section_key = tuple(current_section)
                    if section_key not in seen_sections:
                        seen_sections.add(section_key)
                        sections.append(
                            Section(
                                object_id=f"section_{len(sections) + 1:04d}",
                                page_number=page_number,
                                section_path=current_section,
                                title=stripped,
                            )
                        )

                object_id = f"text_p{page_number:04d}_b{block_index:04d}"
                text_blocks.append(
                    TextBlock(
                        object_id=object_id,
                        page_number=page_number,
                        section_path=current_section,
                        text=block_text,
                    )
                )
                spans.append(
                    SourceObjectSpan(
                        object_id=object_id,
                        page_number=page_number,
                        section_path=current_section,
                        source_start=page_offsets[page_number - 1] + local_start,
                        source_end=page_offsets[page_number - 1] + local_end,
                    )
                )
                contained_ids.append(object_id)

                figure_match = FIGURE_CAPTION_PATTERN.match(stripped)
                table_match = TABLE_CAPTION_PATTERN.match(stripped)
                if CAPTION_REFERENCE_SENTENCE_PATTERN.match(stripped):
                    figure_match = None
                    table_match = None
                equation_match = EQUATION_NUMBER_PATTERN.search(stripped)
                reference_match = REFERENCE_PATTERN.match(stripped)
                if figure_match:
                    label = f"Figure {figure_match.group(1)}"
                    if label in seen_figure_labels:
                        continue
                    figure = LabeledObject(
                        object_id=f"figure_p{page_number:04d}_{len(figures) + 1:04d}",
                        page_number=page_number,
                        section_path=current_section,
                        label=label,
                        content=block_text,
                    )
                    figures.append(figure)
                    seen_figure_labels.add(label)
                    contained_ids.append(figure.object_id)
                elif table_match:
                    label = f"Table {table_match.group(1)}"
                    if label in seen_table_labels:
                        continue
                    table = LabeledObject(
                        object_id=f"table_p{page_number:04d}_{len(tables) + 1:04d}",
                        page_number=page_number,
                        section_path=current_section,
                        label=label,
                        content=block_text,
                    )
                    tables.append(table)
                    seen_table_labels.add(label)
                    contained_ids.append(table.object_id)
                elif equation_match and MATH_MARKER_PATTERN.search(stripped):
                    equation = LabeledObject(
                        object_id=f"equation_p{page_number:04d}_{len(equations) + 1:04d}",
                        page_number=page_number,
                        section_path=current_section,
                        label=f"Equation {equation_match.group(1)}",
                        content=block_text,
                    )
                    equations.append(equation)
                    contained_ids.append(equation.object_id)

                if reference_match and current_section[-1].lower().startswith("references"):
                    reference_key = reference_match.group(1) or reference_match.group(2)
                    reference = ReferenceObject(
                        object_id=f"reference_{len(references) + 1:04d}",
                        page_number=page_number,
                        section_path=current_section,
                        reference_key=reference_key,
                        citation_text=block_text,
                    )
                    references.append(reference)
                    contained_ids.append(reference.object_id)

            existing_equation_labels = {item.label for item in equations}
            for local_start, local_end, number in _numbered_equation_spans(text):
                label = f"Equation {number}"
                if label in existing_equation_labels:
                    continue
                global_start = page_offsets[page_number - 1] + local_start
                global_end = page_offsets[page_number - 1] + local_end
                source_span = next(
                    (
                        span
                        for span in spans
                        if span.page_number == page_number
                        and span.source_start < global_end
                        and span.source_end > global_start
                    ),
                    None,
                )
                equation = LabeledObject(
                    object_id=f"equation_p{page_number:04d}_{len(equations) + 1:04d}",
                    page_number=page_number,
                    section_path=source_span.section_path if source_span else current_section,
                    label=label,
                    content=text[local_start:local_end],
                )
                equations.append(equation)
                existing_equation_labels.add(label)
                contained_ids.append(equation.object_id)

            pages.append(
                Page(
                    object_id=f"page_{page_number:04d}",
                    page_number=page_number,
                    section_path=current_section,
                    contained_object_ids=contained_ids,
                )
            )

        if not sections:
            warnings.append("NO_SECTION_HEADING_DETECTED")
            sections.append(
                Section(
                    object_id="section_0001",
                    page_number=1,
                    section_path=["Document"],
                    title="Document",
                )
            )
        if len(clean_text.encode("utf-8")) > self.maximum_clean_text_bytes:
            raise PdfParseError("PDF_TEXT_TOO_LARGE", "Extracted text exceeds the splitter input limit.")
        document_ir = DocumentIR(
            paper_id=paper_id,
            pages=pages,
            sections=sections,
            text_blocks=text_blocks,
            equations=equations,
            figures=figures,
            tables=tables,
            references=references,
            parse_quality=ParseQuality(
                status=status,
                text_coverage_ratio=coverage,
                warnings=warnings,
            ),
        )
        return ParsedDocument(
            paper_id=paper_id,
            document_ir=document_ir,
            clean_text=clean_text,
            source_text_sha256=sha256(clean_text.encode("utf-8")).hexdigest(),
            object_spans=spans,
        )
