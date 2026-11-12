from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from config.constants import DEFAULT_MAX_RESULTS, DEFAULT_QUERY_COUNT, DEFAULT_YEAR_WINDOW
from src.schemas import (
    AcademicPaper,
    ConversationContext,
    LiteratureReport,
    LiteratureRetriever,
    LiteratureSearchRequest,
    PaperSource,
    QueryPlan,
    QueryPlanDraft,
    RetrievalBatch,
    RetrievalError,
)
from src.tools import AnnualPublicationFishboneTool, LiteratureListTool

from .ranking import rank_papers
from .state import LiteratureAgentState


PROMPT_DIR = Path(__file__).resolve().parents[2] / "assets" / "prompts"


class LiteratureNodes:
    def __init__(
        self,
        chat_model: Any,
        *,
        local_retriever: LiteratureRetriever | None,
        personal_retriever: LiteratureRetriever | None,
        arxiv_tool: Any,
        scholar_tool: Any,
        top_n: int,
        current_year: int | None = None,
        output_path: str = "output/annual_publication_fishbone.png",
        output_title: str = "年度文献发表脉络",
        allow_report_fallback: bool = False,
    ) -> None:
        self.chat_model = chat_model
        self.retrievers: dict[PaperSource, Any | None] = {
            "local_knowledge": local_retriever,
            "personal_knowledge": personal_retriever,
            "google_scholar": scholar_tool,
            "arxiv": arxiv_tool,
        }
        self.top_n = top_n
        self.current_year = current_year or datetime.now().year
        self.output_path = output_path
        self.output_title = output_title
        self.list_tool = LiteratureListTool()
        self.fishbone_tool = AnnualPublicationFishboneTool()
        self.allow_report_fallback = allow_report_fallback

    def rewrite_query(self, state: LiteratureAgentState) -> LiteratureAgentState:
        user_text = state.get("user_text", "").strip()
        if not user_text:
            raise ValueError("user_text cannot be blank")
        system_prompt = read_prompt("literature_query_rewrite.txt").format(current_year=self.current_year)
        history_context = format_history_context(state.get("recent_turns", []))
        if history_context:
            system_prompt = f"{system_prompt}\n\n同一会话的最近检索上下文：\n{history_context}"
        try:
            structured_model = self.chat_model.with_structured_output(QueryPlanDraft)
            response = structured_model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
            )
            draft = response if isinstance(response, QueryPlanDraft) else QueryPlanDraft.model_validate(response)
        except Exception:
            # Query planning must never prevent the independent source searches
            # from starting.  When the optional LLM is unavailable, retain the
            # user's query and construct the same three-query search plan locally.
            if not self.allow_report_fallback:
                raise
            draft = fallback_bilingual_query_plan(user_text, self.current_year)
        return {"query_plan": normalize_query_plan(user_text, draft, self.current_year)}

    def retrieve_local(self, state: LiteratureAgentState) -> LiteratureAgentState:
        return self._retrieve_source("local_knowledge", state)

    def retrieve_personal(self, state: LiteratureAgentState) -> LiteratureAgentState:
        return self._retrieve_source("personal_knowledge", state)

    def retrieve_scholar(self, state: LiteratureAgentState) -> LiteratureAgentState:
        return self._retrieve_source("google_scholar", state)

    def retrieve_arxiv(self, state: LiteratureAgentState) -> LiteratureAgentState:
        return self._retrieve_source("arxiv", state)

    def _retrieve_source(self, source: PaperSource, state: LiteratureAgentState) -> LiteratureAgentState:
        retriever = self.retrievers[source]
        if retriever is None:
            label = "本地知识库" if source == "local_knowledge" else "个人知识库"
            return {"warnings": [f"{label}未配置，已跳过该检索源"]}
        plan = state["query_plan"]
        minimum_interval = float(getattr(retriever, "minimum_interval_seconds", 0.0))
        if minimum_interval > 0:
            return self._retrieve_rate_limited(source, retriever, plan, minimum_interval)
        indexed_results: list[tuple[int, RetrievalBatch]] = []
        errors: list[RetrievalError] = []
        with ThreadPoolExecutor(max_workers=DEFAULT_QUERY_COUNT) as executor:
            futures = {
                executor.submit(self._invoke_retriever, source, retriever, query, plan): (index, query)
                for index, query in enumerate(plan.queries)
            }
            for future in as_completed(futures):
                index, query = futures[future]
                try:
                    indexed_results.append((index, future.result()))
                except Exception as exc:  # noqa: BLE001
                    errors.append(RetrievalError(source=source, query=query, message=str(exc)))
        indexed_results.sort(key=lambda item: item[0])
        return {
            retrieval_state_key(source): [batch for _, batch in indexed_results],
            "errors": errors,
        }

    def _retrieve_rate_limited(
        self,
        source: PaperSource,
        retriever: Any,
        plan: QueryPlan,
        minimum_interval: float,
    ) -> LiteratureAgentState:
        batches: list[RetrievalBatch] = []
        errors: list[RetrievalError] = []
        for index, query in enumerate(plan.queries):
            if index:
                time.sleep(minimum_interval)
            try:
                batches.append(self._invoke_retriever(source, retriever, query, plan))
            except Exception as exc:  # noqa: BLE001
                errors.append(RetrievalError(source=source, query=query, message=str(exc)))
        return {retrieval_state_key(source): batches, "errors": errors}

    def _invoke_retriever(
        self,
        source: PaperSource,
        retriever: Any,
        query: str,
        plan: QueryPlan,
    ) -> RetrievalBatch:
        request = LiteratureSearchRequest(
            query=query,
            start_year=plan.start_year,
            end_year=plan.end_year,
            max_results=DEFAULT_MAX_RESULTS,
        )
        payload = request.model_dump()
        response = retriever.invoke(payload)
        raw_papers = response.get("papers", []) if isinstance(response, dict) else []
        papers: list[AcademicPaper] = []
        for item in raw_papers:
            data = item.model_dump() if isinstance(item, AcademicPaper) else dict(item)
            if not matches_search_query(data, query):
                continue
            data["source"] = source
            existing_sources = data.get("sources") or []
            data["sources"] = list(dict.fromkeys([source, *existing_sources]))
            papers.append(AcademicPaper.model_validate(data))
        return RetrievalBatch(source=source, query=query, papers=papers)

    def aggregate_and_rank(self, state: LiteratureAgentState) -> LiteratureAgentState:
        batches: list[RetrievalBatch] = []
        for source in ("local_knowledge", "personal_knowledge", "google_scholar", "arxiv"):
            batches.extend(state.get(retrieval_state_key(source), []))
        all_ranked = [paper for paper in rank_papers(batches) if is_displayable_paper(paper)]
        return {
            "retrieval_batches": batches,
            "all_ranked_papers": all_ranked,
            "ranked_papers": all_ranked[: self.top_n],
        }

    def generate_report(self, state: LiteratureAgentState) -> LiteratureAgentState:
        selected = state.get("ranked_papers", [])[: self.top_n]
        if not selected:
            report = LiteratureReport(
                paper_count=0,
                selected_paper_ids=[],
                markdown="# 文献检索报告\n\n在指定时间范围和数据源中未检索到可用于生成报告的文献。",
            )
            return {"report": report}
        plan = state["query_plan"]
        paper_payload = [report_paper_payload(index, paper) for index, paper in enumerate(selected, start=1)]
        human_prompt = (
            f"用户原始需求：{state['user_text']}\n"
            f"检索意图：{plan.intent_summary}\n"
            f"关键词：{', '.join(plan.keywords)}\n"
            f"时间范围：{plan.start_year}-{plan.end_year}\n\n"
            f"候选文献 JSON：\n{json.dumps(paper_payload, ensure_ascii=False, indent=2)}"
        )
        try:
            response = self.chat_model.invoke(
                [
                    SystemMessage(content=read_prompt("literature_report.txt")),
                    HumanMessage(content=human_prompt),
                ]
            )
            markdown = message_text(response)
        except Exception as exc:  # noqa: BLE001
            if not self.allow_report_fallback:
                raise
            markdown = fallback_report(selected, exc)
        validate_report_references(markdown, len(selected))
        return {
            "report": LiteratureReport(
                paper_count=len(selected),
                selected_paper_ids=[paper.id for paper in selected],
                markdown=markdown,
            )
        }

    def format_literature_list(self, state: LiteratureAgentState) -> LiteratureAgentState:
        papers = [paper.model_dump(mode="json") for paper in state.get("all_ranked_papers", [])]
        result = self.list_tool.invoke({"papers": papers})
        return {"literature_list": result["literature_list"], "list_total": result["total"]}

    def generate_annual_fishbone(self, state: LiteratureAgentState) -> LiteratureAgentState:
        writer = get_stream_writer()
        final_event: dict[str, Any] = {}
        for event in self.fishbone_tool.stream(
            {
                "literature_list": state.get("literature_list", []),
                "output_path": state.get("output_path", self.output_path),
                "title": state.get("output_title", self.output_title),
                "stream_delay_seconds": state.get("stream_delay_seconds", 0.0),
            }
        ):
            writer(event)
            final_event = event
        return {"fishbone_result": final_event}


