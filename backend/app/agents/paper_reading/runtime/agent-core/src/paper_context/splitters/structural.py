from __future__ import annotations

from ..splitter_contract import RawSplitChunk
from .spans import SectionSpan, Span, atomic_spans


def _group_units(
    units: list[Span], target_chars: int, max_chars: int, overlap_target_chars: int
) -> list[Span]:
    chunks: list[Span] = []
    index = 0
    while index < len(units):
        start = units[index].start
        stop = index + 1
        while stop < len(units) and units[stop].end - start <= target_chars:
            stop += 1
        if stop < len(units) and units[stop].end - start <= max_chars:
            current_distance = abs(units[stop - 1].end - start - target_chars)
            next_distance = abs(units[stop].end - start - target_chars)
            if next_distance <= current_distance:
                stop += 1
        end = units[stop - 1].end
        chunks.append(Span(start, end))
        if stop >= len(units):
            break
        if units[stop].length + overlap_target_chars > max_chars:
            index = stop
            continue
        overlap_start = stop - 1
        while overlap_start > index and end - units[overlap_start].start < overlap_target_chars:
            overlap_start -= 1
        index = max(index + 1, overlap_start)
    return chunks


def split_structural(
    text: str,
    sections: list[SectionSpan],
    *,
    target_chars: int,
    max_chars: int,
    overlap_target_chars: int,
    parent_ids: bool,
) -> list[RawSplitChunk]:
    chunks: list[RawSplitChunk] = []
    for section in sections:
        units = atomic_spans(text, section, max_chars)
        for span in _group_units(units, target_chars, max_chars, overlap_target_chars):
            chunk_text = text[span.start : span.end]
            if not chunk_text.strip():
                continue
            chunks.append(
                RawSplitChunk(
                    text=chunk_text,
                    source_start=span.start,
                    source_end=span.end,
                    section_name=None if section.heading == "UNKNOWN" else section.heading,
                    parent_source_id=section.section_id if parent_ids else None,
                )
            )
    return chunks
