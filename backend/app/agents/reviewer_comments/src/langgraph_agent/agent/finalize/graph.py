"""WorkspaceTaskGraph 的独立 FINALIZE 分支编排。

来源：backend/app/graphs/finalize_graph.py

差异（仅依赖注入）：
- 原 `session_factory` → `stores["finalize"]`（FinalizeStore Protocol）
- 导出文件生成复用 `langgraph_agent.tools.export_files`
- 状态枚举用 `langgraph_agent.schemas.GraphRunStatus`
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, TypedDict
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph

from langgraph_agent.agent.finalize.consistency import check_cross_source_consistency
from langgraph_agent.agent.finalize.export import (
    enrich_output_files_for_summary,
    generate_export_files,
)
from langgraph_agent.ports.finalize_store import FinalizeStore
from langgraph_agent.schemas import GraphRunStatus


class FinalizeState(TypedDict):
    workspace_id: UUID
    user_id: str
    run_id: UUID
    run_scope: Literal["FINALIZE"]
    input_version: str
    phase: str
    draft_refs: dict[str, Any]
    result_refs: list[dict[str, str]]
    status: GraphRunStatus
    error_code: str | None


_PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{[^{}]+\}\}"),
    re.compile(r"\[(?:TODO|TBD|PLACEHOLDER|待补|待确认)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b", re.IGNORECASE),
    re.compile(r"<[^<>]*(?:待补|待确认|placeholder)[^<>]*>", re.IGNORECASE),
)
_COMPLETION_PATTERN = re.compile(
    r"(?:已经|已在|已增加|已补充|已完成|已修改|已修订|"
    r"\bwe have\b|\bhas been\b|\bhave been\b|\bwas added\b|"
    r"\bwere added\b|\bcompleted\b)",
    re.IGNORECASE,
)


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_finalize_store(stores: Mapping[str, Any]) -> FinalizeStore:
    store = stores.get("finalize")
    if store is None:
        raise KeyError(
            "build_finalize_graph 需要 stores['finalize']（FinalizeStore 实现）"
        )
    return store  # type: ignore[return-value]


def compute_finalize_input_version(context: dict[str, Any]) -> str:
    """以当前 source、回复、事实和表达设置快照生成 finalize 幂等版本。"""
    version_source = {
        "workspace_id": context["workspace_id"],
        "global_settings": context["global_settings"],
        "suggestions": context["suggestions"],
        "sources": context["sources"],
    }
    return f"finalize:v1:{_stable_hash(version_source)}"


def _fact_ids(response_facts: object) -> set[str]:
    result: set[str] = set()
    if isinstance(response_facts, dict):
        direct = response_facts.get("linked_fact_ids", [])
        items = response_facts.get("fact_items", [])
        candidates = list(direct) if isinstance(direct, list) else []
        if isinstance(items, list):
            candidates.extend(items)
    elif isinstance(response_facts, list):
        candidates = response_facts
    else:
        candidates = []
    for item in candidates:
        if isinstance(item, str):
            try:
                result.add(str(UUID(item)))
            except ValueError:
                continue
        elif isinstance(item, dict):
            fact_id = item.get("fact_id")
            if fact_id is not None:
                try:
                    result.add(str(UUID(str(fact_id))))
                except ValueError:
                    pass
            linked = item.get("linked_fact_ids")
            if isinstance(linked, list):
                for value in linked:
                    try:
                        result.add(str(UUID(str(value))))
                    except ValueError:
                        continue
    return result


def _unresolved_items(response_facts: object) -> list[str]:
    unresolved: list[str] = []
    if isinstance(response_facts, dict):
        direct = response_facts.get("unresolved_items", [])
        if isinstance(direct, list):
            unresolved.extend(
                str(value) for value in direct if str(value).strip()
            )
        items = response_facts.get("fact_items", [])
        candidates = items if isinstance(items, list) else []
    elif isinstance(response_facts, list):
        candidates = response_facts
    else:
        candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        values = item.get("unresolved_items")
        if isinstance(values, list):
            unresolved.extend(str(value) for value in values if str(value).strip())
    return unresolved


def _block(
    code: str,
    reason: str,
    action: str,
    *,
    source_id: str | None = None,
    suggestion_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "source_id": source_id,
        "suggestion_id": suggestion_id,
        "reason": reason,
        "action": action,
    }


def build_finalize_validation(context: dict[str, Any]) -> dict[str, Any]:
    """执行第 9.3 节完整性、事实与跨来源一致性检查。"""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    sources = context["sources"]
    if not sources:
        blockers.append(
            _block(
                "NO_ACTIVE_SOURCES",
                "Workspace 没有可汇总的有效审稿来源",
                "先完成意见拆分与确认",
            )
        )

    facts = {
        fact["fact_id"]: fact
        for suggestion in context["suggestions"]
        for fact in suggestion["modification_facts"]
    }
    replies_by_suggestion: dict[str, list[dict[str, Any]]] = {}

    for source in sources:
        source_id = source["source_id"]
        suggestion_id = source["suggestion_id"]
        reply = source.get("reply")
        if not isinstance(reply, dict):
            blockers.append(
                _block(
                    "MISSING_REPLY",
                    "该来源尚未生成回复",
                    "进入来源回复页生成并审核草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
            continue

        replies_by_suggestion.setdefault(suggestion_id, []).append(
            {"source_id": source_id, **reply}
        )
        if not reply.get("strategy"):
            blockers.append(
                _block(
                    "MISSING_STRATEGY",
                    "该来源尚未确定回复策略",
                    "确认回复策略后重新生成草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
        if reply.get("status") == "STALE":
            blockers.append(
                _block(
                    "STALE_REPLY",
                    "来源回复已因上游事实变化而过期",
                    "重新生成并审核回复",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
        elif reply.get("status") != "APPROVED":
            blockers.append(
                _block(
                    "REPLY_NOT_APPROVED",
                    "来源回复尚未审核通过",
                    "审核并接受当前回复",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )

        draft = reply.get("current_draft")
        if not isinstance(draft, dict):
            blockers.append(
                _block(
                    "MISSING_DRAFT",
                    "来源回复缺少当前草稿",
                    "重新生成来源回复草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
            continue
        if draft.get("status") == "STALE":
            blockers.append(
                _block(
                    "STALE_DRAFT",
                    "当前草稿已经过期",
                    "根据最新事实重新生成并审核草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
        elif draft.get("status") != "APPROVED":
            blockers.append(
                _block(
                    "DRAFT_NOT_APPROVED",
                    "当前草稿尚未被用户接受",
                    "审核并接受当前草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )

        content = str(draft.get("content", ""))
        if any(pattern.search(content) for pattern in _PLACEHOLDER_PATTERNS):
            blockers.append(
                _block(
                    "UNRESOLVED_PLACEHOLDER",
                    "当前草稿仍包含占位符或待确认内容",
                    "填写真实内容并重新审核草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
        unresolved = _unresolved_items(reply.get("response_facts"))
        if unresolved:
            blockers.append(
                _block(
                    "UNRESOLVED_RESPONSE_FACTS",
                    "回复事实仍有未解决项：" + "；".join(unresolved),
                    "补齐并确认回复事实",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )

        for fact_id in sorted(_fact_ids(reply.get("response_facts"))):
            fact = facts.get(fact_id)
            if fact is None or fact["status"] != "CONFIRMED":
                blockers.append(
                    _block(
                        "UNCONFIRMED_FACT",
                        f"草稿引用的修改事实 {fact_id} 不是当前已确认事实",
                        "重新确认事实并生成草稿",
                        source_id=source_id,
                        suggestion_id=suggestion_id,
                    )
                )
            elif fact["action_type"] == "DEFER" and _COMPLETION_PATTERN.search(
                content
            ):
                blockers.append(
                    _block(
                        "DEFERRED_FACT_AS_COMPLETED",
                        "草稿将延期或计划中的动作表述为已经完成",
                        "改为计划式表述或完成该动作后重新确认",
                        source_id=source_id,
                        suggestion_id=suggestion_id,
                    )
                )

        report = draft.get("consistency_report")
        if isinstance(report, dict):
            issues = report.get("issues", [])
            reminders = report.get("reminders", [])
        elif isinstance(report, list):
            issues = report
            reminders = []
        else:
            issues = []
            reminders = []
        # 人审优先于机器一致性残留：reply+draft 均已批准时，
        # 单草稿 consistency issues 默认降为 warnings，不阻断导出。
        # 跨来源冲突仍在下方单独阻断（真风险）。
        human_approved = (
            reply.get("status") == "APPROVED"
            and draft.get("status") == "APPROVED"
        )
        for issue in issues:
            description = (
                str(issue.get("description", "草稿一致性检查未通过"))
                if isinstance(issue, dict)
                else str(issue)
            )
            if human_approved:
                warnings.append(
                    {
                        "code": "DRAFT_CONSISTENCY_ISSUE",
                        "source_id": source_id,
                        "suggestion_id": suggestion_id,
                        "message": description,
                        "reason": description,
                        "action": "已批准内容可导出；如需可在后续版本修订",
                    }
                )
                continue
            blockers.append(
                _block(
                    "DRAFT_CONSISTENCY_ISSUE",
                    description,
                    "处理一致性问题后重新审核草稿",
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                )
            )
        for reminder in reminders:
            warnings.append(
                {
                    "code": "DRAFT_REMINDER",
                    "source_id": source_id,
                    "suggestion_id": suggestion_id,
                    "message": str(reminder),
                }
            )

    cross_source_conflicts = []
    for suggestion_id, replies in replies_by_suggestion.items():
        conflicts = check_cross_source_consistency(suggestion_id, replies)
        cross_source_conflicts.extend(conflicts)
        for conflict in conflicts:
            source_ids = conflict.get("source_ids") or [None]
            for source_id in source_ids:
                blockers.append(
                    _block(
                        "CROSS_SOURCE_CONFLICT",
                        conflict["description"],
                        "统一同一建议下各来源回复的事实表述",
                        source_id=source_id,
                        suggestion_id=suggestion_id,
                    )
                )

    unique_blockers: list[dict[str, Any]] = []
    seen_blockers: set[str] = set()
    for blocker in blockers:
        signature = _stable_hash(blocker)
        if signature not in seen_blockers:
            seen_blockers.add(signature)
            unique_blockers.append(blocker)
    return {
        "blocked": bool(unique_blockers),
        "block_list": unique_blockers,
        "warnings": warnings,
        "cross_source_conflicts": cross_source_conflicts,
    }


def load_approved(
    state: FinalizeState, store: FinalizeStore
) -> dict[str, Any]:
    if state["run_scope"] != "FINALIZE":
        raise ValueError("finalize_graph 只支持 FINALIZE run_scope")
    context = store.load_finalize_context(state["workspace_id"])
    if context["user_id"] != state["user_id"]:
        raise ValueError("Workspace 不属于当前用户")
    return {
        "phase": "CONSISTENCY_CHECK",
        "draft_refs": {"context": dict(context)},
        "status": GraphRunStatus.RUNNING,
    }


def consistency_check(state: FinalizeState) -> dict[str, Any]:
    context = state["draft_refs"].get("context")
    if not isinstance(context, dict):
        raise ValueError("load_approved 未提供 finalize context")
    draft_refs = dict(state["draft_refs"])
    draft_refs["validation"] = build_finalize_validation(context)
    return {"phase": "ROUTE_FINALIZE", "draft_refs": draft_refs}


def _route_after_check(state: FinalizeState) -> Literal["blocked", "export"]:
    validation = state["draft_refs"].get("validation")
    if not isinstance(validation, dict):
        raise ValueError("consistency_check 未提供 validation")
    return "blocked" if validation.get("blocked") else "export"


def build_block_list(state: FinalizeState) -> dict[str, Any]:
    validation = state["draft_refs"]["validation"]
    block_list_id = uuid5(
        UUID(str(state["workspace_id"])),
        "finalize:block-list:" + _stable_hash(validation["block_list"]),
    )
    draft_refs = dict(state["draft_refs"])
    draft_refs["final_result"] = validation
    return {
        "phase": "BLOCKED",
        "draft_refs": draft_refs,
        "result_refs": [
            {"type": "finalize_block_list", "id": str(block_list_id)}
        ],
        "status": GraphRunStatus.SUCCEEDED,
    }


def create_export_snapshot(state: FinalizeState) -> dict[str, Any]:
    context = state["draft_refs"]["context"]
    validation = state["draft_refs"]["validation"]
    content = {
        "workspace_id": context["workspace_id"],
        "included_source_ids": [
            source["source_id"] for source in context["sources"]
        ],
        "accepted_draft_version_ids": [
            source["reply"]["current_draft"]["draft_id"]
            for source in context["sources"]
        ],
        "revision_action_version_ids": [
            fact["fact_id"]
            for suggestion in context["suggestions"]
            for fact in suggestion["modification_facts"]
            if fact["status"] == "CONFIRMED"
        ],
        "global_expression_settings": context["global_settings"],
        "validation_result": validation,
        "internal_revision_items": context["internal_revision_items"],
        "external_replies": context["external_replies"],
    }
    content_hash = _stable_hash(content)
    snapshot_id = uuid5(
        UUID(str(state["workspace_id"])),
        "finalize:export-snapshot:" + content_hash,
    )
    snapshot = {
        "export_snapshot_id": str(snapshot_id),
        "workspace_id": context["workspace_id"],
        "workspace_title": context["workspace_title"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": state["user_id"],
        **content,
        "acknowledged_warnings": [],
        "output_files": [],
        "content_hash": content_hash,
    }
    draft_refs = dict(state["draft_refs"])
    draft_refs["export_snapshot"] = snapshot
    return {"phase": "GENERATE_FINAL", "draft_refs": draft_refs}


def generate_final(
    state: FinalizeState, store: FinalizeStore
) -> dict[str, Any]:
    snapshot = dict(state["draft_refs"]["export_snapshot"])
    snapshot_id = UUID(snapshot["export_snapshot_id"])
    existing = store.load_export_snapshot(snapshot_id)
    if existing is not None:
        snapshot.update(dict(existing))
    else:
        snapshot["output_files"] = generate_export_files(snapshot)
        persisted = store.save_export_snapshot(
            workspace_id=UUID(str(snapshot["workspace_id"])),
            snapshot_id=snapshot_id,
            snapshot=snapshot,
            actor_user_id=state["user_id"],
        )
        snapshot.update(dict(persisted))

    draft_refs = dict(state["draft_refs"])
    draft_refs["export_snapshot"] = snapshot
    draft_refs["final_result"] = {
        "blocked": False,
        "block_list": [],
        "warnings": snapshot["validation_result"]["warnings"],
        "export_snapshot": snapshot,
    }
    return {
        "phase": "SUCCEEDED",
        "draft_refs": draft_refs,
        "result_refs": [
            {"type": "export_snapshot", "id": str(snapshot_id)}
        ],
        "status": GraphRunStatus.SUCCEEDED,
    }


def build_summary_data(
    store: FinalizeStore, workspace_id: UUID
) -> dict[str, Any]:
    """只读构建当前汇总，不调用图、不创建快照或文件。"""
    context = store.load_finalize_context(workspace_id)
    validation = build_finalize_validation(dict(context))
    raw_latest = store.load_latest_export_snapshot(workspace_id)
    latest_snapshot = enrich_output_files_for_summary(
        workspace_id,
        dict(raw_latest) if raw_latest is not None else None,
    )
    completed = sum(
        1
        for reply in context["external_replies"]
        if reply["reply_status"] == "APPROVED"
        and reply["draft_status"] == "APPROVED"
    )
    return {
        "workspace_id": context["workspace_id"],
        "workspace_title": context["workspace_title"],
        "completion": {
            "total_sources": len(context["sources"]),
            "completed_sources": completed,
            "blocked_sources": len(
                {
                    item["source_id"]
                    for item in validation["block_list"]
                    if item["source_id"] is not None
                }
            ),
        },
        "internal_revision_items": context["internal_revision_items"],
        "external_replies": context["external_replies"],
        "validation": validation,
        "blocked": validation["blocked"],
        "block_list": validation["block_list"],
        "warnings": validation.get("warnings", []),
        "latest_export_snapshot": latest_snapshot,
    }


def build_finalize_graph(*, stores: Mapping[str, Any]):
    """组装第 19.4 节规定的 FINALIZE 节点链。

    Parameters
    ----------
    stores:
        至少包含 ``finalize: FinalizeStore``。
    """
    store = _resolve_finalize_store(stores)

    graph = StateGraph(FinalizeState)
    graph.add_node(
        "load_approved", lambda state: load_approved(state, store)
    )
    graph.add_node("consistency_check", consistency_check)
    graph.add_node("build_block_list", build_block_list)
    graph.add_node("create_export_snapshot", create_export_snapshot)
    graph.add_node(
        "generate_final", lambda state: generate_final(state, store)
    )
    graph.add_edge(START, "load_approved")
    graph.add_edge("load_approved", "consistency_check")
    graph.add_conditional_edges(
        "consistency_check",
        _route_after_check,
        {"blocked": "build_block_list", "export": "create_export_snapshot"},
    )
    graph.add_edge("build_block_list", END)
    graph.add_edge("create_export_snapshot", "generate_final")
    graph.add_edge("generate_final", END)
    return graph.compile(name="finalize_graph")


__all__ = [
    "FinalizeState",
    "build_finalize_graph",
    "build_finalize_validation",
    "build_summary_data",
    "compute_finalize_input_version",
    "consistency_check",
    "create_export_snapshot",
    "generate_final",
    "load_approved",
]
