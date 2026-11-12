from __future__ import annotations

import re

from academic_translation.schemas.models import QualityReport, TermEntry, TranslationSegment
from academic_translation.utils.text import restore_protected_content


def assess_quality(segments: list[TranslationSegment], terms: list[TermEntry], warnings: list[str]) -> QualityReport:
    missing: list[str] = []
    terminology: list[str] = []
    tokens: list[str] = []
    formats: list[str] = []
    strict_constraints = {term.source: term.target for term in terms if term.origin in {"user", "library"}}
    suggested_constraints = {term.source: term.target for term in terms if term.origin == "model"}
    for segment in segments:
        if not segment.translatable:
            if segment.kind == "reference" and segment.translated_text != segment.source_text:
                formats.append(f"{segment.segment_id}: preserved reference was modified")
            continue
        if not segment.translated_text.strip():
            missing.append(segment.segment_id)
            continue
        for token in segment.tokens:
            if token not in segment.translated_text:
                tokens.append(f"{segment.segment_id}: missing {token}")
        for token in set(re.findall(r"\[\[KEEP_\d+\]\]", segment.translated_text)) - set(segment.tokens):
            tokens.append(f"{segment.segment_id}: unexpected {token}")
        visible_source = restore_protected_content(segment.source_text, segment.tokens)
        visible_target = restore_protected_content(segment.translated_text, segment.tokens)
        for source, target in strict_constraints.items():
            if source.lower() in visible_source.lower() and target not in visible_target:
                terminology.append(f"{segment.segment_id}: {source} => {target}")
        for source, target in suggested_constraints.items():
            if source.lower() in visible_source.lower() and target not in visible_target:
                warnings.append(f"{segment.segment_id}: model terminology suggestion varied: {source} => {target}")
    if len({segment.segment_id for segment in segments}) != len(segments):
        formats.append("duplicate segment identifiers")
    return QualityReport(total_segments=len(segments), translated_segments=sum(bool(segment.translated_text.strip()) for segment in segments if segment.translatable), untranslated_segment_ids=missing, terminology_violations=terminology, protected_token_violations=tokens, format_violations=formats, warnings=warnings)
