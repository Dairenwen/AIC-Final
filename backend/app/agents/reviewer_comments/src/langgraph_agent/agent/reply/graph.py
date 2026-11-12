"""SourceReplyGraph 的来源级回复生成与审核流程。"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict
from typing import NotRequired
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from langgraph_agent.ports.reply_store import ReplyStore
from langgraph_agent.ports.types import (
    AnalysisSnapshotRecord,
    ModificationFactRecord,
    ReplyContext,
    SuggestionSourceRecord,
)
from langgraph_agent.schemas.interaction import (
    EditableField,
    EditableFieldType,
    InteractionOption,
    PendingInteraction,
    ResumeCommand,
)
from langgraph_agent.schemas.reply import (
    ApprovedSourceReply,
    ClaimInterpretation,
    ConfirmedAnalysis,
    ConfirmedModificationFact,
    ReplyDirection,
    ReplyStrategy,
    ResponseDraft,
    ResponseFacts,
    SourceClaim,
)
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import ResponseSettings

from .node import (
    build_response_facts,
    check_consistency,
    generate_draft,
    interpret_claim,
    recommend_strategy,
)
from .persist import persist_and_review, persist_review_decision
from .thread_ids import legacy_reply_thread_id


def _ensure_response_settings(raw: object) -> dict[str, Any]:
    """空 dict / 非法设置回退系统默认，避免 seed 与脏 checkpoint 拖垮 REPLY。"""
    from langgraph_agent.adapters.postgres.repositories.suggestion_repo import (
        default_response_settings,
    )

    if not isinstance(raw, dict) or not raw:
        return default_response_settings()
    try:
        return ResponseSettings.model_validate(raw).model_dump(mode="json")
    except Exception:  # noqa: BLE001
        return default_response_settings()


class SourceReplyState(TypedDict):
    workspace_id: UUID
    suggestion_id: UUID
    source_id: UUID
    thread_id: NotRequired[str]
    user_id: str
    run_id: UUID
    input_version: str
    phase: str
    pending_interaction_id: UUID | None
    draft_refs: dict[str, Any]
    result_refs: list[dict[str, str]]
    status: GraphRunStatus
    error_code: str | None


class ReplyGraphStores(Protocol):
    """图工厂注入的最小 store 集合。"""

    reply: ReplyStore


def _draft(state: SourceReplyState) -> dict[str, Any]:
    return dict(state.get("draft_refs", {}))


def _thread_id(state: SourceReplyState) -> str:
    stored_thread_id = state.get("thread_id")
    if stored_thread_id:
        return stored_thread_id
    return legacy_reply_thread_id(state["workspace_id"], state["source_id"])


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


def _source_claim_from_record(source: SuggestionSourceRecord) -> dict[str, Any]:
    return SourceClaim(
        source_id=source["source_id"],
        suggestion_id=source["suggestion_id"],
        original_text=source["excerpt"],
        localized_claim=source["localized_claim"],
    ).model_dump(mode="json")


def _confirmed_analysis_from_record(
    snapshot: AnalysisSnapshotRecord,
) -> dict[str, Any]:
    return ConfirmedAnalysis(
        analysis_id=snapshot["analysis_id"],
        suggestion_id=snapshot["suggestion_id"],
        input_version=snapshot["input_version"],
        categories=snapshot["categories"],
        evidence_items=list(snapshot["evidence_items"]),
        coverage=snapshot["coverage"],  # type: ignore[arg-type]
        priority=snapshot["priority"],  # type: ignore[arg-type]
        recommended_actions=snapshot["recommended_actions"],
        status="CONFIRMED",
    ).model_dump(mode="json")


def _confirmed_fact_from_record(fact: ModificationFactRecord) -> dict[str, Any]:
    return ConfirmedModificationFact(
        fact_id=fact["fact_id"],
        suggestion_id=fact["suggestion_id"],
        action_type=fact["action_type"],  # type: ignore[arg-type]
        paper_change_summary=fact["paper_change_summary"],
        response_fact_summary=fact["response_fact_summary"],
        constraints=fact["constraints"],
        status="CONFIRMED",
        input_version=fact["input_version"],
    ).model_dump(mode="json")


def load_context(
    state: SourceReplyState,
    reply_store: ReplyStore,
) -> dict[str, object]:
    """读取来源、共享分析、确认事实、表达设置及其他已批准回复。"""
    workspace_id = UUID(str(state["workspace_id"]))
    source_id = UUID(str(state["source_id"]))
    suggestion_id = UUID(str(state["suggestion_id"]))
    initial_settings = _draft(state).get("expression_settings")
    if initial_settings is not None and not isinstance(initial_settings, dict):
        raise ValueError("expression_settings 必须是 JSON 对象")

    context: ReplyContext = reply_store.load_reply_context(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        expression_settings=initial_settings,
    )
    source = context["source"]
    if (
        source["workspace_id"] != workspace_id
        or source["suggestion_id"] != suggestion_id
        or source["source_id"] != source_id
    ):
        raise ValueError("SuggestionSource 不存在或运行目标不匹配")

    mapped: dict[str, Any] = {
        "analysis_ready": bool(context["analysis_ready"]),
        "source_claim": _source_claim_from_record(source),
        "expression_settings": _ensure_response_settings(
            context.get("expression_settings")
        ),
        "confirmed_modification_facts": [
            _confirmed_fact_from_record(fact)
            for fact in context["confirmed_modification_facts"]
            if fact.get("status") == "CONFIRMED"
        ],
        "other_approved_replies": [
            ApprovedSourceReply(
                source_id=item["source_id"],
                generated_content=item["generated_content"],
                linked_fact_ids=list(item.get("linked_fact_ids") or []),
            ).model_dump(mode="json")
            for item in context["other_approved_replies"]
        ],
    }
    snapshot = context.get("confirmed_analysis")
    if snapshot is not None and snapshot.get("status") == "CONFIRMED":
        mapped["confirmed_analysis"] = _confirmed_analysis_from_record(snapshot)

    draft_refs = _draft(state)
    draft_refs.update(mapped)
    return {
        "phase": "CHECK_ANALYSIS_READY",
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def check_analysis_ready(state: SourceReplyState) -> dict[str, object]:
    if not _draft(state).get("analysis_ready"):
        return {"phase": "BLOCKED_ANALYSIS"}
    return {"phase": "INTERPRET_CLAIM"}


def _analysis_route(state: SourceReplyState) -> Literal["blocked", "ready"]:
    return "blocked" if state["phase"] == "BLOCKED_ANALYSIS" else "ready"


def block_analysis(state: SourceReplyState) -> dict[str, object]:
    """分析未就绪时结束且不写任何来源回复。"""
    draft_refs = _draft(state)
    draft_refs["blockers"] = ["请先完成并确认该建议的共享分析与修改事实。"]
    return {
        "phase": "BLOCKED_ANALYSIS",
        "draft_refs": draft_refs,
        "result_refs": [],
        "status": GraphRunStatus.SUCCEEDED,
        "error_code": None,
    }


def interpret_claim_node(state: SourceReplyState) -> dict[str, object]:
    draft_refs = _draft(state)
    interpretation = interpret_claim(
        SourceClaim.model_validate(draft_refs["source_claim"]),
        ConfirmedAnalysis.model_validate(draft_refs["confirmed_analysis"]),
    )
    draft_refs["claim_interpretation"] = interpretation.model_dump(mode="json")
    return {"phase": "RECOMMEND_STRATEGY", "draft_refs": draft_refs}


def recommend_strategy_node(state: SourceReplyState) -> dict[str, object]:
    draft_refs = _draft(state)
    strategy = recommend_strategy(
        SourceClaim.model_validate(draft_refs["source_claim"]),
        ConfirmedAnalysis.model_validate(draft_refs["confirmed_analysis"]),
        ClaimInterpretation.model_validate(draft_refs["claim_interpretation"]),
        [
            ConfirmedModificationFact.model_validate(item)
            for item in draft_refs["confirmed_modification_facts"]
        ],
    )
    draft_refs["strategy_proposal"] = strategy.model_dump(mode="json")
    return {
        "phase": "CONFIRM_STRATEGY",
        "pending_interaction_id": uuid5(
            UUID(str(state["run_id"])), "CONFIRM_REPLY_STRATEGY"
        ),
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def strategy_interaction(state: SourceReplyState) -> PendingInteraction:
    """构造策略确认交互（当前自动确认路径不 interrupt，保留供门面/测试）。"""
    proposal = _draft(state)["strategy_proposal"]
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_REPLY_STRATEGY",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        source_id=UUID(str(state["source_id"])),
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认回复策略",
        question="请确认或编辑当前来源的回复方向。",
        context={"strategy": proposal},
        options=[
            InteractionOption(value=item.value, label=item.value)
            for item in ReplyDirection
        ],
        editable_fields=[
            EditableField(
                key="selected_direction",
                label="回复方向",
                type=EditableFieldType.SELECT,
                required=True,
                default=proposal["recommended_direction"],
                choices=[
                    InteractionOption(value=item.value, label=item.value)
                    for item in ReplyDirection
                ],
            )
        ],
        blockers=[],
        resume_action="confirm_strategy",
    )


def confirm_strategy(state: SourceReplyState) -> dict[str, object]:
    """自动采用系统推荐策略，不再中断等人确认，便于批量并发生成草稿。"""
    draft_refs = _draft(state)
    proposal = dict(draft_refs["strategy_proposal"])
    confirmed = ReplyStrategy.model_validate(proposal)
    draft_refs["confirmed_strategy"] = confirmed.model_dump(mode="json")
    return {
        "phase": "BUILD_RESPONSE_FACTS",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def build_response_facts_node(state: SourceReplyState) -> dict[str, object]:
    draft_refs = _draft(state)
    strategy = ReplyStrategy.model_validate(draft_refs["confirmed_strategy"])
    response_facts = build_response_facts(
        SourceClaim.model_validate(draft_refs["source_claim"]),
        strategy.recommended_direction,
        [
            ConfirmedModificationFact.model_validate(item)
            for item in draft_refs["confirmed_modification_facts"]
        ],
    )
    draft_refs["response_facts_proposal"] = response_facts.model_dump(mode="json")
    return {
        "phase": "CONFIRM_RESPONSE_FACTS",
        "pending_interaction_id": uuid5(
            UUID(str(state["run_id"])), "CONFIRM_RESPONSE_FACTS"
        ),
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def response_facts_interaction(state: SourceReplyState) -> PendingInteraction:
    """构造回复事实确认交互（当前自动确认路径不 interrupt，保留供门面/测试）。"""
    proposal = _draft(state)["response_facts_proposal"]
    allowed = _draft(state)["confirmed_modification_facts"]
    unresolved_items = list(proposal.get("unresolved_items") or [])
    choices = [
        InteractionOption(
            value=item["fact_id"],
            label=item["response_fact_summary"],
        )
        for item in allowed
    ]
    blockers: list[str] = []
    if unresolved_items:
        blockers.append(
            "模型列出了未决提醒（默认确认后清空，仅用已勾选确认事实生成计划向草稿）："
        )
        blockers.extend(f"· {item}" for item in unresolved_items[:8])
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_RESPONSE_FACTS",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        source_id=UUID(str(state["source_id"])),
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认回复事实",
        question=(
            "请确认本条回复允许引用的已确认修改事实。"
            "确认后将仅用勾选事实生成草稿；未决提醒默认清空，不阻断生成。"
        ),
        context={
            "response_facts": proposal,
            "allowed_facts": allowed,
            "unresolved_items": unresolved_items,
        },
        options=choices,
        editable_fields=[
            EditableField(
                key="linked_fact_ids",
                label="引用的确认事实",
                type=EditableFieldType.MULTISELECT,
                required=False,
                default=proposal["linked_fact_ids"],
                choices=choices,
            ),
            EditableField(
                key="clear_unresolved",
                label="清空未决提醒（推荐：仅用已勾选确认事实生成草稿）",
                type=EditableFieldType.CHECKBOX,
                required=False,
                default=True,
                help_text=(
                    "勾选后确认时将 unresolved_items 置空；"
                    "取消勾选则保留模型列出的未决提醒。"
                ),
            ),
        ],
        blockers=blockers,
        resume_action="confirm_facts",
    )


def confirm_response_facts(state: SourceReplyState) -> dict[str, object]:
    """自动确认回复事实：采用模型勾选的 linked 事实，并清空未决提醒。"""
    draft_refs = _draft(state)
    proposal = dict(draft_refs["response_facts_proposal"])
    proposal["unresolved_items"] = []
    proposal["confirmation_status"] = "CONFIRMED"
    confirmed = ResponseFacts.model_validate(proposal)
    allowed_ids = {
        UUID(str(item["fact_id"]))
        for item in draft_refs["confirmed_modification_facts"]
    }
    if set(confirmed.linked_fact_ids) - allowed_ids:
        raise ValueError("回复事实引用了未确认的 ModificationFact")
    draft_refs["confirmed_response_facts"] = confirmed.model_dump(mode="json")
    return {
        "phase": "GENERATE_DRAFT",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def generate_draft_node(state: SourceReplyState) -> dict[str, object]:
    draft_refs = _draft(state)
    settings = _ensure_response_settings(draft_refs.get("expression_settings"))
    draft_refs["expression_settings"] = settings
    generated = generate_draft(
        SourceClaim.model_validate(draft_refs["source_claim"]),
        ResponseFacts.model_validate(draft_refs["confirmed_response_facts"]),
        [
            ConfirmedModificationFact.model_validate(item)
            for item in draft_refs["confirmed_modification_facts"]
        ],
        ResponseSettings.model_validate(settings),
    )
    draft_refs["generated_draft"] = generated.model_dump(mode="json")
    return {"phase": "CONSISTENCY_CHECK", "draft_refs": draft_refs}


def consistency_check_node(state: SourceReplyState) -> dict[str, object]:
    draft_refs = _draft(state)
    report = check_consistency(
        SourceClaim.model_validate(draft_refs["source_claim"]),
        ReplyStrategy.model_validate(draft_refs["confirmed_strategy"]),
        ResponseFacts.model_validate(draft_refs["confirmed_response_facts"]),
        ResponseDraft.model_validate(draft_refs["generated_draft"]),
        [
            ConfirmedModificationFact.model_validate(item)
            for item in draft_refs["confirmed_modification_facts"]
        ],
        [
            ApprovedSourceReply.model_validate(item)
            for item in draft_refs.get("other_approved_replies", [])
        ],
    )
    draft_refs["consistency_report"] = report.model_dump(mode="json")
    return {"phase": "PERSIST_AND_REVIEW", "draft_refs": draft_refs}


def review_interaction(state: SourceReplyState) -> PendingInteraction:
    """构造草稿审核交互（interrupt 使用）。"""
    persisted = _draft(state)["persisted_reply"]
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="REVIEW_REPLY_DRAFT",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=UUID(str(state["suggestion_id"])),
        source_id=UUID(str(state["source_id"])),
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="审核回复草稿",
        question="请批准当前草稿，或编辑后再次审核。",
        context={"draft": persisted},
        options=[
            InteractionOption(value="approve", label="批准"),
            InteractionOption(value="edit", label="编辑"),
        ],
        editable_fields=[
            EditableField(
                key="action",
                label="审核操作",
                type=EditableFieldType.SELECT,
                required=True,
                default="approve",
                choices=[
                    InteractionOption(value="approve", label="批准当前草稿"),
                    InteractionOption(value="edit", label="保存编辑并重新审核"),
                ],
            ),
            EditableField(
                key="content",
                label="编辑后的草稿",
                type=EditableFieldType.TEXTAREA,
                required=False,
                default=persisted["content"],
            ),
        ],
        blockers=[],
        resume_action="review_draft",
    )


def review_draft(state: SourceReplyState) -> dict[str, object]:
    interaction = review_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    action = str(payload.get("action", ""))
    if action not in {"approve", "edit"}:
        raise ValueError("草稿审核 action 只能是 approve 或 edit")
    draft_refs = _draft(state)
    draft_refs["review_decision"] = {
        "action": action,
        "content": payload.get("content"),
    }
    return {
        "phase": "PERSIST_REVIEW_DECISION",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def _review_route(state: SourceReplyState) -> Literal["review", "done"]:
    return "review" if state["phase"] == "REVIEW_DRAFT" else "done"


def build_source_reply_graph(
    *,
    stores: ReplyGraphStores | ReplyStore,
    checkpointer,
    store=None,
):
    """组装 SourceReplyGraph。

    Parameters
    ----------
    stores:
        实现 `ReplyStore` 的对象，或带 `.reply` 属性的 stores 聚合。
    checkpointer:
        必须注入的持久化 Checkpointer（测试可用 MemorySaver）。
    store:
        可选 LangGraph Store。
    """
    if checkpointer is None:
        raise ValueError("SourceReplyGraph 必须注入持久化 Checkpointer")

    reply_store: ReplyStore
    if hasattr(stores, "load_reply_context"):
        reply_store = stores  # type: ignore[assignment]
    else:
        reply_store = stores.reply  # type: ignore[attr-defined]

    graph = StateGraph(SourceReplyState)
    graph.add_node("load_context", lambda state: load_context(state, reply_store))
    graph.add_node("check_analysis_ready", check_analysis_ready)
    graph.add_node("block", block_analysis)
    graph.add_node("interpret_claim", interpret_claim_node)
    graph.add_node("recommend_strategy", recommend_strategy_node)
    graph.add_node("confirm_strategy", confirm_strategy)
    graph.add_node("build_response_facts", build_response_facts_node)
    graph.add_node("confirm_facts", confirm_response_facts)
    graph.add_node("generate_draft", generate_draft_node)
    graph.add_node("consistency_check", consistency_check_node)
    graph.add_node(
        "persist_and_review",
        lambda state: persist_and_review(state, reply_store),
    )
    graph.add_node("review_draft", review_draft)
    graph.add_node(
        "persist_review_decision",
        lambda state: persist_review_decision(state, reply_store),
    )

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "check_analysis_ready")
    graph.add_conditional_edges(
        "check_analysis_ready",
        _analysis_route,
        {"blocked": "block", "ready": "interpret_claim"},
    )
    graph.add_edge("block", END)
    graph.add_edge("interpret_claim", "recommend_strategy")
    graph.add_edge("recommend_strategy", "confirm_strategy")
    graph.add_edge("confirm_strategy", "build_response_facts")
    graph.add_edge("build_response_facts", "confirm_facts")
    graph.add_edge("confirm_facts", "generate_draft")
    graph.add_edge("generate_draft", "consistency_check")
    graph.add_edge("consistency_check", "persist_and_review")
    graph.add_edge("persist_and_review", "review_draft")
    graph.add_edge("review_draft", "persist_review_decision")
    graph.add_conditional_edges(
        "persist_review_decision",
        _review_route,
        {"review": "review_draft", "done": END},
    )

    return graph.compile(
        checkpointer=checkpointer,
        store=store,
        name="source_reply_graph",
    )


def get_compiled_reply_graph(*, stores, checkpointer=None, store=None):
    """便捷入口：与 backend `get_compiled_reply_graph` 对齐。"""
    return build_source_reply_graph(
        stores=stores,
        checkpointer=checkpointer,
        store=store,
    )


# 兼容旧测试命名
_response_facts_interaction = response_facts_interaction
_strategy_interaction = strategy_interaction
_review_interaction = review_interaction
