"""ArXiv 文献检索服务 —— 轻量版，直接调用 arXiv API

设计要点：
- arXiv 是英文索引，中文短语无法命中。因此对外提供基于「英文关键词列表」的检索入口，
  由上层（LLM）先把用户诉求转成英文学术术语再传入。
- 采用「精确 AND → 宽松 OR → 原始兜底」三级降级策略，尽量避免空结果。
"""

import re
import html
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
from urllib.error import HTTPError


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_API_URL = "http://export.arxiv.org/api/query"
MAX_RETRIES = 2
TIMEOUT = 15

# 常见英文停用词 + 论文检索无意义的口语词，构建检索式时剔除
_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "with",
    "about", "please", "help", "write", "writing", "me", "my", "paper",
    "papers", "give", "make", "how", "what", "is", "are", "this", "that",
}


def search_arxiv(
    query: str = "",
    max_results: int = 5,
    sort_by: str = "relevance",
    terms: Optional[List[str]] = None,
) -> List[Dict]:
    """检索 arXiv 论文（多级降级，尽量避免空结果）。

    Args:
        query: 原始检索文本（可为中文，仅作兜底用）
        max_results: 最大返回数量
        sort_by: 排序方式 (relevance / lastUpdatedDate / submittedDate)
        terms: 英文关键词列表（优先使用，推荐由上层 LLM 提供）

    Returns:
        论文列表 [{"title", "authors", "year", "abstract", "url"}]
    """
    candidate_queries: List[str] = []

    # 1) 优先使用英文关键词列表
    clean_terms = _clean_terms(terms or [])
    if not clean_terms:
        # 从原始 query 里抽取英文词作为关键词
        clean_terms = _clean_terms(_extract_ascii_terms(query))

    if clean_terms:
        # 精确：前 3 个关键词 AND
        candidate_queries.append(_build_from_terms(clean_terms[:3], joiner=" AND "))
        # 宽松：前 5 个关键词 OR
        candidate_queries.append(_build_from_terms(clean_terms[:5], joiner=" OR "))

    # 2) 原始文本兜底（剔除中文/标点后）
    fallback = _build_query(query)
    if fallback:
        candidate_queries.append(fallback)

    # 逐级尝试，命中即返回
    tried = set()
    for search_query in candidate_queries:
        if not search_query or search_query in tried:
            continue
        tried.add(search_query)
        papers = _run_search(search_query, max_results, sort_by)
        if papers:
            return papers

    return []


def _run_search(search_query: str, max_results: int, sort_by: str) -> List[Dict]:
    """执行一次 arXiv 检索请求。"""
    params = urllib.parse.urlencode({
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": "descending",
    })

    url = f"{ARXIV_API_URL}?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml",
            "User-Agent": "writing-agent/0.1",
        },
    )

    root = _fetch_with_retry(request)
    if root is None:
        return []

    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        paper = _parse_entry(entry)
        if paper:
            papers.append(paper)

    return papers


def _clean_terms(terms: List[str]) -> List[str]:
    """规整关键词：去重、去空、剔除纯停用词，保留短语。"""
    seen = set()
    result = []
    for raw in terms:
        if not raw:
            continue
        term = re.sub(r"\s+", " ", str(raw)).strip().strip('"').strip()
        # 仅保留含英文字母的关键词（arXiv 英文索引）
        if not re.search(r"[A-Za-z]", term):
            continue
        # 单个词若为停用词则跳过
        if " " not in term and term.lower() in _STOPWORDS:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(term)
    return result


def _extract_ascii_terms(query: str) -> List[str]:
    """从原始文本中抽取英文词（用于无关键词时的兜底）。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", query or "")
    return [w for w in words if w.lower() not in _STOPWORDS]


def _build_from_terms(terms: List[str], joiner: str) -> str:
    """由关键词列表构建检索式；多词关键词加引号作短语匹配。"""
    parts = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        if " " in term:
            parts.append(f'all:"{term}"')
        else:
            parts.append(f"all:{term}")
    return joiner.join(parts)


def _build_query(query: str) -> str:
    """原始文本兜底检索式（剔除中文与标点）。"""
    if not query:
        return ""
    # 已含字段前缀则直接使用
    if re.search(r"\b(?:ti|au|abs|all|cat):", query, flags=re.I):
        return query

    ascii_terms = _extract_ascii_terms(query)
    if ascii_terms:
        return _build_from_terms(ascii_terms[:5], joiner=" OR ")
    return ""


def _fetch_with_retry(request: urllib.request.Request) -> Optional[ET.Element]:
    """带重试的请求"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return ET.fromstring(response.read())
        except HTTPError as exc:
            if exc.code in {429, 503} and attempt < MAX_RETRIES:
                time.sleep(3)
                continue
            return None
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(2)
                continue
            return None
    return None


def _parse_entry(entry: ET.Element) -> Optional[Dict]:
    """解析单个 arXiv entry"""
    try:
        title = _element_text(entry, "atom:title")
        abstract = _element_text(entry, "atom:summary")
        published = _element_text(entry, "atom:published")
        entry_url = _element_text(entry, "atom:id")

        authors = []
        for author_el in entry.findall("atom:author", ATOM_NS):
            name = _element_text(author_el, "atom:name")
            if name:
                authors.append(name)

        year_match = re.match(r"(\d{4})", published)
        year = int(year_match.group(1)) if year_match else None

        return {
            "title": _normalize(title),
            "authors": authors,
            "year": year,
            "abstract": html.unescape(_normalize(abstract))[:300],
            "url": entry_url,
        }
    except Exception:
        return None


def _element_text(element: ET.Element, path: str) -> str:
    found = element.find(path, ATOM_NS)
    return found.text.strip() if found is not None and found.text else ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