def normalize_query_plan(user_text: str, draft: QueryPlanDraft, current_year: int) -> QueryPlan:
    keywords = unique_nonempty(draft.keywords)[:12]
    if not keywords:
        keywords = [user_text]
    queries = unique_nonempty(draft.queries)
    fallback_queries = semantic_query_variants(user_text, keywords)
    for candidate in fallback_queries:
        if candidate.strip() and candidate.strip().casefold() not in {item.casefold() for item in queries}:
            queries.append(candidate.strip())
        if len(queries) == DEFAULT_QUERY_COUNT:
            break
    while len(queries) < DEFAULT_QUERY_COUNT:
        queries.append(f"{keywords[0]} related methods and applications {len(queries) + 1}")
    start_year, end_year = resolve_year_range(user_text, draft, current_year)
    return QueryPlan(
        intent_summary=draft.intent_summary.strip() or user_text,
        keywords=keywords,
        start_year=start_year,
        end_year=end_year,
        queries=queries[:DEFAULT_QUERY_COUNT],
    )


def extract_fallback_keywords(user_text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", user_text)
    return words[:8] or [user_text]


def fallback_bilingual_query_plan(user_text: str, current_year: int) -> QueryPlanDraft:
    chinese_term, english_term = bilingual_core_terms(user_text)
    semantic_keywords = acronym_semantic_keywords(english_term)
    keywords = unique_nonempty([chinese_term, english_term, *semantic_keywords, *extract_fallback_keywords(user_text)])
    return QueryPlanDraft(
        intent_summary=(
            f"已识别中文研究术语“{chinese_term}”及其英文学术表达“{english_term}”，"
            "将从核心概念、相关方法、应用场景与综述维度进行中英文语义扩展检索。"
        ),
        keywords=keywords,
        start_year=current_year - DEFAULT_YEAR_WINDOW,
        end_year=current_year,
        queries=semantic_query_variants(user_text, keywords),
    )


def semantic_query_variants(user_text: str, keywords: list[str]) -> list[str]:
    primary = keywords[0] if keywords else user_text
    english = next((item for item in keywords if re.search(r"[A-Za-z]", item)), primary)
    standard_name = acronym_semantic_keywords(english)[:1]
    concept_variant = standard_name or ([f"{english} related concepts"] if primary == english else [])
    variants = [
        primary,
        english,
        *concept_variant,
        f"{english} methods techniques",
        f"{english} applications use cases",
        f"{english} survey systematic review",
    ]
    return unique_nonempty(variants)[:DEFAULT_QUERY_COUNT]


def acronym_semantic_keywords(term: str) -> list[str]:
    expansions = {
        "UML": ["unified modeling language", "model-driven engineering", "software modeling"],
        "RAG": ["retrieval-augmented generation", "grounded language model retrieval"],
        "LLM": ["large language model", "foundation language model"],
    }
    return expansions.get(term.strip().upper(), [])


def bilingual_core_terms(user_text: str) -> tuple[str, str]:
    translations = {
        "动态检索增强生成": "dynamic retrieval-augmented generation",
        "检索增强生成": "retrieval-augmented generation",
        "分子间关系学习": "molecular interaction learning",
        "分子关系学习": "molecular relational learning",
        "分子间相互作用": "molecular interaction",
        "量子机器学习": "quantum machine learning",
        "机器学习": "machine learning",
        "人工智能": "artificial intelligence",
        "深度学习": "deep learning",
        "强化学习": "reinforcement learning",
        "大语言模型": "large language model",
        "多模态学习": "multimodal learning",
        "知识图谱": "knowledge graph",
        "图神经网络": "graph neural network",
        "自然语言处理": "natural language processing",
        "计算机视觉": "computer vision",
        "联邦学习": "federated learning",
        "迁移学习": "transfer learning",
    }
    for chinese, english in translations.items():
        if chinese in user_text:
            return chinese, english
    english_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", user_text)
    if english_terms:
        english = " ".join(dict.fromkeys(english_terms))
        return english, english
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,}", user_text)
    chinese = chinese_terms[-1] if chinese_terms else user_text
    return chinese, chinese


