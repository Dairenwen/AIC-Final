"""SourceReplyGraph 五个核心节点的纯逻辑（含防虚构校验）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel

from langgraph_agent.llm import invoke_structured
from langgraph_agent.llm.resilience import LlmFinalError
from langgraph_agent.schemas.reply import (
    ApprovedSourceReply,
    ClaimInterpretation,
    ConfirmedAnalysis,
    ConfirmedModificationFact,
    ConsistencyReport,
    LlmResponseDraft,
    LlmResponseFacts,
    ReplyDirection,
    ReplyStrategy,
    ResponseDraft,
    ResponseFacts,
    SourceClaim,
)
from langgraph_agent.schemas.workspace import ResponseSettings

from .prompts import (
    CONSISTENCY_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    INTERPRET_CLAIM_SYSTEM_PROMPT,
    RESPONSE_FACTS_SYSTEM_PROMPT,
    STRATEGY_SYSTEM_PROMPT,
    build_reply_user_prompt,
)


SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _json_context(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2)


def _invoke_structured(
    schema: type[SchemaT],
    system_prompt: str,
    task: str,
    context: dict[str, Any],
) -> SchemaT:
    try:
        raw_result = invoke_structured(
            "draft",
            schema,
            [
                ("system", system_prompt),
                ("human", build_reply_user_prompt(task, _json_context(context))),
            ],
        )
    except LlmFinalError as error:
        facts = context.get(
            "allowed_confirmed_facts",
            context.get("confirmed_modification_facts", []),
        )
        fact_diagnostic = [
            {
                "fact_id": item.get("fact_id"),
                "status": item.get("status"),
                "action_type": item.get("action_type"),
            }
            for item in facts
            if isinstance(item, dict)
        ]
        raise LlmFinalError(
            f"{error}；reply_schema={schema.__name__}；"
            f"reply_schema_fields={sorted(schema.model_fields)!r}；"
            f"facts={fact_diagnostic!r}",
            cause=error.__cause__ or error,
        ) from error
    return (
        raw_result
        if isinstance(raw_result, schema)
        else schema.model_validate(raw_result)
    )


def _validate_source_context(
    source: SourceClaim,
    analysis: ConfirmedAnalysis,
    facts: Sequence[ConfirmedModificationFact] = (),
) -> None:
    if source.suggestion_id != analysis.suggestion_id:
        raise ValueError("来源诉求与共享分析不属于同一建议")
    if any(fact.suggestion_id != source.suggestion_id for fact in facts):
        raise ValueError("确认事实与来源诉求不属于同一建议")


def _fact_map(
    facts: Sequence[ConfirmedModificationFact],
) -> dict[UUID, ConfirmedModificationFact]:
    return {fact.fact_id: fact for fact in facts}


def interpret_claim(
    source: SourceClaim,
    analysis: ConfirmedAnalysis,
) -> ClaimInterpretation:
    """解释当前来源的明确诉求、隐含担忧和待确认信息。"""
    _validate_source_context(source, analysis)
    return _invoke_structured(
        ClaimInterpretation,
        INTERPRET_CLAIM_SYSTEM_PROMPT,
        "解释当前单个审稿来源的诉求，不推荐或选择回复方向。",
        {
            "source": source.model_dump(mode="json"),
            "confirmed_analysis": analysis.model_dump(mode="json"),
        },
    )


def recommend_strategy(
    source: SourceClaim,
    analysis: ConfirmedAnalysis,
    interpretation: ClaimInterpretation,
    confirmed_facts: Sequence[ConfirmedModificationFact],
) -> ReplyStrategy:
    """为当前来源推荐回复方向，结果仍需用户选择或确认。"""
    _validate_source_context(source, analysis, confirmed_facts)
    return _invoke_structured(
        ReplyStrategy,
        STRATEGY_SYSTEM_PROMPT,
        "基于已确认分析和事实推荐本来源回复方向。推荐不等于用户最终选择。",
        {
            "source": source.model_dump(mode="json"),
            "confirmed_analysis": analysis.model_dump(mode="json"),
            "claim_interpretation": interpretation.model_dump(mode="json"),
            "confirmed_modification_facts": [
                fact.model_dump(mode="json") for fact in confirmed_facts
            ],
        },
    )


def build_response_facts(
    source: SourceClaim,
    selected_direction: ReplyDirection,
    confirmed_facts: Sequence[ConfirmedModificationFact],
) -> ResponseFacts:
    """仅从已确认 ModificationFact 裁剪本条 ResponseFacts。"""
    if any(fact.suggestion_id != source.suggestion_id for fact in confirmed_facts):
        raise ValueError("确认事实与来源诉求不属于同一建议")

    allowed_facts = _fact_map(confirmed_facts)
    llm_result = _invoke_structured(
        LlmResponseFacts,
        RESPONSE_FACTS_SYSTEM_PROMPT,
        "组织单条回复要点，并仅选择允许的 CONFIRMED fact ID。",
        {
            "source": source.model_dump(mode="json"),
            "selected_direction": selected_direction.value,
            "allowed_confirmed_facts": [
                fact.model_dump(mode="json") for fact in confirmed_facts
            ],
        },
    )

    linked_fact_ids = list(dict.fromkeys(llm_result.linked_fact_ids))
    unknown_ids = set(linked_fact_ids) - set(allowed_facts)
    if unknown_ids:
        raise ValueError(
            f"ResponseFacts 引用了未确认事实：{sorted(map(str, unknown_ids))}"
        )
    if selected_direction is not ReplyDirection.ACKNOWLEDGE_ONLY and not linked_fact_ids:
        raise ValueError("非纯致谢回复必须引用至少一条已确认事实")

    return ResponseFacts(
        response_facts_id=None,
        source_id=source.source_id,
        selected_direction=selected_direction,
        acknowledgement=llm_result.acknowledgement,
        direct_answer=llm_result.direct_answer,
        author_position=llm_result.author_position,
        linked_fact_ids=linked_fact_ids,
        fact_item_ids=[],
        confirmed_revision_action_ids=[],
        evidence_item_ids=[],
        modification_locations=llm_result.modification_locations,
        limitation_fact_ids=[],
        alternative_action_ids=[],
        unresolved_items=llm_result.unresolved_items,
        version=1,
        confirmation_status=None,
    )


def generate_draft(
    source: SourceClaim,
    response_facts: ResponseFacts,
    confirmed_facts: Sequence[ConfirmedModificationFact],
    expression_settings: ResponseSettings,
) -> ResponseDraft:
    """根据确认事实与表达设置生成来源级草稿。"""
    if source.source_id != response_facts.source_id:
        raise ValueError("ResponseFacts 不属于当前来源")

    allowed_facts = _fact_map(confirmed_facts)
    linked_ids = set(response_facts.linked_fact_ids)
    if linked_ids - set(allowed_facts):
        raise ValueError("ResponseFacts 包含未确认或不存在的事实引用")
    # 有 linked 确认事实即可生成；unresolved_items 仅作提醒，不阻断草稿。
    if (
        response_facts.selected_direction is not ReplyDirection.ACKNOWLEDGE_ONLY
        and not linked_ids
    ):
        unresolved_hint = ""
        if response_facts.unresolved_items:
            preview = "；".join(response_facts.unresolved_items[:5])
            unresolved_hint = f" 当前未决提醒：{preview}"
        raise ValueError(
            "未绑定任何已确认修改事实，不能生成正式草稿。"
            "请在确认回复事实步骤勾选至少一条 CONFIRMED ModificationFact。"
            f"{unresolved_hint}"
        )
    selected_facts = [allowed_facts[fact_id] for fact_id in response_facts.linked_fact_ids]

    task = (
        "按表达设置生成正式单条回复，只能使用 linked_fact_ids 对应事实；"
        "计划/PLANNED/DEFER 必须用计划向表述，禁止完成时态。"
    )
    if response_facts.unresolved_items:
        task += (
            " 上下文含 unresolved_items，不得写成已完成修改或实验结果，优先完全不提。"
        )

    llm_result = _invoke_structured(
        LlmResponseDraft,
        DRAFT_SYSTEM_PROMPT,
        task,
        {
            "source": source.model_dump(mode="json"),
            "confirmed_response_facts": response_facts.model_dump(mode="json"),
            "allowed_confirmed_facts": [
                fact.model_dump(mode="json") for fact in selected_facts
            ],
            "expression_settings": expression_settings.model_dump(mode="json"),
            "unresolved_reminders": list(response_facts.unresolved_items),
        },
    )

    used_fact_ids = list(dict.fromkeys(llm_result.used_fact_ids))
    if set(used_fact_ids) - linked_ids:
        raise ValueError("草稿引用了 ResponseFacts 之外的事实")
    if linked_ids and not used_fact_ids:
        raise ValueError("草稿未声明其使用的确认事实")

    return ResponseDraft(
        draft_version_id=None,
        source_id=source.source_id,
        response_facts_version=response_facts.version,
        language=expression_settings.response_language,
        expression_settings_version=None,
        generated_content=llm_result.generated_content,
        user_edited_content=None,
        used_fact_ids=used_fact_ids,
        consistency_check_result=None,
        stale_reason=None,
        created_at=datetime.now(timezone.utc),
    )


def check_consistency(
    source: SourceClaim,
    strategy: ReplyStrategy,
    response_facts: ResponseFacts,
    draft: ResponseDraft,
    confirmed_facts: Sequence[ConfirmedModificationFact],
    other_approved_replies: Sequence[ApprovedSourceReply] = (),
) -> ConsistencyReport:
    """即时检查事实及跨来源矛盾，发现问题仅写报告、不修改草稿。"""
    if source.source_id != response_facts.source_id or source.source_id != draft.source_id:
        raise ValueError("策略检查输入不属于同一来源")

    current_fact_ids = set(_fact_map(confirmed_facts))
    if set(response_facts.linked_fact_ids) - current_fact_ids:
        raise ValueError("一致性检查收到未确认事实引用")

    report = _invoke_structured(
        ConsistencyReport,
        CONSISTENCY_SYSTEM_PROMPT,
        "检查当前草稿事实、策略、诉求及可选跨来源回复的一致性，只输出报告。",
        {
            "source": source.model_dump(mode="json"),
            "recommended_strategy": strategy.model_dump(mode="json"),
            "confirmed_response_facts": response_facts.model_dump(mode="json"),
            "generated_draft": draft.model_dump(mode="json"),
            "confirmed_modification_facts": [
                fact.model_dump(mode="json") for fact in confirmed_facts
            ],
            "other_approved_replies": [
                reply.model_dump(mode="json") for reply in other_approved_replies
            ],
        },
    )

    known_fact_ids = current_fact_ids | {
        fact_id
        for reply in other_approved_replies
        for fact_id in reply.linked_fact_ids
    }
    reported_ids = {
        fact_id
        for issue in (*report.issues, *report.cross_source_conflicts)
        for fact_id in issue.related_fact_ids
    }
    if reported_ids - known_fact_ids:
        raise ValueError("一致性报告引用了未知事实 ID")

    has_issues = bool(report.issues or report.cross_source_conflicts)
    if has_issues and report.is_consistent:
        report = report.model_copy(update={"is_consistent": False})
    return report
