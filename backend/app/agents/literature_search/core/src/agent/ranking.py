from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from src.schemas import AcademicPaper, RetrievalBatch


RRF_K = 60
FUZZY_TITLE_THRESHOLD = 0.95
SOURCE_PRIORITY = {
    "local_knowledge": 0,
    "personal_knowledge": 1,
    "google_scholar": 2,
    "arxiv": 3,
}


def rank_papers(batches: list[RetrievalBatch]) -> list[AcademicPaper]:
    aggregates: list[tuple[AcademicPaper, float]] = []
    for batch in batches:
        for rank, paper in enumerate(batch.papers, start=1):
            contribution = 1.0 / (RRF_K + rank)
            duplicate_index = next(
                (index for index, (candidate, _) in enumerate(aggregates) if papers_match(candidate, paper)),
                None,
            )
            if duplicate_index is None:
                aggregates.append((paper.model_copy(deep=True), contribution))
                continue
            existing, score = aggregates[duplicate_index]
            aggregates[duplicate_index] = (merge_papers(existing, paper), score + contribution)

    ranked: list[AcademicPaper] = []
    for paper, score in aggregates:
        paper.retrieval_score = score
        ranked.append(paper)
    ranked.sort(
        key=lambda paper: (
            -(paper.retrieval_score or 0.0),
            -len(paper.sources),
            -(paper.citation_count or 0),
            -(paper.published_year or 0),
            normalize_title(paper.title),
        )
    )
    return ranked


def papers_match(left: AcademicPaper, right: AcademicPaper) -> bool:
    left_doi, right_doi = normalize_doi(left.doi), normalize_doi(right.doi)
    if left_doi and right_doi and left_doi == right_doi:
        return True
    left_arxiv, right_arxiv = arxiv_id(left), arxiv_id(right)
    if left_arxiv and right_arxiv and left_arxiv == right_arxiv:
        return True
    left_url, right_url = normalize_url(left.url), normalize_url(right.url)
    if left_url and right_url and left_url == right_url:
        return True
    left_title, right_title = normalize_title(left.title), normalize_title(right.title)
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True
    if not years_compatible(left.published_year, right.published_year):
        return False
    if not authors_overlap(left.authors, right.authors):
        return False
    return SequenceMatcher(None, left_title, right_title).ratio() >= FUZZY_TITLE_THRESHOLD


def merge_papers(primary: AcademicPaper, duplicate: AcademicPaper) -> AcademicPaper:
    if SOURCE_PRIORITY[duplicate.source] < SOURCE_PRIORITY[primary.source]:
        primary, duplicate = duplicate, primary
    merged = primary.model_copy(deep=True)
    merged.sources = sorted(
        set([*primary.sources, *duplicate.sources]),
        key=SOURCE_PRIORITY.__getitem__,
    )
    merged.authors = list(dict.fromkeys([*primary.authors, *duplicate.authors]))
    merged.categories = list(dict.fromkeys([*primary.categories, *duplicate.categories]))
    if len(duplicate.abstract) > len(primary.abstract):
        merged.abstract = duplicate.abstract
    for field in ("url", "pdf_url", "published_year", "venue", "doi"):
        if getattr(merged, field) in (None, "") and getattr(duplicate, field) not in (None, ""):
            setattr(merged, field, getattr(duplicate, field))
    if duplicate.citation_count is not None:
        merged.citation_count = max(primary.citation_count or 0, duplicate.citation_count)
    merged.raw = {**duplicate.raw, **primary.raw}
    return merged


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).casefold()


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", ""))


def arxiv_id(paper: AcademicPaper) -> str:
    if paper.id.casefold().startswith("arxiv:"):
        return re.sub(r"v\d+$", "", paper.id.split(":", 1)[1], flags=re.I).casefold()
    for value in (paper.url, paper.pdf_url):
        if value and "arxiv.org" in value.casefold():
            match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", value, flags=re.I)
            if match:
                return re.sub(r"(?:\.pdf)?v\d+$", "", match.group(1), flags=re.I).casefold()
    return ""


def years_compatible(left: int | None, right: int | None) -> bool:
    return left is None or right is None or abs(left - right) <= 1


def authors_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    normalized_left = {normalize_title(author) for author in left}
    normalized_right = {normalize_title(author) for author in right}
    return bool(normalized_left & normalized_right)
