from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


TARGET_CHARS = 1024
MAX_CHARS = 1280
OVERLAP_TARGET_CHARS = 200

HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:#{1,6}\s+.+)|"
    r"(?:(?:abstract|introduction|background|method|methods|experiments?|results?|discussion|conclusion|references)\b.*)|"
    r"(?:\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{0,120})"
    r")\s*$",
    re.IGNORECASE,
)
SENTENCE_END_RE = re.compile(r"(?<=[.!?;\u3002\uff01\uff1f])\s+")
PARAGRAPH_RE = re.compile(r"\S(?:.*\S)?", re.DOTALL)


@dataclass(frozen=True)
class SectionSpan:
    section_id: str
    heading: str
    start: int
    end: int


@dataclass(frozen=True)
class TextUnit:
    start: int
    end: int


def build_local_chunk_records(text: str, paper_id: str, strategy: str) -> list[dict[str, Any]]:
    source_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    sections = section_spans(text)
    if strategy == "paragraph_sentence_v1":
        return _flat_chunks(
            text,
            paper_id=paper_id,
            source_sha256=source_sha256,
            strategy=strategy,
            sections=sections,
        )
    if strategy == "section_parent_child_v1":
        return _section_parent_child_chunks(
            text,
            paper_id=paper_id,
            source_sha256=source_sha256,
            strategy=strategy,
            sections=sections,
        )
    raise ValueError(f"unsupported local splitter strategy: {strategy}")


def section_spans(text: str) -> list[SectionSpan]:
    matches: list[tuple[int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        if line and HEADING_RE.match(line):
            matches.append((offset, _clean_heading(line)))
        offset += len(raw_line)

    if not matches or matches[0][0] != 0:
        matches.insert(0, (0, "Document"))

    sections: list[SectionSpan] = []
    for index, (start, heading) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        sections.append(
            SectionSpan(
                section_id=f"section-{index:04d}",
                heading=heading,
                start=start,
                end=max(start, end),
            )
        )
    return sections or [SectionSpan(section_id="section-0000", heading="Document", start=0, end=len(text))]


def section_for_offset(sections: list[SectionSpan], offset: int) -> SectionSpan:
    if not sections:
        return SectionSpan(section_id="section-0000", heading="Document", start=0, end=max(0, offset + 1))
    for section in sections:
        if section.start <= offset < section.end:
            return section
    return sections[-1] if offset >= sections[-1].end else sections[0]


def _flat_chunks(
    text: str,
    *,
    paper_id: str,
    source_sha256: str,
    strategy: str,
    sections: list[SectionSpan],
) -> list[dict[str, Any]]:
    windows = _windows(_text_units(text))
    return [
        _row(
            text,
            paper_id=paper_id,
            source_sha256=source_sha256,
            strategy=strategy,
            index=index,
            start=start,
            end=end,
            sections=sections,
            parent_section_id=None,
        )
        for index, (start, end) in enumerate(windows)
    ]


def _section_parent_child_chunks(
    text: str,
    *,
    paper_id: str,
    source_sha256: str,
    strategy: str,
    sections: list[SectionSpan],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in sections:
        section_text = text[section.start : section.end]
        relative_units = _text_units(section_text)
        units = [TextUnit(section.start + unit.start, section.start + unit.end) for unit in relative_units]
        parent_chunk_id = f"{paper_id}_{strategy}_parent_{section.section_id}"
        for start, end in _windows(units):
            rows.append(
                _row(
                    text,
                    paper_id=paper_id,
                    source_sha256=source_sha256,
                    strategy=strategy,
                    index=len(rows),
                    start=start,
                    end=end,
                    sections=sections,
                    parent_section_id=section.section_id,
                    parent_chunk_id=parent_chunk_id,
                )
            )
    return rows


def _text_units(text: str) -> list[TextUnit]:
    paragraphs = [TextUnit(match.start(), match.end()) for match in PARAGRAPH_RE.finditer(text) if match.group(0).strip()]
    units: list[TextUnit] = []
    for paragraph in paragraphs:
        paragraph_text = text[paragraph.start : paragraph.end]
        cursor = paragraph.start
        pieces = SENTENCE_END_RE.split(paragraph_text)
        if len(pieces) == 1:
            units.append(paragraph)
            continue
        for piece in pieces:
            if not piece:
                continue
            local = text.find(piece, cursor, paragraph.end)
            if local < 0:
                continue
            units.append(TextUnit(local, local + len(piece)))
            cursor = local + len(piece)
    return _bounded_units(units or ([TextUnit(0, len(text))] if text else []))


def _bounded_units(units: list[TextUnit]) -> list[TextUnit]:
    bounded: list[TextUnit] = []
    for unit in units:
        if unit.end - unit.start <= MAX_CHARS:
            bounded.append(unit)
            continue
        start = unit.start
        while start < unit.end:
            end = min(start + MAX_CHARS, unit.end)
            bounded.append(TextUnit(start, end))
            if end >= unit.end:
                break
            start = max(end - OVERLAP_TARGET_CHARS, start + 1)
    return bounded


def _windows(units: list[TextUnit]) -> list[tuple[int, int]]:
    if not units:
        return []
    windows: list[tuple[int, int]] = []
    index = 0
    while index < len(units):
        start = units[index].start
        end = units[index].end
        next_index = index + 1
        while next_index < len(units):
            candidate_end = units[next_index].end
            if candidate_end - start > MAX_CHARS and end > start:
                break
            end = candidate_end
            next_index += 1
            if end - start >= TARGET_CHARS:
                break
        windows.append((start, end))
        if next_index >= len(units):
            break
        overlap_start = next_index
        while (
            overlap_start > index
            and units[next_index - 1].end - units[overlap_start - 1].start < OVERLAP_TARGET_CHARS
        ):
            overlap_start -= 1
        index = max(overlap_start, index + 1)
    return windows


def _row(
    text: str,
    *,
    paper_id: str,
    source_sha256: str,
    strategy: str,
    index: int,
    start: int,
    end: int,
    sections: list[SectionSpan],
    parent_section_id: str | None,
    parent_chunk_id: str | None = None,
) -> dict[str, Any]:
    content = text[start:end]
    first = section_for_offset(sections, start)
    last = section_for_offset(sections, max(start, end - 1))
    section = first.heading if first.section_id == last.section_id else f"{first.heading} -> {last.heading}"
    return {
        "chunk_id": f"{paper_id}_{strategy}_{index:04d}",
        "paper_id": paper_id,
        "chunk_index": index,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "strategy": strategy,
        "splitter_version": strategy,
        "start_offset": start,
        "end_offset": end,
        "section": section,
        "parent_section_id": parent_section_id,
        "section_path": [section],
        "parent_chunk_id": parent_chunk_id,
        "source_text_sha256": source_sha256,
        "cut_method": strategy,
    }


def _clean_heading(line: str) -> str:
    heading = line.strip().lstrip("#").strip()
    return heading or "Document"
