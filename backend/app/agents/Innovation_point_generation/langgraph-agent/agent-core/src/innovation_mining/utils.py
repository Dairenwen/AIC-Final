from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable

from .models import Document


TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+._/-]{1,}|[\u4e00-\u9fff]{2,}")

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "using",
    "based",
    "large",
    "model",
    "models",
    "paper",
    "task",
    "tasks",
    "method",
    "methods",
    "approach",
    "system",
    "systems",
    "data",
    "dataset",
    "benchmark",
    "framework",
    "learning",
    "deep",
    "neural",
}

DOMAIN_ALIASES = {
    "多模态": ["multimodal", "multi-modal", "vision-language", "cross-modal"],
    "大模型": ["large language model", "llm", "foundation model", "large models"],
    "语言模型": ["language modeling", "llm", "large language model"],
    "智能体": ["agent", "agents", "multi-agent", "autonomous agent"],
    "安全": ["safety", "security", "robustness", "adversarial", "risk"],
    "检测": ["detection", "evaluation", "benchmark", "assessment"],
    "鲁棒": ["robustness", "robust", "adversarial"],
    "小样本": ["few-shot", "low-resource", "data-efficient"],
    "深度伪造": ["deepfake", "forensics", "fake detection"],
    "自动驾驶": ["autonomous driving", "driving", "vehicle"],
    "知识图谱": ["knowledge graph", "graph", "citation network"],
    "检索": ["retrieval", "rag", "search"],
    "文档": ["document understanding", "document", "ocr"],
}


def tokenize(text: str) -> list[str]:
    tokens = []
    for match in TOKEN_RE.findall(text or ""):
        token = match.lower() if match.isascii() else match
        if token and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def unique_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def expand_keywords(domain: str, keywords: Iterable[str] | None = None) -> list[str]:
    expanded: list[str] = []
    expanded.extend(tokenize(domain))
    expanded.extend(str(item).strip() for item in (keywords or []) if str(item).strip())
    for cn_term, aliases in DOMAIN_ALIASES.items():
        if cn_term in (domain or "") or any(cn_term in str(item) for item in (keywords or [])):
            expanded.append(cn_term)
            expanded.extend(aliases)
    return unique_keep_order(expanded)


def document_text(doc: Document) -> str:
    return " ".join(
        [
            doc.title,
            doc.abstract,
            doc.publish_venue,
            doc.research_area,
            " ".join(doc.key_words),
            doc.authors,
        ]
    )


def score_document(doc: Document, terms: Iterable[str]) -> tuple[float, list[str]]:
    fields = [
        (doc.title, 3.5),
        (" ".join(doc.key_words), 2.8),
        (doc.research_area, 2.0),
        (doc.publish_venue, 0.8),
        (doc.abstract, 1.0),
        (doc.authors, 0.4),
    ]
    score = 0.0
    hits: list[str] = []
    for term in terms:
        needle = str(term).lower()
        if not needle:
            continue
        matched = False
        for text, weight in fields:
            haystack = str(text or "").lower()
            if needle in haystack:
                score += weight + min(len(needle), 18) / 30.0
                matched = True
        if matched:
            hits.append(str(term))
    if doc.publish_year:
        score += max(0, doc.publish_year - 2018) * 0.06
    return score, unique_keep_order(hits)


def parse_time_range(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    years = [int(item) for item in re.findall(r"20\d{2}|19\d{2}", value)]
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], years[0]
    return min(years), max(years)


def year_in_range(year: int | None, start: int | None, end: int | None) -> bool:
    if year is None:
        return True
    if start is not None and year < start:
        return False
    if end is not None and year > end:
        return False
    return True


def extract_keyphrases(docs: Iterable[Document], extra_terms: Iterable[str] | None = None, limit: int = 24) -> list[str]:
    counter: Counter[str] = Counter()
    for term in extra_terms or []:
        clean = str(term).strip()
        if clean and clean.lower() not in STOPWORDS:
            counter[clean] += 5
    for doc in docs:
        for keyword in doc.key_words:
            clean = str(keyword).strip()
            if clean and clean.lower() not in STOPWORDS:
                counter[clean] += 4
        for token in tokenize(doc.title):
            counter[token] += 2
        for token in tokenize(doc.abstract[:900]):
            counter[token] += 1
    return [term for term, _ in counter.most_common(limit)]


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if math.isnan(value):
        return low
    return max(low, min(high, value))


def split_list(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，;；]+", value)
    else:
        parts = list(value)
    return unique_keep_order(str(item).strip() for item in parts if str(item).strip())


def stable_id(prefix: str, text: str, index: int) -> str:
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:6].upper()
    return f"{prefix}-{index:03d}-{digest}"


def slugify(value: str, fallback: str = "innovation") -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    if ascii_part:
        return ascii_part[:80]
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{fallback}-{digest}"
