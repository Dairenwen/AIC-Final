"""文献检索工具 —— 基于 Semantic Scholar 和 arXiv API"""

from typing import List, Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """文献检索结果"""
    title: str
    authors: List[str]
    year: int
    abstract: str
    url: str
    citation_count: int = 0
    venue: str = ""


@tool
def search_semantic_scholar(
    query: str,
    limit: int = 10,
    year_from: Optional[int] = None,
    fields_of_study: Optional[List[str]] = None,
) -> List[dict]:
    """
    通过 Semantic Scholar API 检索学术文献。

    Args:
        query: 检索关键词
        limit: 返回结果数量上限
        year_from: 最早年份筛选
        fields_of_study: 研究领域筛选

    Returns:
        文献列表（标题、作者、摘要、引用数等）
    """
    import requests

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,authors,year,abstract,url,citationCount,venue",
    }
    if year_from:
        params["year"] = f"{year_from}-"
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = []
        for paper in data.get("data", []):
            results.append({
                "title": paper.get("title", ""),
                "authors": [a.get("name", "") for a in paper.get("authors", [])],
                "year": paper.get("year", 0),
                "abstract": paper.get("abstract", ""),
                "url": paper.get("url", ""),
                "citation_count": paper.get("citationCount", 0),
                "venue": paper.get("venue", ""),
            })
        return results

    except Exception as e:
        return [{"error": f"检索失败: {str(e)}"}]


@tool
def search_arxiv(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
) -> List[dict]:
    """
    通过 arXiv API 检索论文预印本。

    Args:
        query: 检索关键词
        max_results: 最大返回数量
        sort_by: 排序方式 (relevance / lastUpdatedDate / submittedDate)

    Returns:
        论文列表
    """
    import arxiv

    sort_criterion = {
        "relevance": arxiv.SortCriterion.Relevance,
        "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
        "submittedDate": arxiv.SortCriterion.SubmittedDate,
    }.get(sort_by, arxiv.SortCriterion.Relevance)

    try:
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=sort_criterion,
        )

        results = []
        for paper in search.results():
            results.append({
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "year": paper.published.year,
                "abstract": paper.summary,
                "url": paper.entry_id,
                "categories": paper.categories,
            })
        return results

    except Exception as e:
        return [{"error": f"arXiv检索失败: {str(e)}"}]


# 聚合工具列表
LiteratureSearchTool = [search_semantic_scholar, search_arxiv]
