from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from schemas.models import DocumentIR, PaperRecord

from .models import MetadataProvenance


ARXIV_PATTERN = re.compile(
    r"\barXiv\s*:\s*(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)
YEAR_WITH_CONTEXT_PATTERN = re.compile(
    r"(?:arXiv|published|accepted|proceedings|conference|journal|copyright|©)"
    r".{0,80}?\b(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
ABSTRACT_PATTERN = re.compile(r"^(?:abstract|摘要)\b", re.IGNORECASE)
NON_METADATA_PATTERN = re.compile(
    r"(?:@|https?://|www\.|university|institute|laboratory|department|"
    r"school of|corresponding author|copyright|all rights reserved)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MetadataRecoveryResult:
    paper: PaperRecord
    provenance: tuple[MetadataProvenance, ...]


def _normalized_lines(document_ir: DocumentIR) -> list[tuple[str, str]]:
    first_page = min(page.page_number for page in document_ir.pages)
    lines: list[tuple[str, str]] = []
    for block in document_ir.text_blocks:
        if block.page_number != first_page:
            continue
        for raw in block.text.splitlines():
            value = unicodedata.normalize("NFKC", raw).strip()
            value = re.sub(r"\s+", " ", value)
            if value:
                lines.append((value, block.object_id))
    return lines


def _looks_like_title(line: str) -> bool:
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", line)
    return (
        4 <= len(words) <= 40
        and 15 <= len(line) <= 300
        and not ABSTRACT_PATTERN.match(line)
        and NON_METADATA_PATTERN.search(line) is None
        and not line.endswith((".", "。", ";", "；"))
    )


def _looks_like_author_line(line: str) -> bool:
    return (
        ("," in line or re.search(r"\s+(?:and|&)\s+", line, re.IGNORECASE) is not None)
        and len(line.split()) <= 40
    )


def _title_candidate(lines: list[tuple[str, str]]) -> tuple[str, str] | None:
    for index, (line, object_id) in enumerate(lines[:12]):
        if not _looks_like_title(line):
            continue
        title_lines = [line]
        for next_line, next_object_id in lines[index + 1 : index + 3]:
            if (
                next_object_id != object_id
                or not _looks_like_title(next_line)
                or _looks_like_author_line(next_line)
            ):
                break
            title_lines.append(next_line)
        return " ".join(title_lines)[:1000], object_id
    return None


def _authors_candidate(
    lines: list[tuple[str, str]],
    *,
    title: str | None,
) -> tuple[list[str], str] | None:
    start = 0
    if title is not None:
        title_prefix = title.split(" ", 1)[0].casefold()
        start = next(
            (index + 1 for index, (line, _) in enumerate(lines) if line.casefold().startswith(title_prefix)),
            0,
        )
    for line, object_id in lines[start : start + 6]:
        if NON_METADATA_PATTERN.search(line) or ABSTRACT_PATTERN.match(line):
            continue
        if "," not in line and re.search(r"\s+(?:and|&)\s+", line, re.IGNORECASE) is None:
            continue
        raw_names = re.split(r"\s*(?:,|;|\band\b|&)\s*", line, flags=re.IGNORECASE)
        names = [
            re.sub(r"[*†‡\d]+$", "", name).strip()
            for name in raw_names
            if name.strip()
        ]
        if not 2 <= len(names) <= 30:
            continue
        if all(
            2 <= len(name) <= 100
            and len(name.split()) <= 8
            and not re.search(r"\b(?:university|institute|laboratory|department)\b", name, re.IGNORECASE)
            for name in names
        ):
            return names, object_id
    return None


def recover_first_page_metadata(
    paper: PaperRecord,
    document_ir: DocumentIR,
    *,
    recover_title: bool,
    recover_authors: bool,
) -> MetadataRecoveryResult:
    lines = _normalized_lines(document_ir)
    provenance: list[MetadataProvenance] = []
    updates: dict[str, object] = {}

    title_candidate = _title_candidate(lines) if recover_title else None
    if title_candidate is not None:
        title, object_id = title_candidate
        updates["title"] = title
        provenance.append(
            MetadataProvenance(
                field="title",
                source="FIRST_PAGE_TEXT",
                confidence="HIGH",
                evidence_object_id=object_id,
            )
        )

    authors_candidate = _authors_candidate(
        lines,
        title=title_candidate[0] if title_candidate else None,
    ) if recover_authors else None
    if authors_candidate is not None:
        authors, object_id = authors_candidate
        updates["authors"] = authors
        provenance.append(
            MetadataProvenance(
                field="authors",
                source="FIRST_PAGE_TEXT",
                confidence="MEDIUM",
                evidence_object_id=object_id,
            )
        )

    front_matter_text = "\n".join(line for line, _ in lines[:30])
    if paper.year is None:
        match = YEAR_WITH_CONTEXT_PATTERN.search(front_matter_text)
        if match:
            updates["year"] = int(match.group("year"))
            provenance.append(
                MetadataProvenance(
                    field="year",
                    source="FIRST_PAGE_TEXT",
                    confidence="HIGH",
                    evidence_object_id=lines[0][1] if lines else None,
                )
            )
    if paper.arxiv_id is None:
        match = ARXIV_PATTERN.search(front_matter_text)
        if match:
            updates["arxiv_id"] = match.group("id")
            provenance.append(
                MetadataProvenance(
                    field="arxiv_id",
                    source="FIRST_PAGE_TEXT",
                    confidence="HIGH",
                    evidence_object_id=lines[0][1] if lines else None,
                )
            )

    return MetadataRecoveryResult(
        paper=paper.model_copy(update=updates),
        provenance=tuple(provenance),
    )
