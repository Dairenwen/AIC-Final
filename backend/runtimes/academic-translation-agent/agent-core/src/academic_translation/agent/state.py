from typing import TypedDict

from academic_translation.schemas.models import QualityReport, TermEntry, TranslationRequest, TranslationSegment


class TranslationState(TypedDict, total=False):
    request: TranslationRequest
    task_id: str
    segments: list[TranslationSegment]
    glossary: list[TermEntry]
    quality: QualityReport
    outputs: dict[str, str]
    warnings: list[str]
