from __future__ import annotations

import re

from ..splitter_contract import RawSplitChunk
from .spans import section_for_offset, section_spans


BOUNDARY_RE = re.compile(r"(\n\n+|(?<=[.!?;:])\s+)")


def _split_units(text: str) -> list[str]:
    parts = BOUNDARY_RE.split(text.strip())
    units: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        current += part
        if BOUNDARY_RE.fullmatch(part):
            if current.strip():
                units.append(current.strip())
            current = ""
    if current.strip():
        units.append(current.strip())
    return units


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks: list[str] = []
    current = ""
    for unit in _split_units(text):
        if len(unit) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(unit):
                end = min(start + chunk_size, len(unit))
                chunks.append(unit[start:end].strip())
                if end >= len(unit):
                    break
                start = end - overlap
            continue
        next_text = f"{current} {unit}".strip() if current else unit
        if len(next_text) <= chunk_size:
            current = next_text
            continue
        if current:
            chunks.append(current.strip())
        tail = current[-overlap:].strip() if overlap and current else ""
        current = f"{tail} {unit}".strip() if tail else unit
    if current:
        chunks.append(current.strip())
    return chunks


def split_fixed_boundary(text: str, *, chunk_size: int, overlap: int) -> list[RawSplitChunk]:
    sections = section_spans(text)
    chunks: list[RawSplitChunk] = []
    lower_bound = 0
    for chunk in _chunk_text(text, chunk_size, overlap):
        exact = text.find(chunk, lower_bound)
        if exact >= 0:
            start, end = exact, exact + len(chunk)
        else:
            tokens = [re.escape(token) for token in re.split(r"\s+", chunk.strip()) if token]
            match = re.compile(r"\s+".join(tokens)).search(text, lower_bound) if tokens else None
            if match is None:
                raise ValueError("Could not map fixed-boundary chunk to source text")
            start, end = match.span()
        first = section_for_offset(sections, start)
        last = section_for_offset(sections, max(start, end - 1))
        section_name = (
            first.heading if first.section_id == last.section_id else f"{first.heading} -> {last.heading}"
        )
        source_text = text[start:end]
        chunks.append(
            RawSplitChunk(
                text=source_text,
                source_start=start,
                source_end=end,
                section_name=None if section_name == "UNKNOWN" else section_name,
                source_span_ambiguous=False,
            )
        )
        lower_bound = start if len(chunk) <= overlap else max(start + 1, end - overlap)
    return chunks
