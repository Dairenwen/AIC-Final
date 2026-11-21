"""论文证据检索纯逻辑：章节/卡片路由 + 原文片段选取。

来源：backend/app/graphs/paper_evidence.py

路由基于分类主/子类型（诉求优先），不按建议文本关键词硬匹配。
quote 必须来自已确认卡片 source_quote 或 structure_summary.abstract 原文。

说明：不硬依赖 analysis_schemas 中的 Classification* 类型，采用鸭子类型 + dict，
以便 Wave1 并行时不依赖 A3 schemas 落盘顺序。
"""

from __future__ import annotations

import re
from typing import Any

from langgraph_agent.tools.paper_schemas import CardType, SectionType

# 每条 quote 最大字符数；单次检索最多返回条数。
_MAX_QUOTE_CHARS = 600
_MAX_EXCERPTS = 5

# 主类型 → 优先核对的论文章节类型。
_PRIMARY_SECTION_ROUTE: dict[str, tuple[str, ...]] = {
    "RESEARCH_POSITIONING_CONTRIBUTION": (
        SectionType.ABSTRACT.value,
        SectionType.INTRODUCTION.value,
        SectionType.CONCLUSION.value,
    ),
    "RELATED_WORK_CITATION": (
        SectionType.RELATED_WORK.value,
        SectionType.INTRODUCTION.value,
    ),
    "METHOD_THEORY": (SectionType.METHOD.value,),
    "DATA_SAMPLE": (
        SectionType.DATASET.value,
        SectionType.EXPERIMENTS.value,
    ),
    "EXPERIMENT_EVALUATION": (
        SectionType.EXPERIMENTS.value,
        SectionType.RESULTS.value,
        SectionType.ABLATION.value,
    ),
    "RESULTS_DISCUSSION_CONCLUSION": (
        SectionType.RESULTS.value,
        SectionType.DISCUSSION.value,
        SectionType.CONCLUSION.value,
        SectionType.LIMITATIONS.value,
    ),
    "REPRODUCIBILITY_TRANSPARENCY": (
        SectionType.METHOD.value,
        SectionType.EXPERIMENTS.value,
    ),
    "WRITING_CONTENT_PRESENTATION": (
        SectionType.INTRODUCTION.value,
        SectionType.METHOD.value,
        SectionType.RESULTS.value,
        SectionType.ABSTRACT.value,
    ),
    "FORMAT_SUBMISSION_COMPLIANCE": (
        SectionType.OTHER.value,
        SectionType.REFERENCES.value,
        SectionType.ABSTRACT.value,
    ),
    "ETHICS_RESEARCH_INTEGRITY": (
        SectionType.LIMITATIONS.value,
        SectionType.DISCUSSION.value,
        SectionType.ABSTRACT.value,
    ),
}

_DEFAULT_SECTIONS: tuple[str, ...] = (
    SectionType.METHOD.value,
    SectionType.EXPERIMENTS.value,
    SectionType.RESULTS.value,
)

# 主类型 → 优先消费的信息卡片类型。
_PRIMARY_CARD_ROUTE: dict[str, tuple[str, ...]] = {
    "RESEARCH_POSITIONING_CONTRIBUTION": (
        CardType.RESEARCH_QUESTION.value,
        CardType.RESEARCH_MOTIVATION.value,
        CardType.CORE_CONTRIBUTIONS.value,
        CardType.RESEARCH_BOUNDARY.value,
    ),
    "RELATED_WORK_CITATION": (CardType.CORE_CONTRIBUTIONS.value,),
    "METHOD_THEORY": (CardType.MAIN_METHOD.value,),
    "DATA_SAMPLE": (CardType.DATASET_OR_SAMPLE.value,),
    "EXPERIMENT_EVALUATION": (
        CardType.EXPERIMENT_SETUP_BASELINES_METRICS.value,
        CardType.MAIN_RESULTS.value,
        CardType.ABLATION_OR_SUPPLEMENTARY_ANALYSIS.value,
    ),
    "RESULTS_DISCUSSION_CONCLUSION": (
        CardType.MAIN_RESULTS.value,
        CardType.LIMITATIONS.value,
        CardType.RESEARCH_BOUNDARY.value,
    ),
    "REPRODUCIBILITY_TRANSPARENCY": (
        CardType.MAIN_METHOD.value,
        CardType.EXPERIMENT_SETUP_BASELINES_METRICS.value,
    ),
    "WRITING_CONTENT_PRESENTATION": (
        CardType.CORE_CONTRIBUTIONS.value,
        CardType.MAIN_METHOD.value,
    ),
    "FORMAT_SUBMISSION_COMPLIANCE": (),
    "ETHICS_RESEARCH_INTEGRITY": (
        CardType.LIMITATIONS.value,
        CardType.RESEARCH_BOUNDARY.value,
    ),
}

_DEFAULT_CARDS: tuple[str, ...] = (
    CardType.MAIN_METHOD.value,
    CardType.MAIN_RESULTS.value,
    CardType.EXPERIMENT_SETUP_BASELINES_METRICS.value,
)

