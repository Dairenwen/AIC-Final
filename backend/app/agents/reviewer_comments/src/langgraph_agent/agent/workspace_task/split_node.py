"""审稿意见拆分的纯逻辑 LLM 节点。"""

from __future__ import annotations

import logging
import re

from langgraph_agent.agent.workspace_task.split_prompt import (
    SPLIT_SYSTEM_PROMPT,
    build_split_user_prompt,
)
from langgraph_agent.llm import invoke_structured
from langgraph_agent.schemas import (
    LlmSplitResult,
    ReviewPoint,
    SplitCandidate,
    SplitReviewInput,
    SplitReviewResult,
)

logger = logging.getLogger(__name__)


_POSITIVE_MARKERS = (
    "well written",
    "clearly written",
    "clearly organized",
    "valuable contribution",
    "strong contribution",
    "excellent",
    "写得很好",
    "结构清晰",
    "贡献突出",
    "很有价值",
)

_ACTIONABLE_MARKERS = (
    " however ",
    " but ",
    " although ",
    "please",
    "should",
    "must",
    "need",
    "lack",
    "missing",
    "unclear",
    "concern",
    "issue",
    "problem",
    "improve",
    "clarify",
    "add ",
    "provide",
    "report",
    "compare",
    "但",
    "然而",
    "不过",
    "请",
    "建议",
    "需要",
    "应该",
    "缺少",
    "不足",
    "问题",
    "不清",
    "补充",
    "解释",
    "修改",
    "增加",
    "报告",
    "比较",
)

_EMPTY_CONCERN_VALUES = {
    "none",
    "n/a",
    "not applicable",
    "无",
    "无明确要求",
    "无隐含担忧",
}


def _normalize_for_comparison(value: str) -> str:
    # 统一空白与常见数学符号写法，降低模型轻微改写导致的 quote 误杀。
    text = value.casefold()
    text = (
        text.replace("√", "sqrt")
        .replace("×", "x")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    return re.sub(r"\s+", "", text)


def _is_praise_only(original_text: str) -> bool:
    padded_text = f" {original_text.casefold()} "
    has_positive_marker = any(marker in padded_text for marker in _POSITIVE_MARKERS)
    has_actionable_marker = any(marker in padded_text for marker in _ACTIONABLE_MARKERS)
    return has_positive_marker and not has_actionable_marker


def _has_actionable_content(candidate: SplitCandidate) -> bool:
    values = (candidate.explicit_request, candidate.implicit_concern)
    return any(
        value is not None
        and _normalize_for_comparison(value) not in _EMPTY_CONCERN_VALUES
        for value in values
    )


def _quote_comes_from_original(source_quote: str, original_text: str) -> bool:
    if source_quote in original_text:
        return True
    return _normalize_for_comparison(source_quote) in _normalize_for_comparison(
        original_text
    )


def _postprocess_split_result(
    request: SplitReviewInput,
    llm_result: LlmSplitResult,
) -> SplitReviewResult:
    if _is_praise_only(request.original_text):
        return SplitReviewResult(review_points=[])

    unique_candidates: list[SplitCandidate] = []
    seen_concerns: set[str] = set()
    skipped_bad_quotes = 0

    for candidate in llm_result.review_points:
        if not _has_actionable_content(candidate):
            continue
        if not _quote_comes_from_original(candidate.source_quote, request.original_text):
            # 单条 quote 对不上时丢弃该点，避免整次拆分任务失败。
            skipped_bad_quotes += 1
            logger.warning(
                "拆分结果 source_quote 无法对应原文，已跳过该问题点 concern=%r quote=%r",
                candidate.atomic_concern[:120],
                candidate.source_quote[:160],
            )
            continue

        concern_key = _normalize_for_comparison(candidate.atomic_concern)
        if concern_key in seen_concerns:
            continue
        seen_concerns.add(concern_key)
        unique_candidates.append(candidate)

    if skipped_bad_quotes and not unique_candidates and llm_result.review_points:
        logger.warning(
            "全部 %s 条问题点因 source_quote 无效被跳过，返回空拆分结果",
            skipped_bad_quotes,
        )

    review_points = [
        ReviewPoint(
            point_id=f"P-{index:02d}",
            reviewer_id=None,
            original_item_id=None,
            original_item_number=None,
            original_text=request.original_text,
            atomic_concern=candidate.atomic_concern,
            explicit_request=candidate.explicit_request,
            implicit_concern=candidate.implicit_concern,
            source_order=index,
            source_quote=candidate.source_quote,
            split_confidence=candidate.split_confidence,
            split_status=None,
            parent_point_id=None,
        )
        for index, candidate in enumerate(unique_candidates, start=1)
    ]
    return SplitReviewResult(review_points=review_points)


def split_review_points(
    original_text: str,
    language: str | None = None,
) -> SplitReviewResult:
    """调用 split 用途结构化 LLM，将一条原始意见拆为独立问题点。"""
    request = SplitReviewInput(original_text=original_text, language=language)
    raw_result = invoke_structured(
        "split",
        LlmSplitResult,
        [
            ("system", SPLIT_SYSTEM_PROMPT),
            (
                "human",
                build_split_user_prompt(request.original_text, request.language),
            ),
        ],
    )
    llm_result = (
        raw_result
        if isinstance(raw_result, LlmSplitResult)
        else LlmSplitResult.model_validate(raw_result)
    )
    return _postprocess_split_result(request, llm_result)
