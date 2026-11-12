"""Scrape public server-rendered paper data from arXivDaily."""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup
from langchain_core.runnables import RunnableLambda

from config.constants import SOURCE_URL, USER_AGENT
from src.schemas.paper import Category, Paper


class ArxivDailyScraper:
    """Small, rate-conscious scraper for the source's public HTML."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
        )
        self.normalizer = RunnableLambda(self._to_paper)

    def close(self) -> None:
        self.client.close()

    def fetch_categories(self) -> list[Category]:
        # The source used to accept ``?major=CS``.  It now treats filtered
        # requests as a protected query and returns a login page, while the
        # unfiltered home page still exposes the public category catalogue.
        try:
            soup = self._get_soup({})
        except httpx.HTTPError:
            return self._fallback_categories()
        major_input = soup.select_one('input[name="category"][value="major:CS"]')
        if not major_input:
            return self._fallback_categories()
        group = major_input.find_parent("div", class_="category-picker-group")
        if group is None:
            return self._fallback_categories()
        categories: list[Category] = []
        for item in group.select("label.category-choice-sub"):
            code = item.select_one(".choice-code")
            name = item.select_one(".choice-name")
            if code and name and code.get_text(strip=True) != "cs":
                categories.append(Category(code.get_text(strip=True), name.get_text(strip=True)))
        return categories or self._fallback_categories()

    def fetch_papers(self, category: str) -> list[Paper]:
        """Fetch the newest batch published by arXivDaily for one CS category.

        arXivDaily publishes according to its own release schedule and timezone.  A
        local-calendar date filter can therefore point at an unpublished day and
        produce an empty result even when the source has a complete newest batch.
        Omitting that filter deliberately follows the source site's latest view.
        """
        # Filtered pages may now be protected by the source site.  First try
        # the historical endpoint, then parse the public home page and filter
        # its cards locally.  The latter also avoids a second request when the
        # source changes its query contract again.
        for params in ({"category": f"subcat:{category}"}, {}):
            try:
                soup = self._get_soup(params)
            except httpx.HTTPError:
                continue
            papers = self._parse_cards(soup, category=category)
            if papers:
                return papers

        # arXiv remains the authoritative public source when arXivDaily is
        # unavailable or its anti-abuse page hides the filtered cards.
        return self._fetch_arxiv_api_papers(category)

    def _parse_cards(self, soup: BeautifulSoup, *, category: str) -> list[Paper]:
        papers: list[Paper] = []
        for card in soup.select("article.paper-card"):
            data_node = card.select_one("script[data-paper-share-json]")
            if not data_node or not data_node.get_text(strip=True):
                continue
            try:
                source = json.loads(data_node.get_text())
                source = self._enrich_card_payload(card, source)
                source_categories = {str(item) for item in source.get("categories") or []}
                if category not in source_categories:
                    continue
                papers.append(self.normalizer.invoke(source))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return papers

    @staticmethod
    def _enrich_card_payload(card, source: dict) -> dict:
        """Merge complete details rendered in the source card into its share payload.

        arXivDaily's compact JSON is designed for sharing, so it deliberately
        omits the PDF URL and both abstracts.  They are already server-rendered
        in the same card, which makes parsing them both faster and more reliable
        than issuing a second request per paper.
        """
        payload = dict(source)

        # Current cards keep the title/summary in ordinary markup and the
        # expandable abstract behind an authenticated API.  Preserve the
        # public fields so the card remains useful without that API token.
        title_cn = card.select_one(".title-cn")
        if title_cn and title_cn.get_text(" ", strip=True):
            payload["title_cn"] = title_cn.get_text(" ", strip=True)
        summary = card.select_one(".summary")
        if summary:
            label = summary.select_one(".summary-label")
            if label:
                label.extract()
            text = summary.get_text(" ", strip=True)
            if text:
                payload["summary_cn"] = text

        pdf_link = card.select_one("a.pdf-button[href]")
        if pdf_link and pdf_link.get("href"):
            payload["pdf_url"] = pdf_link["href"].strip()

        chinese_abstract = card.select_one(".detail-abstract-cn p")
        if chinese_abstract:
            payload["abstract_cn"] = chinese_abstract.get_text(" ", strip=True)

        english_abstract = next(
            (
                item.select_one("p").get_text(" ", strip=True)
                for item in card.select(".detail-abstract")
                if "detail-abstract-cn" not in (item.get("class") or []) and item.select_one("p")
            ),
            "",
        )
        if english_abstract:
            payload["abstract"] = english_abstract
        return payload

    def _fetch_arxiv_api_papers(self, category: str) -> list[Paper]:
        response = self.client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"cat:{category}",
                "start": "0",
                "max_results": "30",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        papers: list[Paper] = []
        for entry in root.findall("atom:entry", ns):
            identifier = _text(entry.find("atom:id", ns)).rsplit("/", 1)[-1]
            identifier = re.sub(r"v\d+$", "", identifier)
            title = _clean_text(_text(entry.find("atom:title", ns)))
            abstract = _clean_text(_text(entry.find("atom:summary", ns)))
            if not identifier or not title:
                continue
            authors = ", ".join(
                _clean_text(_text(author.find("atom:name", ns)))
                for author in entry.findall("atom:author", ns)
                if _text(author.find("atom:name", ns))
            )
            categories = [
                str(item.get("term"))
                for item in entry.findall("arxiv:category", ns)
                if item.get("term")
            ] or [category]
            pdf_url = next(
                (
                    str(link.get("href"))
                    for link in entry.findall("atom:link", ns)
                    if link.get("title") == "pdf" and link.get("href")
                ),
                f"https://arxiv.org/pdf/{identifier}",
            )
            updated = _text(entry.find("atom:published", ns))[:10]
            papers.append(
                Paper(
                    arxiv_id=identifier,
                    title=title,
                    title_cn=title,
                    summary_cn="源站暂未提供 AI 总结",
                    pdf_url=pdf_url,
                    authors=authors,
                    affiliations=[],
                    abstract_cn="源站暂未提供 AI 中文摘要",
                    abstract=abstract,
                    categories=categories,
                    updated=updated,
                    submission_label="arXiv API",
                )
            )
        if not papers:
            raise RuntimeError(f"未获取到 {category} 的论文数据")
        return papers

    @staticmethod
    def _fallback_categories() -> list[Category]:
        return [
            Category(code, name)
            for code, name in (
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
        ]

    @staticmethod
    def _to_paper(source: dict) -> Paper:
        affiliations = source.get("affiliations") or []
        if isinstance(affiliations, str):
            affiliations = [affiliations]
        normalized_affiliations: list[str] = []
        for affiliation in affiliations:
            if isinstance(affiliation, dict):
                english = str(affiliation.get("en", "")).strip()
                chinese = str(affiliation.get("zh", "")).strip()
                normalized_affiliations.append(f"{english}（{chinese}）" if english and chinese else english or chinese)
            else:
                normalized_affiliations.append(str(affiliation))
        return Paper(
            arxiv_id=str(source.get("arxiv_id", "")),
            title=str(source.get("title", "")),
            title_cn=str(source.get("title_cn", "暂无中文翻译标题")),
            summary_cn=str(source.get("summary_cn", "暂无 AI 总结")),
            pdf_url=str(source.get("pdf_url", "")),
            authors=str(source.get("authors", "")),
            affiliations=normalized_affiliations,
            abstract_cn=str(source.get("abstract_cn", "暂无 AI 中文摘要")),
            abstract=str(source.get("abstract", "暂无英文摘要")),
            categories=[str(item) for item in source.get("categories", [])],
            updated=str(source.get("updated", "")),
            submission_label=str(source.get("submission_label", "")),
        )

    def _get_soup(self, params: dict[str, str]) -> BeautifulSoup:
        for attempt in range(3):
            response = self.client.get(SOURCE_URL, params=params)
            if response.status_code != 429:
                response.raise_for_status()
                return BeautifulSoup(response.text, "html.parser")
            if attempt == 2:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After", "30")
            try:
                delay = max(1.0, min(float(retry_after), 120.0))
            except ValueError:
                delay = 30.0
            time.sleep(delay)
        raise RuntimeError("unreachable")


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext())


def _clean_text(value: str) -> str:
    return " ".join(value.split())
