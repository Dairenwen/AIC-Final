from __future__ import annotations

import re
from dataclasses import dataclass


HEADING_RE = re.compile(
    r"(?m)^(?P<heading>(?:[1-9]\d*(?:\.[1-9]\d*)*\.|[A-Z]\.)[ \t]+[^\r\n]{2,100}|"
    r"Appendix|Acknowledg(?:e)?ments?(?:\.[^\r\n]*)?)\s*$"
)
SENTENCE_END_RE = re.compile(r"(?<=[.!?])(?:[\"')\]]*)\s+")
PARAGRAPH_BREAK_RE = re.compile(r"\n{2,}")


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class SectionSpan(Span):
    section_id: str
    heading: str


def _trim_span(text: str, start: int, end: int) -> Span:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Span(start, end)


def heading_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in HEADING_RE.finditer(text):
        heading = match.group("heading").strip()
        if heading in {"1. Input", "2. Extract region", "3. Compute", "4. Classify"}:
            continue
        spans.append(_trim_span(text, match.start("heading"), match.end("heading")))
    return spans


def section_spans(text: str, *, major_only: bool = False) -> list[SectionSpan]:
    headings = heading_spans(text)
    if major_only:
        headings = [
            span
            for span in headings
            if re.match(
                r"^(?:[1-9]\d*\.|[A-Z]\.|Appendix|Acknowledg)",
                text[span.start : span.end],
            )
            and not re.match(r"^[1-9]\d*\.[1-9]", text[span.start : span.end])
        ]
    unknown_id = "parent-unknown-000" if major_only else "section-unknown-000"
    if not headings:
        return [SectionSpan(0, len(text), unknown_id, "UNKNOWN")]
    sections: list[SectionSpan] = []
    if headings[0].start > 0:
        sections.append(SectionSpan(0, headings[0].start, unknown_id, "UNKNOWN"))
    prefix = "parent" if major_only else "section"
    for index, heading in enumerate(headings):
        end = headings[index + 1].start if index + 1 < len(headings) else len(text)
        sections.append(
            SectionSpan(
                heading.start,
                end,
                f"{prefix}-{index + 1:03d}",
                text[heading.start : heading.end],
            )
        )
    return sections


def section_for_offset(sections: list[SectionSpan], offset: int) -> SectionSpan:
    for section in sections:
        if section.start <= offset < section.end:
            return section
    return sections[-1]


def _paragraph_spans(text: str, start: int, end: int) -> list[Span]:
    spans: list[Span] = []
    cursor = start
    for match in PARAGRAPH_BREAK_RE.finditer(text, start, end):
        candidate = Span(cursor, match.end())
        if text[candidate.start : candidate.end].strip():
            spans.append(candidate)
        cursor = match.end()
    candidate = Span(cursor, end)
    if text[candidate.start : candidate.end].strip():
        spans.append(candidate)
    return spans


def _sentence_spans(text: str, start: int, end: int) -> list[Span]:
    spans: list[Span] = []
    cursor = start
    for match in SENTENCE_END_RE.finditer(text, start, end):
        candidate = Span(cursor, match.end())
        if text[candidate.start : candidate.end].strip():
            spans.append(candidate)
        cursor = match.end()
    candidate = Span(cursor, end)
    if text[candidate.start : candidate.end].strip():
        spans.append(candidate)
    return spans


def atomic_spans(text: str, section: SectionSpan, max_chars: int) -> list[Span]:
    units: list[Span] = []
    for paragraph in _paragraph_spans(text, section.start, section.end):
        if paragraph.length <= max_chars:
            units.append(paragraph)
            continue
        for sentence in _sentence_spans(text, paragraph.start, paragraph.end):
            if sentence.length <= max_chars:
                units.append(sentence)
                continue
            cursor = sentence.start
            while cursor < sentence.end:
                boundary = min(cursor + max_chars, sentence.end)
                units.append(Span(cursor, boundary))
                cursor = boundary
    if units:
        units[0] = Span(section.start, units[0].end)
        units[-1] = Span(units[-1].start, section.end)
    return units