def matches_search_query(paper: dict[str, Any], query: str) -> bool:
    terms = search_terms(query)
    if not terms:
        return True
    content = " ".join(
        str(paper.get(field) or "")
        for field in ("title", "abstract", "venue", "categories", "keywords")
    ).casefold()
    return any(term in content for term in terms)


def is_displayable_paper(paper: AcademicPaper) -> bool:
    """Keep only citable, inspectable records in the user-facing result list."""
    if not paper.title.strip() or not paper.authors or len(paper.abstract.strip()) < 40:
        return False
    pdf_url = (paper.pdf_url or "").strip()
    return bool(
        re.match(r"https?://", pdf_url, re.I)
        and re.search(r"(?:arxiv\.org/pdf/|/pdf(?:/|$)|\.pdf(?:[?#]|$)|/article/download/)", pdf_url, re.I)
    )


def search_terms(query: str) -> list[str]:
    ignored_english = {"paper", "papers", "research", "review", "survey", "latest", "recent", "study"}
    ignored_chinese = {"搜索", "索有", "有关", "文献", "论文", "研究", "最新", "近年", "综述", "请帮", "帮我"}
    terms = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]+", query)
        if len(token) > 2 and token.casefold() not in ignored_english
    }
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        terms.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return sorted(term.casefold() for term in terms if term not in ignored_chinese)


