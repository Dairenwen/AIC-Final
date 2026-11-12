from __future__ import annotations

from dataclasses import dataclass


class SplitterGatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RawSplitChunk:
    text: str
    source_start: int
    source_end: int
    section_name: str | None
    parent_source_id: str | None = None
    source_span_ambiguous: bool = False
