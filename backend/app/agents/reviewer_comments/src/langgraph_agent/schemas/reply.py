"""SourceReplyGraph 纯逻辑节点使用的结构化契约。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from .common import ApiSchema, JsonObject, JsonValue


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _coerce_nonblank_str(value: Any) -> Any:
    """容忍模型把 rationale 等字段打成 list/dict 的情况。"""
    if isinstance(value, str):
        return value
    if value is None:
        return value
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(parts) if parts else value
    if isinstance(value, dict):
        # 常见：{"summary": "..."} / {"text": "..."}
        for key in ("text", "summary", "rationale", "value", "content"):
            if key in value and value[key] is not None:
                return str(value[key])
        return " ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


def _coerce_str_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)
            elif item is not None:
                text = str(item).strip()
                if text:
                    result.append(text)
        return result
    return [str(value).strip()] if str(value).strip() else []


CoercedNonBlankStr = Annotated[NonBlankStr, BeforeValidator(_coerce_nonblank_str)]
CoercedStrList = Annotated[list[str], BeforeValidator(_coerce_str_list)]


def _fill_missing_list_fields(
    data: Any, list_fields: tuple[str, ...]
) -> Any:
    """模型常省略空列表字段；校验前补 []，避免 Field required。"""
    if not isinstance(data, dict):
        return data
    patched = dict(data)
    for key in list_fields:
        if key not in patched or patched[key] is None:
            patched[key] = []
    return patched


class ReplyDirection(str, Enum):
    """第 11.5 节定义的来源级回复方向。"""

    ACCEPT_AND_REVISE = "ACCEPT_AND_REVISE"
    PARTIALLY_ACCEPT = "PARTIALLY_ACCEPT"
    CLARIFY_WITH_EVIDENCE = "CLARIFY_WITH_EVIDENCE"
    EXPLAIN_LIMITATION = "EXPLAIN_LIMITATION"
    ACKNOWLEDGE_ONLY = "ACKNOWLEDGE_ONLY"


class SourceClaim(ApiSchema):
    """单个审稿来源的原始意见与归一化诉求。"""

    source_id: UUID
    suggestion_id: UUID
    original_text: NonBlankStr
    localized_claim: NonBlankStr


class ConfirmedAnalysis(ApiSchema):
    """SourceReplyGraph 只读使用的已确认共享分析。"""

    analysis_id: UUID
    suggestion_id: UUID
    input_version: NonBlankStr
    categories: JsonValue
    evidence_items: list[JsonObject]
    coverage: Literal["FULL", "PARTIAL", "NONE", "UNKNOWN"]
    priority: Literal["P0", "P1", "P2", "P3"]
    recommended_actions: JsonValue
    status: Literal["CONFIRMED"]


class ConfirmedModificationFact(ApiSchema):
    """可被正式回复引用的 CONFIRMED ModificationFact。"""

    fact_id: UUID
    suggestion_id: UUID
    action_type: Literal["ACCEPT", "PARTIAL_ACCEPT", "REJECT", "CLARIFY", "DEFER"]
    paper_change_summary: NonBlankStr
    response_fact_summary: NonBlankStr
    constraints: JsonValue
    status: Literal["CONFIRMED"]
    input_version: NonBlankStr


class ClaimInterpretation(ApiSchema):
    """第 11.3/11.8 节定义的来源诉求解释。"""

    reviewer_intent_summary: CoercedNonBlankStr
    implicit_concerns: CoercedStrList = Field(default_factory=list)
    paper_coverage_summary: CoercedNonBlankStr
    required_questions: CoercedStrList = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: Any) -> Any:
        return _fill_missing_list_fields(
            data, ("implicit_concerns", "required_questions")
        )


class ReplyStrategy(ApiSchema):
    """来源级回复策略推荐提案。"""

    recommended_direction: ReplyDirection
    direction_rationale: CoercedNonBlankStr
    emphasis_points: CoercedStrList = Field(default_factory=list)
    avoid_points: CoercedStrList = Field(default_factory=list)
    risk_flags: CoercedStrList = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: Any) -> Any:
        return _fill_missing_list_fields(
            data, ("emphasis_points", "avoid_points", "risk_flags")
        )


class LlmResponseFacts(ApiSchema):
    """LLM 组织回复事实时返回的内容字段。"""

    acknowledgement: NonBlankStr
    direct_answer: NonBlankStr
    author_position: NonBlankStr
    linked_fact_ids: list[UUID] = Field(default_factory=list)
    modification_locations: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: Any) -> Any:
        # 模型常省略空列表；缺省时补 []，避免 Field required。
        return _fill_missing_list_fields(
            data,
            ("linked_fact_ids", "modification_locations", "unresolved_items"),
        )


class ResponseFacts(ApiSchema):
    """第 13.3 节的单条回复要点纯逻辑表示。"""

    response_facts_id: UUID | None
    source_id: UUID
    selected_direction: ReplyDirection
    acknowledgement: NonBlankStr
    direct_answer: NonBlankStr
    author_position: NonBlankStr
    linked_fact_ids: list[UUID]
    fact_item_ids: list[UUID]
    confirmed_revision_action_ids: list[UUID]
    evidence_item_ids: list[str]
    modification_locations: list[str]
    limitation_fact_ids: list[UUID]
    alternative_action_ids: list[UUID]
    unresolved_items: list[str]
    version: int = Field(ge=1)
    confirmation_status: str | None


class LlmResponseDraft(ApiSchema):
    """LLM 生成的草稿正文及其事实引用。"""

    generated_content: NonBlankStr
    used_fact_ids: list[UUID] = Field(min_length=1)


class ResponseDraft(ApiSchema):
    """第 13.4 节的来源级表达版本纯逻辑表示。"""

    draft_version_id: UUID | None
    source_id: UUID
    response_facts_version: int = Field(ge=1)
    language: NonBlankStr
    expression_settings_version: int | None
    generated_content: NonBlankStr
    user_edited_content: str | None
    used_fact_ids: list[UUID]
    consistency_check_result: JsonValue
    review_status: Literal["GENERATED"] = "GENERATED"
    stale_reason: str | None
    created_at: datetime


class ApprovedSourceReply(ApiSchema):
    """同建议下其他已通过来源回复的最小检查输入。"""

    source_id: UUID
    generated_content: NonBlankStr
    linked_fact_ids: list[UUID]


class ConsistencyIssueType(str, Enum):
    """第 7.7/13.8/21.4 节列出的检查问题。"""

    UNSUPPORTED_FACT = "UNSUPPORTED_FACT"
    FACT_CONFLICT = "FACT_CONFLICT"
    STATUS_CONFLICT = "STATUS_CONFLICT"
    STRATEGY_CONFLICT = "STRATEGY_CONFLICT"
    OMITTED_CONCERN = "OMITTED_CONCERN"
    PLACEHOLDER = "PLACEHOLDER"
    CROSS_SOURCE_CONFLICT = "CROSS_SOURCE_CONFLICT"


class ConsistencyIssue(ApiSchema):
    issue_type: ConsistencyIssueType
    description: NonBlankStr
    related_fact_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: Any) -> Any:
        return _fill_missing_list_fields(data, ("related_fact_ids",))


class ConsistencyReport(ApiSchema):
    """即时生成且不自动阻断草稿的一致性报告。"""

    is_consistent: bool
    issues: list[ConsistencyIssue] = Field(default_factory=list)
    cross_source_conflicts: list[ConsistencyIssue] = Field(default_factory=list)
    reminders: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: Any) -> Any:
        # 无跨来源矛盾时模型常省略 cross_source_conflicts，必须补 []。
        return _fill_missing_list_fields(
            data, ("issues", "cross_source_conflicts", "reminders")
        )