def resolve_year_range(user_text: str, draft: QueryPlanDraft, current_year: int) -> tuple[int, int]:
    explicit_range = re.search(
        r"\b((?:19|20)\d{2})\s*(?:年)?\s*(?:至|到|[-—~～])\s*((?:19|20)\d{2})\b",
        user_text,
    )
    if explicit_range:
        years = int(explicit_range.group(1)), int(explicit_range.group(2))
        return min(years), max(years)
    relative = re.search(r"(?:近|过去|最近)\s*(\d{1,2})\s*年", user_text)
    if relative:
        return current_year - int(relative.group(1)), current_year
    if draft.start_year is not None or draft.end_year is not None:
        start = draft.start_year if draft.start_year is not None else draft.end_year
        end = draft.end_year if draft.end_year is not None else draft.start_year
        assert start is not None and end is not None
        return min(start, end), max(start, end)
    return current_year - DEFAULT_YEAR_WINDOW, current_year


def unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def read_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def report_paper_payload(index: int, paper: AcademicPaper) -> dict[str, Any]:
    return {
        "reference_number": index,
        "id": paper.id,
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "year": paper.published_year,
        "venue": paper.venue,
        "citation_count": paper.citation_count,
        "sources": paper.sources,
        "url": paper.url,
    }


def message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [item.get("text", "") if isinstance(item, dict) else str(item) for item in content]
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def validate_report_references(markdown: str, paper_count: int) -> None:
    if not markdown:
        raise ValueError("The report model returned empty content")
    invalid = sorted(
        {
            int(value)
            for value in re.findall(r"\[(\d+)\]", markdown)
            if int(value) < 1 or int(value) > paper_count
        }
    )
    if invalid:
        raise ValueError(f"The report contains unknown reference numbers: {invalid}")


def fallback_report(papers: list[AcademicPaper], error: Exception) -> str:
    lines = [
        "# 文献检索报告",
        "",
        "报告模型暂时不可用，以下内容根据已完成检索和排序的文献元数据生成。",
        f"模型调用信息：{type(error).__name__}。",
        "",
        "## 核心文献",
        "",
    ]
    for index, paper in enumerate(papers, start=1):
        authors = "、".join(paper.authors[:3]) or "作者信息缺失"
        lines.extend(
            [
                f"### [{index}] {paper.title}",
                "",
                f"- 作者：{authors}",
                f"- 年份：{paper.published_year or '未知'}",
                f"- 来源：{paper.venue or '未知'}",
                f"- 检索来源：{'、'.join(paper.sources) or paper.source}",
                "",
            ]
        )
    return "\n".join(lines)


def retrieval_state_key(source: PaperSource) -> str:
    return {
        "local_knowledge": "local_retrieval_batches",
        "personal_knowledge": "personal_retrieval_batches",
        "google_scholar": "scholar_retrieval_batches",
        "arxiv": "arxiv_retrieval_batches",
    }[source]


def format_history_context(turns: list[ConversationContext] | list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for index, turn in enumerate(turns, start=1):
        item = turn if isinstance(turn, ConversationContext) else ConversationContext.model_validate(turn)
        papers = "; ".join(
            f"{paper.get('title', '未命名')} ({paper.get('published_year') or paper.get('year') or '年份未知'})"
            for paper in item.top_papers[:5]
        )
        sections.append(
            f"历史轮次 {index}：输入={item.user_text}\n"
            f"意图={item.intent_summary}；时间={item.start_year}-{item.end_year}\n"
            f"Top 文献={papers or '无'}"
        )
    return "\n\n".join(sections)