# 子类型微调：收窄或增补章节，不读建议文本。
_SUBTYPE_SECTION_OVERRIDE: dict[str, tuple[str, ...]] = {
    "ABLATION_STUDY": (
        SectionType.ABLATION.value,
        SectionType.RESULTS.value,
        SectionType.EXPERIMENTS.value,
    ),
    "BASELINE_COMPARISON": (
        SectionType.EXPERIMENTS.value,
        SectionType.RESULTS.value,
    ),
    "EVALUATION_METRIC": (
        SectionType.EXPERIMENTS.value,
        SectionType.RESULTS.value,
    ),
    "LIMITATION": (
        SectionType.LIMITATIONS.value,
        SectionType.DISCUSSION.value,
        SectionType.CONCLUSION.value,
    ),
    "RESEARCH_SCOPE_BOUNDARY": (
        SectionType.LIMITATIONS.value,
        SectionType.DISCUSSION.value,
        SectionType.CONCLUSION.value,
    ),
    "LITERATURE_COVERAGE": (
        SectionType.RELATED_WORK.value,
        SectionType.INTRODUCTION.value,
    ),
    "RECENT_RESEARCH": (
        SectionType.RELATED_WORK.value,
        SectionType.INTRODUCTION.value,
    ),
}

_SUBTYPE_CARD_OVERRIDE: dict[str, tuple[str, ...]] = {
    "ABLATION_STUDY": (
        CardType.ABLATION_OR_SUPPLEMENTARY_ANALYSIS.value,
        CardType.MAIN_RESULTS.value,
    ),
    "BASELINE_COMPARISON": (
        CardType.EXPERIMENT_SETUP_BASELINES_METRICS.value,
        CardType.MAIN_RESULTS.value,
    ),
    "LIMITATION": (CardType.LIMITATIONS.value, CardType.RESEARCH_BOUNDARY.value),
    "RESEARCH_SCOPE_BOUNDARY": (
        CardType.RESEARCH_BOUNDARY.value,
        CardType.LIMITATIONS.value,
    ),
    "RESEARCH_MOTIVATION": (CardType.RESEARCH_MOTIVATION.value,),
    "RESEARCH_QUESTION": (CardType.RESEARCH_QUESTION.value,),
    "CONTRIBUTION_CLAIM": (CardType.CORE_CONTRIBUTIONS.value,),
    "METHOD_CLARITY": (CardType.MAIN_METHOD.value,),
    "METHOD_CORRECTNESS": (CardType.MAIN_METHOD.value,),
}


def _as_str(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value)


def _decision_fields(decision: object) -> dict[str, str]:
    if isinstance(decision, dict):
        return {
            "primary_type": _as_str(decision.get("primary_type")),
            "target_subtype": _as_str(decision.get("target_subtype")),
        }
    return {
        "primary_type": _as_str(getattr(decision, "primary_type", "")),
        "target_subtype": _as_str(getattr(decision, "target_subtype", "")),
    }


def _active_decision(classification: object) -> dict[str, Any]:
    """优先 confirmed_result，否则顶层 primary_type/target_subtype。"""
    if classification is None:
        return {"primary_type": "", "target_subtype": ""}

    # 鸭子类型：ClassificationResult 风格（含 confirmed_result / automatic_result）
    confirmed = getattr(classification, "confirmed_result", None)
    automatic = getattr(classification, "automatic_result", None)
    if confirmed is not None or automatic is not None:
        return _decision_fields(confirmed or automatic)

    # ClassificationDecision 风格：直接挂 primary_type
    if hasattr(classification, "primary_type") and not isinstance(classification, dict):
        return _decision_fields(classification)

    if not isinstance(classification, dict):
        return {"primary_type": "", "target_subtype": ""}

    confirmed_dict = classification.get("confirmed_result")
    if isinstance(confirmed_dict, dict) and confirmed_dict.get("primary_type"):
        return {
            "primary_type": _as_str(confirmed_dict.get("primary_type")),
            "target_subtype": _as_str(confirmed_dict.get("target_subtype")),
        }
    automatic_dict = classification.get("automatic_result")
    if isinstance(automatic_dict, dict) and automatic_dict.get("primary_type"):
        return {
            "primary_type": _as_str(automatic_dict.get("primary_type")),
            "target_subtype": _as_str(automatic_dict.get("target_subtype")),
        }
    return {
        "primary_type": _as_str(classification.get("primary_type")),
        "target_subtype": _as_str(classification.get("target_subtype")),
    }


def build_section_route(classification: object) -> list[str]:
    """按分类主/子类型返回应核对的 SectionType.value 列表。"""
    decision = _active_decision(classification)
    subtype = decision["target_subtype"]
    if subtype in _SUBTYPE_SECTION_OVERRIDE:
        return list(_SUBTYPE_SECTION_OVERRIDE[subtype])
    primary = decision["primary_type"]
    return list(_PRIMARY_SECTION_ROUTE.get(primary, _DEFAULT_SECTIONS))


