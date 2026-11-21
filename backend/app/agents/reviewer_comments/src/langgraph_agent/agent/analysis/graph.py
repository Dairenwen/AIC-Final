"""SuggestionAnalysisGraph 的 FAST/SLOW 主链与人工确认关卡。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from langgraph_agent.agent.analysis.node import (
    analyze_evidence,
    assess_priority,
    classify_suggestion,
    recommend_actions,
)
from langgraph_agent.agent.analysis.persist import persist_analysis
from langgraph_agent.ports.analysis_store import AnalysisStore
from langgraph_agent.ports.manuscript_store import ManuscriptStore
from langgraph_agent.ports.suggestion_store import SuggestionStore
from langgraph_agent.ports.types import PaperBaseline
from langgraph_agent.schemas.analysis import (
    ActionRecommendations,
    ClassificationDecision,
    ClassificationResult,
    EvidenceAssessment,
    IssueType,
    PaperExcerpt,
    PriorityAssessment,
)
from langgraph_agent.schemas.interaction import (
    EditableField,
    EditableFieldType,
    InteractionOption,
    PendingInteraction,
    ResumeCommand,
)
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import WorkspaceMode
from langgraph_agent.tools.paper_evidence import (
    build_card_route,
    build_section_route,
    select_paper_excerpts,
)


_CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.75
_DECISION_STATUSES = {"ACCEPTED", "REJECTED", "DEFERRED"}
_EXECUTION_STATUSES = {
    "NOT_STARTED",
    "PLANNED",
    "IN_PROGRESS",
    "COMPLETED",
    "NOT_FEASIBLE",
    "NOT_REQUIRED",
}
_FACT_ACTION_TYPES = {"ACCEPT", "PARTIAL_ACCEPT", "REJECT", "CLARIFY", "DEFER"}


class SuggestionAnalysisState(TypedDict):
    workspace_id: UUID
    suggestion_id: UUID
    user_id: str
    mode: WorkspaceMode
    manuscript_version_id: UUID | None
    thread_id: str
    run_id: UUID
    input_version: str
    phase: str
    pending_interaction_id: UUID | None
    draft_refs: dict[str, Any]
    result_refs: list[dict[str, str]]
    status: GraphRunStatus
    error_code: str | None


class AnalysisGraphStores(Protocol):
    """图工厂注入的 Store 集合（dict 或对象均可）。"""

    @property
    def analysis_store(self) -> AnalysisStore: ...

    @property
    def suggestion_store(self) -> SuggestionStore | None: ...

    @property
    def manuscript_store(self) -> ManuscriptStore | None: ...


def _resolve_stores(
    stores: AnalysisGraphStores | Mapping[str, Any],
) -> tuple[AnalysisStore, SuggestionStore | None, ManuscriptStore | None]:
    if isinstance(stores, Mapping):
        analysis_store = stores.get("analysis_store")
        if analysis_store is None:
            raise ValueError("stores 必须提供 analysis_store")
        return (
            analysis_store,
            stores.get("suggestion_store"),
            stores.get("manuscript_store"),
        )
    analysis_store = getattr(stores, "analysis_store", None)
    if analysis_store is None:
        raise ValueError("stores 必须提供 analysis_store")
    return (
        analysis_store,
        getattr(stores, "suggestion_store", None),
        getattr(stores, "manuscript_store", None),
    )


def _draft(state: SuggestionAnalysisState) -> dict[str, Any]:
    return dict(state.get("draft_refs", {}))


def _thread_id(state: SuggestionAnalysisState) -> str:
    return state["thread_id"]


def _optional_manuscript_version_id(
    value: object,
    field_name: str,
) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} 不是合法 UUID") from error


def _interaction_id(state: SuggestionAnalysisState, name: str) -> UUID:
    return uuid5(UUID(str(state["run_id"])), name)


def _resume_payload(value: object, interaction: PendingInteraction) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("恢复数据必须是 JSON 对象")
    command_keys = {
        "workspace_id",
        "thread_id",
        "interaction_id",
        "input_version",
        "payload",
    }
    if command_keys.issubset(value):
        command = ResumeCommand.model_validate(value)
        if command.workspace_id != interaction.workspace_id:
            raise ValueError("恢复命令的 workspace_id 不匹配")
        if command.thread_id != interaction.thread_id:
            raise ValueError("恢复命令的 thread_id 不匹配")
        if command.interaction_id != interaction.interaction_id:
            raise ValueError("恢复命令的 interaction_id 不匹配")
        if command.input_version != interaction.input_version:
            raise ValueError("恢复命令的 input_version 已过期")
        return dict(command.payload)
    return dict(value)


def _empty_paper_baseline() -> PaperBaseline:
    return {
        "has_baseline": False,
        "manuscript_version_id": None,
        "abstract": "",
        "sections": [],
        "cards": [],
    }


def _load_paper_baseline(
    *,
    workspace_id: UUID,
    suggestion_id: UUID,
    input_version: str,
    manuscript_version_id: UUID | None,
    analysis_store: AnalysisStore,
    manuscript_store: ManuscriptStore | None,
) -> PaperBaseline:
    """加载论文基线；优先走 AnalysisStore 上下文，其次 ManuscriptStore。"""
    if manuscript_version_id is None:
        return _empty_paper_baseline()

    if manuscript_store is not None:
        manuscript = manuscript_store.get_manuscript_version(manuscript_version_id)
        if manuscript is None:
            raise ValueError("分析绑定的论文版本不存在")
        if manuscript["workspace_id"] != workspace_id:
            raise ValueError("分析绑定的论文版本不属于当前 Workspace")
        if manuscript["parse_status"] != "SUCCEEDED":
            raise ValueError("分析绑定的论文版本尚未解析成功")
        summary = manuscript.get("structure_summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        sections = summary.get("sections") or []
        if not isinstance(sections, list):
            sections = []
        abstract = str(summary.get("abstract") or "")
        records = manuscript_store.get_paper_cards(
            workspace_id,
            manuscript["manuscript_version_id"],
            confirmed_only=True,
        )
        cards = [
            {
                "card_type": record["card_type"],
                "content": record["content"],
                "source_sections": list(record.get("source_sections") or []),
                "source_quote": record["source_quote"],
                "confidence": record["confidence"],
                "confirmation_status": record["confirmation_status"],
            }
            for record in records
        ]
        return {
            "has_baseline": True,
            "manuscript_version_id": str(manuscript["manuscript_version_id"]),
            "abstract": abstract,
            "sections": [
                {
                    "original_heading": str(item.get("original_heading") or ""),
                    "normalized_type": str(item.get("normalized_type") or ""),
                    "pages": list(item.get("pages") or [])
                    if isinstance(item, dict)
                    else [],
                    "confidence": item.get("confidence")
                    if isinstance(item, dict)
                    else None,
                }
                for item in sections
                if isinstance(item, dict)
            ],
            "cards": cards,
        }

    context = analysis_store.load_analysis_context(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=input_version,
        manuscript_version_id=manuscript_version_id,
    )
    baseline = context.get("paper_baseline") or _empty_paper_baseline()
    return baseline  # type: ignore[return-value]


def check_reuse(
    state: SuggestionAnalysisState,
    analysis_store: AnalysisStore,
) -> dict[str, object]:
    """复用版本一致的已确认分析，不重复调用 LLM。"""
    suggestion_id = UUID(str(state["suggestion_id"]))
    workspace_id = UUID(str(state["workspace_id"]))
    manuscript_version_id = _optional_manuscript_version_id(
        state.get("manuscript_version_id"),
        "state.manuscript_version_id",
    )
    context = analysis_store.load_analysis_context(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=str(state["input_version"]),
        manuscript_version_id=manuscript_version_id,
    )
    if context.get("reusable"):
        snapshot = context.get("current_snapshot")
        if snapshot is None:
            raise ValueError("可复用标记为真但缺少 current_snapshot")
        refs: list[dict[str, str]] = [
            {"type": "analysis", "id": str(snapshot["analysis_id"])}
        ]
        for fact in context.get("confirmed_facts") or []:
            refs.append(
                {"type": "modification_fact", "id": str(fact["fact_id"])}
            )
        return {
            "phase": "REUSED",
            "result_refs": refs,
            "status": GraphRunStatus.SUCCEEDED,
        }
    return {"phase": "LOAD_SUGGESTION", "status": GraphRunStatus.RUNNING}


def _reuse_route(state: SuggestionAnalysisState) -> Literal["reuse", "run"]:
    return "reuse" if state["phase"] == "REUSED" else "run"


def load_suggestion(
    state: SuggestionAnalysisState,
    analysis_store: AnalysisStore,
    suggestion_store: SuggestionStore | None = None,
) -> dict[str, object]:
    """读取建议及有效来源，只把运行所需小对象放入 checkpoint。"""
    suggestion_id = UUID(str(state["suggestion_id"]))
    workspace_id = UUID(str(state["workspace_id"]))

    if suggestion_store is not None:
        bundle = suggestion_store.load_suggestion_bundle(
            suggestion_id,
            workspace_id=workspace_id,
            source_status="ACTIVE",
        )
        suggestion = bundle["suggestion"]
        sources = bundle["sources"]
    else:
        context = analysis_store.load_analysis_context(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            input_version=str(state["input_version"]),
            manuscript_version_id=_optional_manuscript_version_id(
                state.get("manuscript_version_id"),
                "state.manuscript_version_id",
            ),
        )
        suggestion = context["suggestion"]
        sources = context["sources"]

    if not sources:
        raise ValueError("Suggestion 没有 ACTIVE 来源")

    context_payload = {
        "canonical_text": suggestion["canonical_text"],
        "sources": [
            {
                "source_id": str(source["source_id"]),
                "excerpt": source["excerpt"],
                "localized_claim": source["localized_claim"],
            }
            for source in sources
        ],
    }
    draft_refs = _draft(state)
    draft_refs["suggestion_context"] = context_payload
    return {"phase": "CLASSIFY", "draft_refs": draft_refs}


def classify(state: SuggestionAnalysisState) -> dict[str, object]:
    draft_refs = _draft(state)
    context = draft_refs.get("suggestion_context")
    if not isinstance(context, dict):
        raise ValueError("缺少 Suggestion 上下文")
    sources = context.get("sources", [])
    classification = classify_suggestion(
        str(context["canonical_text"]),
        [str(item["localized_claim"]) for item in sources],
    )
    draft_refs["classification"] = classification.model_dump(mode="json")
    requires_confirmation = (
        classification.classification_confidence
        < _CLASSIFICATION_CONFIDENCE_THRESHOLD
        or classification.primary_type is IssueType.ETHICS_RESEARCH_INTEGRITY
    )
    if requires_confirmation:
        return {
            "phase": "CONFIRM_CLASSIFICATION",
            "pending_interaction_id": _interaction_id(
                state, "CONFIRM_CLASSIFICATION"
            ),
            "draft_refs": draft_refs,
            "status": GraphRunStatus.WAITING_USER,
        }
    return {
        "phase": "EVIDENCE",
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def _classification_route(
    state: SuggestionAnalysisState,
) -> Literal["confirm", "fast", "slow"]:
    if state["phase"] == "CONFIRM_CLASSIFICATION":
        return "confirm"
    return "slow" if state["mode"] in {WorkspaceMode.SLOW, "SLOW"} else "fast"


def _classification_interaction(
    state: SuggestionAnalysisState,
) -> PendingInteraction:
    classification = _draft(state).get("classification", {})
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_CLASSIFICATION",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        source_id=None,
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认问题分类",
        question="该建议分类置信度较低或风险较高，请确认或编辑分类结果。",
        context={"classification": classification},
        options=[InteractionOption(value="confirm", label="确认分类")],
        editable_fields=[
            EditableField(
                key="classification",
                label="确认后的分类对象",
                type=EditableFieldType.TEXTAREA,
                required=False,
            )
        ],
        blockers=[],
        resume_action="confirm_classification",
    )


def confirm_classification(state: SuggestionAnalysisState) -> dict[str, object]:
    interaction = _classification_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    if payload.get("approved") is False:
        raise ValueError("分类未确认，分析图不能继续")
    draft_refs = _draft(state)
    original = ClassificationResult.model_validate(draft_refs["classification"])
    edited = payload.get("classification")
    decision = (
        ClassificationDecision.model_validate(edited)
        if isinstance(edited, dict)
        else original.automatic_result
    )
    draft_refs["classification"] = original.model_copy(
        update={"confirmed_result": decision}
    ).model_dump(mode="json")
    draft_refs["classification_confirmed_by_user"] = True
    return {
        "phase": "EVIDENCE",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def _mode_after_confirmation(
    state: SuggestionAnalysisState,
) -> Literal["fast", "slow"]:
    return "slow" if state["mode"] in {WorkspaceMode.SLOW, "SLOW"} else "fast"


def collect_user_facts(state: SuggestionAnalysisState) -> dict[str, object]:
    """FAST 模式不臆造事实；仅消费调用方已明确放入 state 的用户事实。"""
    draft_refs = _draft(state)
    context = draft_refs["suggestion_context"]
    evidence = analyze_evidence(
        str(context["canonical_text"]),
        [str(item["localized_claim"]) for item in context["sources"]],
        user_facts=list(draft_refs.get("user_facts", [])),
    )
    draft_refs["evidence"] = evidence.model_dump(mode="json")
    return {"phase": "ASSESS_PRIORITY", "draft_refs": draft_refs}


def build_check_plan(
    state: SuggestionAnalysisState,
    analysis_store: AnalysisStore,
    manuscript_store: ManuscriptStore | None = None,
) -> dict[str, object]:
    """按分类结果产出论文核查计划；无基线时安全降级。"""
    draft_refs = _draft(state)
    classification = draft_refs.get("classification") or {}
    target_sections = build_section_route(classification)
    target_card_types = build_card_route(classification)

    primary_type = ""
    target_subtype = ""
    if isinstance(classification, dict):
        confirmed = classification.get("confirmed_result")
        source = (
            confirmed
            if isinstance(confirmed, dict) and confirmed.get("primary_type")
            else classification
        )
        primary_type = str(source.get("primary_type") or "")
        target_subtype = str(source.get("target_subtype") or "")

    manuscript_version_id = _optional_manuscript_version_id(
        state.get("manuscript_version_id"),
        "state.manuscript_version_id",
    )
    baseline = _load_paper_baseline(
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        input_version=str(state["input_version"]),
        manuscript_version_id=manuscript_version_id,
        analysis_store=analysis_store,
        manuscript_store=manuscript_store,
    )
    confirmed_card_count = len(baseline["cards"])
    has_abstract = bool(str(baseline.get("abstract") or "").strip())

    if not baseline["has_baseline"]:
        status = "DEGRADED"
        degraded_reason = "NO_BASELINE"
    elif confirmed_card_count == 0 and not has_abstract:
        status = "DEGRADED"
        degraded_reason = "NO_CONFIRMED_CARDS"
    else:
        status = "READY"
        degraded_reason = None

    # 只写元数据，不塞卡片/章节全文，避免 checkpoint 膨胀。
    draft_refs["check_plan"] = {
        "status": status,
        "degraded_reason": degraded_reason,
        "primary_type": primary_type,
        "target_subtype": target_subtype,
        "target_sections": target_sections,
        "target_card_types": target_card_types,
        "manuscript_version_id": (
            str(manuscript_version_id)
            if manuscript_version_id is not None
            else None
        ),
        "confirmed_card_count": confirmed_card_count,
        "section_count": len(baseline["sections"]),
        "has_abstract": has_abstract,
    }
    return {"phase": "RETRIEVE_EVIDENCE", "draft_refs": draft_refs}


def retrieve_evidence(
    state: SuggestionAnalysisState,
    analysis_store: AnalysisStore,
    manuscript_store: ManuscriptStore | None = None,
) -> dict[str, object]:
    """按检查计划从论文基线选取原文片段；无基线时返回空证据。"""
    draft_refs = _draft(state)
    check_plan = draft_refs.get("check_plan") or {}
    classification = draft_refs.get("classification") or {}
    if not isinstance(check_plan, dict):
        raise ValueError("check_plan 必须是对象")
    manuscript_version_id = _optional_manuscript_version_id(
        state.get("manuscript_version_id"),
        "state.manuscript_version_id",
    )
    plan_manuscript_version_id = check_plan.get("manuscript_version_id")
    normalized_plan_version_id = _optional_manuscript_version_id(
        plan_manuscript_version_id,
        "check_plan.manuscript_version_id",
    )
    if normalized_plan_version_id != manuscript_version_id:
        raise ValueError("check_plan.manuscript_version_id 与 state 不一致")

    paper_excerpts: list[dict[str, Any]] = []
    degraded = (
        check_plan.get("status") == "DEGRADED"
        and check_plan.get("degraded_reason") == "NO_BASELINE"
    )
    if not degraded:
        # 再读一次 store：不依赖 check_plan 内的大对象。
        baseline = _load_paper_baseline(
            workspace_id=UUID(str(state["workspace_id"])),
            suggestion_id=UUID(str(state["suggestion_id"])),
            input_version=str(state["input_version"]),
            manuscript_version_id=manuscript_version_id,
            analysis_store=analysis_store,
            manuscript_store=manuscript_store,
        )
        if baseline["has_baseline"] and (
            baseline["cards"] or str(baseline.get("abstract") or "").strip()
        ):
            raw_items = select_paper_excerpts(
                classification,
                baseline["cards"],
                baseline["sections"],
                abstract=str(baseline.get("abstract") or ""),
            )
            for item in raw_items:
                try:
                    paper_excerpts.append(
                        PaperExcerpt.model_validate(item).model_dump(mode="json")
                    )
                except Exception:
                    # 单项校验失败则丢弃，不阻断整条分析链。
                    continue

    draft_refs["paper_excerpts"] = paper_excerpts
    return {"phase": "JUDGE_COVERAGE", "draft_refs": draft_refs}


def judge_coverage(state: SuggestionAnalysisState) -> dict[str, object]:
    draft_refs = _draft(state)
    context = draft_refs["suggestion_context"]
    evidence = analyze_evidence(
        str(context["canonical_text"]),
        [str(item["localized_claim"]) for item in context["sources"]],
        paper_excerpts=list(draft_refs.get("paper_excerpts", [])),
    )
    draft_refs["evidence"] = evidence.model_dump(mode="json")
    return {"phase": "ASSESS_PRIORITY", "draft_refs": draft_refs}


def assess_priority_node(state: SuggestionAnalysisState) -> dict[str, object]:
    draft_refs = _draft(state)
    context = draft_refs["suggestion_context"]
    priority = assess_priority(
        str(context["canonical_text"]),
        ClassificationResult.model_validate(draft_refs["classification"]),
        EvidenceAssessment.model_validate(draft_refs["evidence"]),
        [str(item["localized_claim"]) for item in context["sources"]],
        repeated_reviewer_count=len(context["sources"]),
    )
    draft_refs["priority"] = priority.model_dump(mode="json")
    return {"phase": "RECOMMEND_ACTIONS", "draft_refs": draft_refs}


def recommend_actions_node(state: SuggestionAnalysisState) -> dict[str, object]:
    draft_refs = _draft(state)
    context = draft_refs["suggestion_context"]
    actions = recommend_actions(
        str(context["canonical_text"]),
        ClassificationResult.model_validate(draft_refs["classification"]),
        EvidenceAssessment.model_validate(draft_refs["evidence"]),
        PriorityAssessment.model_validate(draft_refs["priority"]),
        [str(item["localized_claim"]) for item in context["sources"]],
        suggestion_id=str(state["suggestion_id"]),
    )
    draft_refs["action_recommendations"] = actions.model_dump(mode="json")
    return {
        "phase": "CONFIRM_FACTS",
        "pending_interaction_id": _interaction_id(state, "CONFIRM_FACTS"),
        "draft_refs": draft_refs,
        "status": GraphRunStatus.WAITING_USER,
    }


def _facts_interaction(state: SuggestionAnalysisState) -> PendingInteraction:
    actions = _draft(state).get("action_recommendations", {})
    recommendations = actions.get("recommendations", [])
    default_facts = [
        {
            "recommendation_id": item["recommendation_id"],
            "decision_status": "ACCEPTED",
            "execution_status": "PLANNED",
            "action_type": _derive_fact_action_type("ACCEPTED", item),
            "paper_change_summary": item["description"],
            "response_fact_summary": f"计划完成：{item['title']}",
            "constraints": {},
        }
        for item in recommendations
    ]
    choices = [
        InteractionOption(
            value=item["recommendation_id"],
            label=item["title"],
            description=item["description"],
        )
        for item in recommendations
    ]
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_MODIFICATION_FACTS",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        source_id=None,
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认修改事实与动作",
        question="请确认每项动作决策、执行状态及回复中可陈述的真实事实。",
        context={
            "action_recommendations": recommendations,
            "fact_schema": {
                "decision_statuses": sorted(_DECISION_STATUSES),
                "execution_statuses": sorted(_EXECUTION_STATUSES),
                "action_types": sorted(_FACT_ACTION_TYPES),
            },
        },
        options=choices,
        editable_fields=[
            EditableField(
                key="facts",
                label="确认后的 ModificationFact 列表",
                type=EditableFieldType.TEXTAREA,
                required=True,
                default=default_facts,
                help_text="每项需包含决策状态、执行状态、事实类型和两类摘要。",
            )
        ],
        blockers=[],
        resume_action="confirm_facts",
    )


def _derive_fact_action_type(
    decision_status: str,
    recommendation: dict[str, Any],
) -> str:
    if decision_status == "REJECTED":
        return "REJECT"
    if decision_status == "DEFERRED":
        return "DEFER"
    resolution = recommendation.get("expected_resolution_level")
    if resolution == "FULL":
        return "ACCEPT"
    if resolution == "PARTIAL":
        return "PARTIAL_ACCEPT"
    return "CLARIFY"


def _confirmed_fact_proposals(
    recommendations: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    if payload.get("approved") is False:
        raise ValueError("修改事实未确认，分析图不能继续")
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("facts 必须是非空数组")
    by_id = {item["recommendation_id"]: item for item in recommendations}
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            raise ValueError("每条 fact 必须是 JSON 对象")
        recommendation_id = str(raw.get("recommendation_id", ""))
        if recommendation_id not in by_id:
            raise ValueError(f"未知动作推荐：{recommendation_id}")
        if recommendation_id in seen:
            raise ValueError(f"动作推荐重复确认：{recommendation_id}")
        seen.add(recommendation_id)
        decision_status = str(raw.get("decision_status", ""))
        execution_status = str(raw.get("execution_status", ""))
        if decision_status not in _DECISION_STATUSES:
            raise ValueError("decision_status 无效")
        if execution_status not in _EXECUTION_STATUSES:
            raise ValueError("execution_status 无效")
        recommendation = by_id[recommendation_id]
        action_type = str(
            raw.get("action_type")
            or _derive_fact_action_type(decision_status, recommendation)
        )
        if action_type not in _FACT_ACTION_TYPES:
            raise ValueError("ModificationFact.action_type 无效")
        paper_summary = str(raw.get("paper_change_summary", "")).strip()
        response_summary = str(raw.get("response_fact_summary", "")).strip()
        if not paper_summary or not response_summary:
            raise ValueError("ModificationFact 的两类摘要不能为空")
        limitations = raw.get("constraints", {})
        if not isinstance(limitations, dict):
            raise ValueError("constraints 必须是 JSON 对象")
        result.append(
            {
                "decision_status": decision_status,
                "execution_status": execution_status,
                "action_type": action_type,
                "paper_change_summary": paper_summary,
                "response_fact_summary": response_summary,
                "constraints": {
                    "decision_status": decision_status,
                    "execution_status": execution_status,
                    "required_facts": recommendation.get("required_facts", []),
                    "prerequisites": recommendation.get("prerequisites", []),
                    "limitations": limitations,
                },
            }
        )
    return result


def confirm_facts(state: SuggestionAnalysisState) -> dict[str, object]:
    interaction = _facts_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    draft_refs = _draft(state)
    actions = ActionRecommendations.model_validate(
        draft_refs["action_recommendations"]
    ).model_dump(mode="json")["recommendations"]
    draft_refs["confirmed_fact_proposals"] = _confirmed_fact_proposals(
        actions, payload
    )
    return {
        "phase": "PERSIST_ANALYSIS",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def build_suggestion_analysis_graph(
    *,
    stores: AnalysisGraphStores | Mapping[str, Any],
    checkpointer,
    store=None,
):
    """编译 SuggestionAnalysisGraph。

    参数:
        stores: 至少含 analysis_store；可选 suggestion_store / manuscript_store
        checkpointer: 必须注入持久化 Checkpointer（interrupt/resume 依赖）
        store: LangGraph Store（可选）
    """
    if checkpointer is None:
        raise ValueError("SuggestionAnalysisGraph 必须注入持久化 Checkpointer")
    analysis_store, suggestion_store, manuscript_store = _resolve_stores(stores)

    graph = StateGraph(SuggestionAnalysisState)
    graph.add_node(
        "check_reuse", lambda state: check_reuse(state, analysis_store)
    )
    graph.add_node(
        "load_suggestion",
        lambda state: load_suggestion(state, analysis_store, suggestion_store),
    )
    graph.add_node("classify", classify)
    graph.add_node("confirm_classification", confirm_classification)
    graph.add_node("collect_user_facts", collect_user_facts)
    graph.add_node(
        "build_check_plan",
        lambda state: build_check_plan(state, analysis_store, manuscript_store),
    )
    graph.add_node(
        "retrieve_evidence",
        lambda state: retrieve_evidence(state, analysis_store, manuscript_store),
    )
    graph.add_node("judge_coverage", judge_coverage)
    graph.add_node("assess_priority", assess_priority_node)
    graph.add_node("recommend_actions", recommend_actions_node)
    graph.add_node("confirm_facts", confirm_facts)
    graph.add_node(
        "persist_analysis",
        lambda state: persist_analysis(state, analysis_store),
    )

    graph.add_edge(START, "check_reuse")
    graph.add_conditional_edges(
        "check_reuse", _reuse_route, {"reuse": END, "run": "load_suggestion"}
    )
    graph.add_edge("load_suggestion", "classify")
    graph.add_conditional_edges(
        "classify",
        _classification_route,
        {
            "confirm": "confirm_classification",
            "fast": "collect_user_facts",
            "slow": "build_check_plan",
        },
    )
    graph.add_conditional_edges(
        "confirm_classification",
        _mode_after_confirmation,
        {"fast": "collect_user_facts", "slow": "build_check_plan"},
    )
    graph.add_edge("build_check_plan", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "judge_coverage")
    graph.add_edge("judge_coverage", "assess_priority")
    graph.add_edge("collect_user_facts", "assess_priority")
    graph.add_edge("assess_priority", "recommend_actions")
    graph.add_edge("recommend_actions", "confirm_facts")
    graph.add_edge("confirm_facts", "persist_analysis")
    graph.add_edge("persist_analysis", END)

    return graph.compile(
        checkpointer=checkpointer,
        store=store,
        name="suggestion_analysis_graph",
    )


__all__ = [
    "SuggestionAnalysisState",
    "build_check_plan",
    "build_suggestion_analysis_graph",
    "check_reuse",
    "classify",
    "collect_user_facts",
    "confirm_classification",
    "confirm_facts",
    "judge_coverage",
    "load_suggestion",
    "retrieve_evidence",
    "_classification_interaction",
    "_confirmed_fact_proposals",
    "_facts_interaction",
    "_resume_payload",
]
