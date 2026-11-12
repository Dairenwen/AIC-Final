"""WorkspaceTaskGraph 的 TASK_INIT 分支（经 ports 依赖注入）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from config.settings import get_settings
from langgraph_agent.agent.state import WorkspaceTaskState
from langgraph_agent.agent.workspace_task.extract_node import extract_parties_and_items
from langgraph_agent.agent.workspace_task.manuscript_node import (
    confirm_baseline,
    generate_baseline_cards,
    parse_manuscript,
    persist_baseline,
)
from langgraph_agent.agent.workspace_task.persist import persist_and_ready
from langgraph_agent.agent.workspace_task.relation_node import (
    apply_relation_confirmation as _apply_relation_confirmation,
    detect_relation_type as _relation_type,
    detect_relations,
    relation_terms as _relation_terms,
)
from langgraph_agent.agent.workspace_task.split_node import split_review_points
from langgraph_agent.ports.manuscript_store import ManuscriptStore
from langgraph_agent.ports.workspace_store import WorkspaceStore
from langgraph_agent.schemas import (
    EditableField,
    EditableFieldType,
    GraphRunStatus,
    InteractionOption,
    PendingInteraction,
    ResumeCommand,
    WorkspaceMode,
)


@dataclass(frozen=True)
class WorkspaceTaskStores:
    """TASK_INIT 图依赖的存储端口集合。"""

    workspace: WorkspaceStore
    manuscript: ManuscriptStore | None = None


def _thread_id(state: WorkspaceTaskState) -> str:
    return state["thread_id"]


def _interaction_id(state: WorkspaceTaskState, interaction_type: str) -> UUID:
    return uuid5(UUID(str(state["run_id"])), interaction_type)


def _draft(state: WorkspaceTaskState) -> dict[str, Any]:
    return dict(state.get("draft_refs", {}))


def load_inputs(
    state: WorkspaceTaskState,
    workspace_store: WorkspaceStore,
) -> dict[str, object]:
    """只读加载 Workspace 的当前 ReviewInput 与对应 ReviewParty。"""
    if state["run_scope"] != "TASK_INIT":
        raise ValueError("本轮图只支持 TASK_INIT 分支")

    workspace_id = UUID(str(state["workspace_id"]))
    workspace = workspace_store.get_workspace(workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace 不存在：{workspace_id}")
    if workspace["user_id"] != state["user_id"]:
        raise ValueError("Workspace 不属于当前用户")

    current_inputs = workspace_store.list_current_review_inputs(workspace_id)
    loaded_inputs: list[dict[str, object]] = []
    for review_input in current_inputs:
        if review_input.get("raw_text") is None:
            raise ValueError(
                f"ReviewInput 尚未提供可读取文本：{review_input['review_input_id']}"
            )
        loaded_inputs.append(
            {
                "review_input_id": str(review_input["review_input_id"]),
                "party_id": str(review_input["party_id"]),
                "role": review_input["role"],
                "display_name": review_input["display_name"],
                "raw_label": review_input["raw_label"],
                "raw_text": review_input["raw_text"],
                "language": review_input.get("language"),
            }
        )
    if not loaded_inputs:
        raise ValueError("Workspace 没有当前生效的 ReviewInput")

    draft_refs = _draft(state)
    draft_refs["loaded_inputs"] = loaded_inputs
    return {
        "phase": "EXTRACT_PARTIES_AND_ITEMS",
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def extract_parties_and_items_node(
    state: WorkspaceTaskState,
) -> dict[str, object]:
    """运行纯逻辑抽取，将结果继续暂存在 draft_refs。"""
    draft_refs = _draft(state)
    loaded_inputs = draft_refs.get("loaded_inputs")
    if not isinstance(loaded_inputs, list):
        raise ValueError("load_inputs 未提供审稿输入")
    extracted = extract_parties_and_items(loaded_inputs)
    draft_refs["extracted"] = extracted.model_dump(mode="json")
    return {
        "phase": "SPLIT_SUGGESTIONS",
        "draft_refs": draft_refs,
    }


def _proposals_from_item(
    item_index: int,
    item: dict[str, Any],
    result: Any,
) -> list[dict[str, Any]]:
    """把单条 split 结果转成建议提案列表。"""
    proposals: list[dict[str, Any]] = []
    for point in result.review_points:
        proposal_id = f"S-{item_index:03d}-{point.point_id}"
        localized_claim = (
            point.explicit_request
            or point.implicit_concern
            or point.atomic_concern
        )
        proposals.append(
            {
                "proposal_id": proposal_id,
                "canonical_text": point.atomic_concern,
                "merge_group_key": None,
                "conflict_group_key": None,
                "sources": [
                    {
                        "party_id": item["party_id"],
                        "review_input_id": item["review_input_id"],
                        "display_name": item["display_name"],
                        "role": item["role"],
                        "excerpt": point.source_quote,
                        "localized_claim": localized_claim,
                        "stance": (
                            "REQUEST"
                            if point.explicit_request is not None
                            else "CONCERN"
                        ),
                        "span_refs": {
                            "original_item_id": item["original_item_id"],
                            "original_item_number": item.get("original_item_number"),
                            "source_order": item["source_order"],
                        },
                    }
                ],
            }
        )
    return proposals


def _split_item_to_proposals(
    item_index: int,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """供线程池调用：拆分单条原文并生成提案。"""
    result = split_review_points(
        item["original_text"], language=item.get("language")
    )
    return _proposals_from_item(item_index, item, result)


def split_suggestions(
    state: WorkspaceTaskState,
    *,
    max_workers: int | None = None,
) -> dict[str, object]:
    """复用 split_review_points 生成尚未落库的建议提案。

    多条原文条目默认有限并发（SPLIT_MAX_WORKERS，默认 3），
    汇总时按 item_index 保序，保证 proposal_id 稳定。
    """
    draft_refs = _draft(state)
    extracted = draft_refs.get("extracted")
    if not isinstance(extracted, dict) or not isinstance(
        extracted.get("items"), list
    ):
        raise ValueError("extract_parties_and_items 未提供原始条目")

    items = extracted["items"]
    if max_workers is None:
        configured = int(get_settings().SPLIT_MAX_WORKERS)
    else:
        configured = int(max_workers)
    if configured < 1:
        raise ValueError("split max_workers 必须 >= 1")
    workers = min(configured, max(len(items), 1))

    proposals: list[dict[str, Any]] = []
    if not items:
        pass
    elif workers == 1 or len(items) == 1:
        for item_index, item in enumerate(items, start=1):
            proposals.extend(_split_item_to_proposals(item_index, item))
    else:
        # 并发提交；按提交顺序取 result，保证 S-001 / S-002 … 稳定。
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_split_item_to_proposals, item_index, item)
                for item_index, item in enumerate(items, start=1)
            ]
            for future in futures:
                proposals.extend(future.result())

    interaction_id = _interaction_id(state, "CONFIRM_SUGGESTIONS")
    draft_refs["suggestion_proposals"] = proposals
    return {
        "phase": "CONFIRM_SUGGESTIONS",
        "pending_interaction_id": interaction_id,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.WAITING_USER,
    }


def _suggestions_interaction(state: WorkspaceTaskState) -> PendingInteraction:
    draft_refs = _draft(state)
    proposals = draft_refs.get("suggestion_proposals", [])
    choices = [
        InteractionOption(
            value=proposal["proposal_id"],
            label=proposal["canonical_text"],
        )
        for proposal in proposals
    ]
    fields = []
    if choices:
        fields.append(
            EditableField(
                key="selected_suggestion_ids",
                label="保留的建议",
                type=EditableFieldType.MULTISELECT,
                required=True,
                default=[choice.value for choice in choices],
                choices=choices,
                help_text="可取消不需要进入后续流程的建议。",
            )
        )
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_SUGGESTIONS",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=None,
        source_id=None,
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认建议清单",
        question="请确认拆分后的独立建议；可选择保留项或提交编辑后的建议文本。",
        context={"suggestions": proposals},
        options=choices,
        editable_fields=fields,
        blockers=[],
        resume_action="confirm_suggestions",
    )


def _resume_payload(
    value: object,
    interaction: PendingInteraction,
) -> dict[str, Any]:
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


def _apply_suggestion_confirmation(
    proposals: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    if payload.get("approved") is False:
        raise ValueError("建议清单未确认，图不能继续")
    by_id = {item["proposal_id"]: item for item in proposals}
    selected = payload.get("selected_suggestion_ids")
    if selected is None:
        selected_ids = list(by_id)
    elif isinstance(selected, list):
        selected_ids = [str(item) for item in selected]
    else:
        raise ValueError("selected_suggestion_ids 必须是数组")

    edited = payload.get("suggestions", [])
    if not isinstance(edited, list):
        raise ValueError("suggestions 必须是数组")
    text_overrides: dict[str, str] = {}
    for item in edited:
        if not isinstance(item, dict):
            raise ValueError("编辑后的建议必须是 JSON 对象")
        proposal_id = str(item.get("proposal_id", ""))
        if proposal_id not in by_id:
            raise ValueError(f"未知建议提案：{proposal_id}")
        if item.get("accepted") is False:
            selected_ids = [value for value in selected_ids if value != proposal_id]
        if "canonical_text" in item:
            canonical_text = str(item["canonical_text"]).strip()
            if not canonical_text:
                raise ValueError("canonical_text 不能为空")
            text_overrides[proposal_id] = canonical_text

    confirmed: list[dict[str, Any]] = []
    for proposal_id in selected_ids:
        if proposal_id not in by_id:
            raise ValueError(f"未知建议提案：{proposal_id}")
        proposal = dict(by_id[proposal_id])
        if proposal_id in text_overrides:
            proposal["canonical_text"] = text_overrides[proposal_id]
        confirmed.append(proposal)
    return confirmed


def confirm_suggestions(state: WorkspaceTaskState) -> dict[str, object]:
    """在 interrupt 恢复后确认或编辑建议提案，不执行数据库写入。"""
    interaction = _suggestions_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    draft_refs = _draft(state)
    proposals = draft_refs.get("suggestion_proposals", [])
    draft_refs["confirmed_suggestions"] = _apply_suggestion_confirmation(
        proposals, payload
    )
    return {
        "phase": "DETECT_RELATIONS",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def _relations_interaction(state: WorkspaceTaskState) -> PendingInteraction:
    draft_refs = _draft(state)
    relations = draft_refs.get("relation_proposals", [])
    choices = [
        InteractionOption(
            value=relation["relation_id"],
            label=f"{relation['type']}: {' / '.join(relation['suggestion_ids'])}",
            description=relation["explanation"],
        )
        for relation in relations
    ]
    fields = []
    if choices:
        fields.append(
            EditableField(
                key="approved_relation_ids",
                label="确认的关系",
                type=EditableFieldType.MULTISELECT,
                required=False,
                default=[choice.value for choice in choices],
                choices=choices,
            )
        )
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_RELATIONS",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=None,
        source_id=None,
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认意见关系",
        question="请确认建议之间的共享、相关或冲突关系。",
        context={"relations": relations},
        options=choices,
        editable_fields=fields,
        blockers=[],
        resume_action="confirm_relations",
    )


def confirm_relations(state: WorkspaceTaskState) -> dict[str, object]:
    """在 interrupt 恢复后确认关系，并组装最终落库提案。"""
    interaction = _relations_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    draft_refs = _draft(state)
    persistable, confirmed_relations = _apply_relation_confirmation(
        draft_refs.get("confirmed_suggestions", []),
        draft_refs.get("relation_proposals", []),
        payload,
    )
    draft_refs["confirmed_relations"] = confirmed_relations
    draft_refs["persist_suggestions"] = persistable
    return {
        "phase": "PERSIST_AND_READY",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def route_by_mode(state: WorkspaceTaskState) -> str:
    """按 mode 选择入口：SLOW 走论文基线子链，FAST 直达 load_inputs。"""
    mode = state["mode"]
    if mode in (WorkspaceMode.SLOW, WorkspaceMode.SLOW.value):
        return "parse_manuscript"
    return "load_inputs"


def build_workspace_task_graph(
    *,
    stores: WorkspaceTaskStores | None = None,
    workspace_store: WorkspaceStore | None = None,
    manuscript_store: ManuscriptStore | None = None,
    checkpointer=None,
    store=None,
    split_max_workers: int | None = None,
):
    """组装并编译 TASK_INIT 图：FAST 7 节点 + SLOW 基线子链。

    参数：
    - stores: 推荐注入方式，包含 workspace / manuscript 端口
    - workspace_store / manuscript_store: 等价的拆分注入（与 stores 二选一）
    - checkpointer: 默认 InMemorySaver；生产由 memory/C1 注入 PostgresSaver
    - store: LangGraph Store（可选）
    - split_max_workers: 覆盖配置中的拆分并发
    """
    if stores is not None:
        ws = stores.workspace
        ms = stores.manuscript
    else:
        if workspace_store is None:
            raise ValueError("必须提供 stores 或 workspace_store")
        ws = workspace_store
        ms = manuscript_store

    graph = StateGraph(WorkspaceTaskState)

    def _require_manuscript() -> ManuscriptStore:
        if ms is None:
            raise ValueError("SLOW 模式需要注入 manuscript_store")
        return ms

    # SLOW 子链：解析 → 卡片候选 → 确认(interrupt) → 落库
    graph.add_node(
        "parse_manuscript",
        lambda state: parse_manuscript(state, _require_manuscript()),
    )
    graph.add_node(
        "generate_baseline_cards",
        lambda state: generate_baseline_cards(state, _require_manuscript()),
    )
    graph.add_node("confirm_baseline", confirm_baseline)
    graph.add_node(
        "persist_baseline",
        lambda state: persist_baseline(state, _require_manuscript()),
    )

    # FAST 链（及 SLOW 汇入后）7 节点
    graph.add_node(
        "load_inputs",
        lambda state: load_inputs(state, ws),
    )
    graph.add_node("extract_parties_and_items", extract_parties_and_items_node)

    def _split_node(state: WorkspaceTaskState) -> dict[str, object]:
        return split_suggestions(state, max_workers=split_max_workers)

    graph.add_node("split_suggestions", _split_node)
    graph.add_node("confirm_suggestions", confirm_suggestions)
    graph.add_node("detect_relations", detect_relations)
    graph.add_node("confirm_relations", confirm_relations)
    graph.add_node(
        "persist_and_ready",
        lambda state: persist_and_ready(state, ws),
    )

    graph.add_conditional_edges(
        START,
        route_by_mode,
        {
            "parse_manuscript": "parse_manuscript",
            "load_inputs": "load_inputs",
        },
    )
    graph.add_edge("parse_manuscript", "generate_baseline_cards")
    graph.add_edge("generate_baseline_cards", "confirm_baseline")
    graph.add_edge("confirm_baseline", "persist_baseline")
    graph.add_edge("persist_baseline", "load_inputs")

    graph.add_edge("load_inputs", "extract_parties_and_items")
    graph.add_edge("extract_parties_and_items", "split_suggestions")
    graph.add_edge("split_suggestions", "confirm_suggestions")
    graph.add_edge("confirm_suggestions", "detect_relations")
    graph.add_edge("detect_relations", "confirm_relations")
    graph.add_edge("confirm_relations", "persist_and_ready")
    graph.add_edge("persist_and_ready", END)

    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        store=store,
        name="workspace_task_graph",
    )


def get_compiled_graph(
    *,
    stores: WorkspaceTaskStores | None = None,
    workspace_store: WorkspaceStore | None = None,
    manuscript_store: ManuscriptStore | None = None,
    checkpointer=None,
    store=None,
    split_max_workers: int | None = None,
):
    """供服务层稳定注入 Checkpointer 与 Store 的入口。"""
    return build_workspace_task_graph(
        stores=stores,
        workspace_store=workspace_store,
        manuscript_store=manuscript_store,
        checkpointer=checkpointer,
        store=store,
        split_max_workers=split_max_workers,
    )


# 兼容既有测试路径的 re-export。
__all__ = [
    "WorkspaceTaskStores",
    "build_workspace_task_graph",
    "get_compiled_graph",
    "load_inputs",
    "extract_parties_and_items_node",
    "split_suggestions",
    "confirm_suggestions",
    "confirm_relations",
    "route_by_mode",
    "_relation_type",
    "_relation_terms",
    "_apply_relation_confirmation",
]