def build_card_route(classification: object) -> list[str]:
    """按分类主/子类型返回应优先选取的 CardType.value 列表。"""
    decision = _active_decision(classification)
    subtype = decision["target_subtype"]
    if subtype in _SUBTYPE_CARD_OVERRIDE:
        return list(_SUBTYPE_CARD_OVERRIDE[subtype])
    primary = decision["primary_type"]
    return list(_PRIMARY_CARD_ROUTE.get(primary, _DEFAULT_CARDS))


def _truncate_quote(text: str, limit: int = _MAX_QUOTE_CHARS) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    boundary = compact.rfind(". ", 0, limit)
    if boundary < limit // 2:
        boundary = limit
    return compact[: boundary + 1].strip()


def _card_field(card: object, name: str, default: Any = None) -> Any:
    if isinstance(card, dict):
        return card.get(name, default)
    return getattr(card, name, default)


def _heading_to_type(sections: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in sections:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("original_heading") or "").strip()
        ntype = str(item.get("normalized_type") or "").strip()
        if heading and ntype:
            mapping[heading] = ntype
            mapping[heading.lower()] = ntype
    return mapping


def _pages_for_headings(
    sections: list[dict[str, Any]], headings: list[str]
) -> list[int]:
    wanted = {h.strip().lower() for h in headings if h}
    pages: list[int] = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("original_heading") or "").strip().lower()
        if heading not in wanted:
            continue
        raw_pages = item.get("pages") or []
        if isinstance(raw_pages, list):
            for page in raw_pages:
                try:
                    pages.append(int(page))
                except (TypeError, ValueError):
                    continue
    return sorted(set(pages))


def _location_from_pages(pages: list[int]) -> str | None:
    if not pages:
        return None
    if len(pages) == 1:
        return f"p.{pages[0]}"
    return f"p.{pages[0]}-{pages[-1]}"


def _card_matches_route(
    card: object,
    *,
    card_route: set[str],
    section_route: set[str],
    heading_types: dict[str, str],
) -> bool:
    card_type = _as_str(_card_field(card, "card_type", ""))
    if card_type and card_type in card_route:
        return True
    source_sections = _card_field(card, "source_sections", []) or []
    if not isinstance(source_sections, list):
        return False
    for heading in source_sections:
        key = str(heading).strip()
        ntype = heading_types.get(key) or heading_types.get(key.lower())
        if ntype and ntype in section_route:
            return True
    return False


def select_paper_excerpts(
    classification: object,
    cards: list[Any],
    sections: list[dict[str, Any]] | None = None,
    *,
    abstract: str = "",
) -> list[dict[str, Any]]:
    """按路由从已确认卡片/摘要选取原文片段，构造可被 PaperExcerpt 校验的 dict。"""
    section_list = list(sections or [])
    section_route = set(build_section_route(classification))
    card_route = set(build_card_route(classification))
    heading_types = _heading_to_type(section_list)

    excerpts: list[dict[str, Any]] = []
    seen_quotes: set[str] = set()

    for card in cards:
        if len(excerpts) >= _MAX_EXCERPTS:
            break
        if not _card_matches_route(
            card,
            card_route=card_route,
            section_route=section_route,
            heading_types=heading_types,
        ):
            continue
        raw_quote = str(_card_field(card, "source_quote", "") or "").strip()
        if not raw_quote:
            continue
        quote = _truncate_quote(raw_quote)
        if not quote or quote in seen_quotes:
            continue
        seen_quotes.add(quote)

        source_sections = _card_field(card, "source_sections", []) or []
        headings = (
            [str(item) for item in source_sections]
            if isinstance(source_sections, list)
            else []
        )
        section_label = headings[0] if headings else _as_str(
            _card_field(card, "card_type", "")
        )
        pages = _pages_for_headings(section_list, headings)
        content = str(_card_field(card, "content", "") or "").strip()
        surrounding = _truncate_quote(content, limit=_MAX_QUOTE_CHARS) if content else None
        if surrounding == quote:
            surrounding = None

        item: dict[str, Any] = {"quote": quote}
        if section_label:
            item["section"] = section_label
        location = _location_from_pages(pages)
        if location:
            item["location"] = location
        if surrounding:
            item["surrounding_context"] = surrounding
        excerpts.append(item)

    # ABSTRACT 在路由中且有摘要原文时，可追加一条（仍是原文，非模型杜撰）。
    abstract_text = (abstract or "").strip()
    if (
        len(excerpts) < _MAX_EXCERPTS
        and SectionType.ABSTRACT.value in section_route
        and abstract_text
    ):
        quote = _truncate_quote(abstract_text)
        if quote and quote not in seen_quotes:
            excerpts.append({"section": "Abstract", "quote": quote})

    return excerpts


__all__ = [
    "build_card_route",
    "build_section_route",
    "select_paper_excerpts",
]
