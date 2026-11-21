from __future__ import annotations

from ..splitter_contract import RawSplitChunk
from .spans import section_spans
from .structural import split_structural


def split_paragraph_sentence(
    text: str, *, target_chars: int, max_chars: int, overlap_target_chars: int
) -> list[RawSplitChunk]:
    return split_structural(
        text,
        section_spans(text),
        target_chars=target_chars,
        max_chars=max_chars,
        overlap_target_chars=overlap_target_chars,
        parent_ids=False,
    )
