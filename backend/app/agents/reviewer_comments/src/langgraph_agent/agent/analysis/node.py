"""SuggestionAnalysisGraph 核心分析节点的纯逻辑。"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from langgraph_agent.agent.analysis.prompts import (
    ACTION_SYSTEM_PROMPT,
    CLASSIFY_SYSTEM_PROMPT,
    EVIDENCE_SYSTEM_PROMPT,
    PRIORITY_SYSTEM_PROMPT,
    build_action_user_prompt,
    build_classify_user_prompt,
    build_evidence_user_prompt,
    build_priority_user_prompt,
)
from langgraph_agent.llm import invoke_structured
from langgraph_agent.schemas.analysis import (
    AcademicImpact,
    ActionRecommendation,
    ActionRecommendations,
    ClassificationDecision,
    ClassificationResult,
    Coverage,
    EvidenceAssessment,
    EvidenceConfirmationStatus,
    EvidenceItem,
    EvidenceJudgement,
    EvidenceRelation,
    Feasibility,
    HandlingRequirement,
    LlmActionRecommendations,
    LlmClassificationResult,
    LlmEvidenceAssessment,
    LlmPriorityAssessment,
    NonBlankStr,
    PaperExcerpt,
    PriorityAssessment,
    PriorityDecision,
    RevisionEffort,
    ScheduleFlag,
    SuggestionAnalysisInput,
    WorkPriority,
)


SchemaT = TypeVar("SchemaT", bound=BaseModel)
_NON_BLANK_LIST_ADAPTER = TypeAdapter(list[NonBlankStr])


def _invoke_structured(
    schema: type[SchemaT],
    system_prompt: str,
    user_prompt: str,
) -> SchemaT:
    raw_result = invoke_structured(
        "analyze",
        schema,
        [("system", system_prompt), ("human", user_prompt)],
    )
    return raw_result if isinstance(raw_result, schema) else schema.model_validate(raw_result)


def _build_input(
    suggestion_text: str,
    source_requests: list[str] | None,
    suggestion_id: str = "S-01",
    review_point_id: str = "P-01",
) -> SuggestionAnalysisInput:
    return SuggestionAnalysisInput(
        suggestion_text=suggestion_text,
        source_requests=source_requests or [],
        suggestion_id=suggestion_id,
        review_point_id=review_point_id,
    )


def classify_suggestion(
    suggestion_text: str,
    source_requests: list[str] | None = None,
) -> ClassificationResult:
    """对一条建议做多维分类，不判断优先级。"""
    request = _build_input(suggestion_text, source_requests)
    llm_result = _invoke_structured(
        LlmClassificationResult,
        CLASSIFY_SYSTEM_PROMPT,
        build_classify_user_prompt(
            request.suggestion_text,
            request.source_requests,
        ),
    )
    decision = ClassificationDecision.model_validate(
        {
            name: getattr(llm_result, name)
            for name in ClassificationDecision.model_fields
        }
    )
    return ClassificationResult(
        **llm_result.model_dump(),
        automatic_result=decision,
        confirmed_result=None,
    )


def analyze_evidence(
    suggestion_text: str,
    source_requests: list[str] | None = None,
    *,
    paper_excerpts: list[PaperExcerpt | dict[str, object]] | None = None,
    user_facts: list[str] | None = None,
    review_point_id: str = "P-01",
) -> EvidenceAssessment:
    """判断调用方提供的原文，并将用户事实放入同一 evidence_items 结构。"""
    request = _build_input(
        suggestion_text,
        source_requests,
        review_point_id=review_point_id,
    )
    excerpts = [
        item if isinstance(item, PaperExcerpt) else PaperExcerpt.model_validate(item)
        for item in (paper_excerpts or [])
    ]
    facts = _NON_BLANK_LIST_ADAPTER.validate_python(user_facts or [])

    evidence_items: list[EvidenceItem] = []
    coverage = Coverage.UNKNOWN

    if excerpts:
        llm_result = _invoke_structured(
            LlmEvidenceAssessment,
            EVIDENCE_SYSTEM_PROMPT,
            build_evidence_user_prompt(
                request.suggestion_text,
                request.source_requests,
                excerpts,
            ),
        )
        seen_indexes: set[int] = set()
        for candidate in llm_result.evidence_items:
            if candidate.source_index in seen_indexes:
                continue
            if candidate.source_index > len(excerpts):
                raise ValueError("证据判断引用了不存在的 source_index")
            seen_indexes.add(candidate.source_index)
            excerpt = excerpts[candidate.source_index - 1]
            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"E-{len(evidence_items) + 1:02d}",
                    review_point_id=request.review_point_id,
                    section=excerpt.section,
                    location=excerpt.location,
                    quote=excerpt.quote,
                    surrounding_context=excerpt.surrounding_context,
                    relevance=candidate.relevance,
                    evidence_judgement=candidate.evidence_judgement,
                    confirmation_status=EvidenceConfirmationStatus.UNCONFIRMED,
                    relation=candidate.relation,
                )
            )
        coverage = llm_result.coverage
        if not seen_indexes and coverage in {Coverage.FULL, Coverage.PARTIAL}:
            coverage = Coverage.UNKNOWN

    for fact in facts:
        evidence_items.append(
            EvidenceItem(
                evidence_id=f"E-{len(evidence_items) + 1:02d}",
                review_point_id=request.review_point_id,
                section=None,
                location=None,
                quote=fact,
                surrounding_context=None,
                relevance=1.0,
                evidence_judgement=EvidenceJudgement.USER_STATEMENT,
                confirmation_status=EvidenceConfirmationStatus.UNCONFIRMED,
                relation=EvidenceRelation.USER_STATED,
            )
        )

    if not excerpts:
        coverage = Coverage.UNKNOWN
    return EvidenceAssessment(evidence_items=evidence_items, coverage=coverage)


def _derive_work_priority(
    academic_impact: AcademicImpact,
    handling_requirement: HandlingRequirement,
) -> WorkPriority:
    if academic_impact is AcademicImpact.CRITICAL:
        return WorkPriority.P0
    if academic_impact is AcademicImpact.MAJOR:
        return WorkPriority.P1
    if handling_requirement is HandlingRequirement.OPTIONAL:
        return WorkPriority.P3
    return WorkPriority.P2


def _derive_schedule_flag(
    work_priority: WorkPriority,
    revision_effort: RevisionEffort,
    feasibility: Feasibility,
) -> ScheduleFlag | None:
    is_high_priority = work_priority in {WorkPriority.P0, WorkPriority.P1}
    if is_high_priority and feasibility is Feasibility.INFEASIBLE:
        return ScheduleFlag.STRATEGY_REQUIRED
    if is_high_priority and revision_effort is RevisionEffort.HIGH:
        return ScheduleFlag.LONG_LEAD
    if is_high_priority and revision_effort is RevisionEffort.LOW:
        return ScheduleFlag.QUICK_WIN
    if work_priority is WorkPriority.P3 and revision_effort is RevisionEffort.LOW:
        return ScheduleFlag.BATCH_EDIT
    return None


def _has_user_stated_facts(evidence: EvidenceAssessment) -> bool:
    return any(
        item.relation is EvidenceRelation.USER_STATED
        for item in evidence.evidence_items
    )


def assess_priority(
    suggestion_text: str,
    classification: ClassificationResult,
    evidence: EvidenceAssessment,
    source_requests: list[str] | None = None,
    *,
    repeated_reviewer_count: int = 1,
) -> PriorityAssessment:
    """评估优先级；成本和可行性不降低重要性。"""
    request = _build_input(suggestion_text, source_requests)
    if repeated_reviewer_count < 1:
        raise ValueError("repeated_reviewer_count 必须至少为 1")
    llm_result = _invoke_structured(
        LlmPriorityAssessment,
        PRIORITY_SYSTEM_PROMPT,
        build_priority_user_prompt(
            request.suggestion_text,
            request.source_requests,
            classification,
            evidence,
            repeated_reviewer_count,
        ),
    )

    feasibility = (
        llm_result.feasibility
        if _has_user_stated_facts(evidence)
        else Feasibility.UNKNOWN
    )
    work_priority = _derive_work_priority(
        llm_result.academic_impact,
        llm_result.handling_requirement,
    )
    schedule_flag = _derive_schedule_flag(
        work_priority,
        llm_result.revision_effort,
        feasibility,
    )
    risk_signals = list(dict.fromkeys(llm_result.risk_signals))
    decision = PriorityDecision(
        academic_impact=llm_result.academic_impact,
        handling_requirement=llm_result.handling_requirement,
        revision_effort=llm_result.revision_effort,
        feasibility=feasibility,
        work_priority=work_priority,
        schedule_flag=schedule_flag,
        risk_signals=risk_signals,
        repeated_reviewer_count=repeated_reviewer_count,
    )
    return PriorityAssessment(
        **decision.model_dump(),
        assessment_confidence=llm_result.assessment_confidence,
        assessment_reason=llm_result.assessment_reason,
        automatic_result=decision,
        confirmed_result=None,
    )


def recommend_actions(
    suggestion_text: str,
    classification: ClassificationResult,
    evidence: EvidenceAssessment,
    priority: PriorityAssessment,
    source_requests: list[str] | None = None,
    *,
    suggestion_id: str = "S-01",
) -> ActionRecommendations:
    """推荐修改动作，不把计划写成已完成事实。"""
    request = _build_input(
        suggestion_text,
        source_requests,
        suggestion_id=suggestion_id,
    )
    llm_result = _invoke_structured(
        LlmActionRecommendations,
        ACTION_SYSTEM_PROMPT,
        build_action_user_prompt(
            request.suggestion_text,
            request.source_requests,
            classification,
            evidence,
            priority,
        ),
    )

    count = len(llm_result.recommendations)
    has_user_facts = _has_user_stated_facts(evidence)
    recommendations: list[ActionRecommendation] = []
    for index, candidate in enumerate(llm_result.recommendations, start=1):
        alternative_to = None
        if (
            candidate.alternative_to_index is not None
            and candidate.alternative_to_index <= count
            and candidate.alternative_to_index != index
        ):
            alternative_to = f"A-{candidate.alternative_to_index:02d}"
        recommendations.append(
            ActionRecommendation(
                recommendation_id=f"A-{index:02d}",
                suggestion_id=request.suggestion_id,
                action_type=candidate.action_type,
                title=candidate.title,
                description=candidate.description,
                addressed_concern=candidate.addressed_concern,
                necessity=candidate.necessity,
                recommendation_basis=candidate.recommendation_basis,
                required_facts=candidate.required_facts,
                prerequisites=candidate.prerequisites,
                expected_outputs=candidate.expected_outputs,
                estimated_cost=candidate.estimated_cost,
                feasibility=(
                    candidate.feasibility if has_user_facts else Feasibility.UNKNOWN
                ),
                expected_resolution_level=candidate.expected_resolution_level,
                alternative_to=alternative_to,
                alternative_actions=candidate.alternative_actions,
                model_metadata={},
            )
        )
    return ActionRecommendations(recommendations=recommendations)


__all__ = [
    "analyze_evidence",
    "assess_priority",
    "classify_suggestion",
    "recommend_actions",
]
