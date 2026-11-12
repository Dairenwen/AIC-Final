"""WorkspaceTaskGraph 已确认建议的落库（经 WorkspaceStore 端口）。"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from langgraph_agent.agent.state import WorkspaceTaskState
from langgraph_agent.ports.workspace_store import WorkspaceStore
from langgraph_agent.schemas import GraphRunStatus

# 合并组内文本高度相似时，保留原始顺序的第一条非空。
_CANONICAL_SIMILARITY = 0.85


def choose_canonical_text(texts: list[str]) -> str:
    """合并组 canonical_text：优先更长更完整；高度相似则取第一条非空。

    不调用 LLM，仅用长度与字符相似度做可解释选择。
    """
    cleaned = [str(text).strip() for text in texts if str(text or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]

    longest = max(cleaned, key=len)
    highly_similar = all(
        SequenceMatcher(None, item.casefold(), longest.casefold()).ratio()
        >= _CANONICAL_SIMILARITY
        for item in cleaned
    )
    if highly_similar:
        return cleaned[0]
    return longest


def group_confirmed_suggestions(
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 merge_group_key 合并建议，保留全部 sources。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for suggestion in suggestions:
        merge_key = suggestion.get("merge_group_key")
        group_key = str(merge_key or suggestion["proposal_id"])
        grouped[group_key].append(suggestion)

    persistable: list[dict[str, Any]] = []
    for group in grouped.values():
        first = group[0]
        sources = [source for item in group for source in item.get("sources", [])]
        persistable.append(
            {
                "canonical_text": choose_canonical_text(
                    [str(item.get("canonical_text") or "") for item in group]
                ),
                "merge_group_key": first.get("merge_group_key"),
                "conflict_group_key": next(
                    (
                        item.get("conflict_group_key")
                        for item in group
                        if item.get("conflict_group_key")
                    ),
                    first.get("conflict_group_key"),
                ),
                "sources": sources,
            }
        )
    return persistable


# 兼容 backend 旧私有名。
_group_confirmed_suggestions = group_confirmed_suggestions


def persist_and_ready(
    state: WorkspaceTaskState,
    workspace_store: WorkspaceStore,
) -> dict[str, object]:
    """经 WorkspaceStore 写入确认后的建议、来源，并将 Workspace 置为 ACTIVE。"""
    draft_refs = dict(state.get("draft_refs", {}))
    confirmed = draft_refs.get("persist_suggestions")
    if not isinstance(confirmed, list):
        raise ValueError("缺少已确认、可落库的建议提案")

    workspace_id = UUID(str(state["workspace_id"]))
    # 合并分组逻辑由 WorkspaceStore 实现（对齐 backend persist_and_ready）；
    # 节点层仅转发已确认提案。纯函数 group_confirmed_suggestions 供适配器/单测复用。
    persisted = workspace_store.persist_task_init_result(
        workspace_id=workspace_id,
        input_version=state["input_version"],
        confirmed_suggestions=confirmed,
    )
    # 状态机内只保留可 JSON/msgpack 的纯 dict，AgentResult 边界再升格为 ResultReference。
    result_refs = [
        {"type": str(item["type"]), "id": str(item["id"])}
        for item in persisted.get("result_refs", [])
    ]
    return {
        "phase": "READY",
        "pending_interaction_id": None,
        "draft_refs": {},
        "result_refs": result_refs,
        "status": GraphRunStatus.SUCCEEDED,
        "error_code": None,
    }
