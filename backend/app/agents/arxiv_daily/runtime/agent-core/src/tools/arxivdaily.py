"""Fetch the newest papers of one arXiv CS category from the official arXiv API.

The previous source (arxivdaily.com) moved its abstracts behind a login-only
API and put category filtering behind an anti-automation check, so its public
pages no longer carry the data this agent needs. arXiv's own API is meant for
programmatic use and returns title, authors, abstract and categories in one
request. Chinese titles and summaries are produced afterwards by the backend.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

import httpx

from config.constants import ARXIV_API_URL, MAX_RESULTS, USER_AGENT
from src.schemas.paper import Category, Paper

CS_CATEGORIES = (
    ("cs.AI", "人工智能"), ("cs.AR", "硬件架构"), ("cs.CC", "计算复杂性"),
    ("cs.CE", "计算工程、金融与科学"), ("cs.CG", "计算几何"), ("cs.CL", "计算与语言"),
    ("cs.CR", "密码学与安全"), ("cs.CV", "计算机视觉与模式识别"), ("cs.CY", "计算机与社会"),
    ("cs.DB", "数据库"), ("cs.DC", "分布式、并行与集群计算"), ("cs.DL", "数字图书馆"),
    ("cs.DM", "离散数学"), ("cs.DS", "数据结构与算法"), ("cs.ET", "新兴技术"),
    ("cs.FL", "形式语言与自动机"), ("cs.GL", "综合文献"), ("cs.GR", "图形学"),
    ("cs.GT", "计算机科学与博弈论"), ("cs.HC", "人机交互"), ("cs.IR", "信息检索"),
    ("cs.IT", "信息论"), ("cs.LG", "机器学习"), ("cs.LO", "计算机逻辑"),
    ("cs.MA", "多智能体系统"), ("cs.MM", "多媒体"), ("cs.MS", "数学软件"),
    ("cs.NA", "数值分析"), ("cs.NE", "神经与进化计算"), ("cs.NI", "网络与互联网架构"),
    ("cs.OH", "其他计算机科学"), ("cs.OS", "操作系统"), ("cs.PF", "性能分析"),
    ("cs.PL", "编程语言"), ("cs.RO", "机器人学"), ("cs.SC", "符号计算"),
    ("cs.SD", "声音技术"), ("cs.SE", "软件工程"), ("cs.SI", "社会与信息网络"),
    ("cs.SY", "系统与控制"),
)
ATOM = dict(atom="http://www.w3.org/2005/Atom", arxiv="http://arxiv.org/schemas/atom")


class ArxivDailyScraper:
    """Thin client for the arXiv listing of one category."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self.client.close()

    def fetch_categories(self) -> list[Category]:
        # The category catalogue is fixed, so no request is needed for it.
        return [Category(code, name) for code, name in CS_CATEGORIES]

    def fetch_papers(self, category: str) -> list[Paper]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.get(
                    ARXIV_API_URL,
                    params=dict(
                        search_query=f"cat:{category}",
                        start="0",
                        max_results=str(MAX_RESULTS),
                        sortBy="submittedDate",
                        sortOrder="descending",
                    ),
                )
                if response.status_code in (429, 503):
                    raise httpx.HTTPStatusError("arXiv is rate limiting", request=response.request, response=response)
                response.raise_for_status()
                papers = self._parse_feed(response.content, category)
                if papers:
                    return papers
                last_error = RuntimeError(f"未获取到 {category} 的论文数据")
            except (httpx.HTTPError, ET.ParseError) as exc:
                last_error = exc
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"arXiv 接口暂时不可用：{last_error}")

    @staticmethod
    def _parse_feed(content: bytes, category: str) -> list[Paper]:
        root = ET.fromstring(content)
        papers: list[Paper] = []
        for entry in root.findall("atom:entry", ATOM):
            identifier = _text(entry.find("atom:id", ATOM)).rsplit("/", 1)[-1]
            identifier = re.sub(r"v\d+$", "", identifier)
            title = _clean_text(_text(entry.find("atom:title", ATOM)))
            abstract = _clean_text(_text(entry.find("atom:summary", ATOM)))
            if not identifier or not title:
                continue
            authors = ", ".join(
                _clean_text(_text(author.find("atom:name", ATOM)))
                for author in entry.findall("atom:author", ATOM)
                if _text(author.find("atom:name", ATOM))
            )
            affiliations: list[str] = []
            for node in entry.findall("atom:author/arxiv:affiliation", ATOM):
                value = _clean_text(_text(node))
                if value and value not in affiliations:
                    affiliations.append(value)
            categories = [
                str(item.get("term"))
                for item in entry.findall("atom:category", ATOM)
                if item.get("term")
            ] or [category]
            pdf_url = next(
                (
                    str(link.get("href"))
                    for link in entry.findall("atom:link", ATOM)
                    if link.get("title") == "pdf" and link.get("href")
                ),
                f"https://arxiv.org/pdf/{identifier}",
            )
            published = _text(entry.find("atom:published", ATOM))[:10]
            updated = _text(entry.find("atom:updated", ATOM))[:10]
            papers.append(
                Paper(
                    arxiv_id=identifier,
                    title=title,
                    title_cn="",
                    summary_cn="",
                    pdf_url=pdf_url.replace("http://", "https://"),
                    authors=authors,
                    affiliations=affiliations,
                    abstract_cn="",
                    abstract=abstract,
                    categories=categories,
                    updated=published or updated,
                    submission_label="修订" if updated and published and updated != published else "新提交",
                )
            )
        return papers


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext())


def _clean_text(value: str) -> str:
    return " ".join(value.split())
