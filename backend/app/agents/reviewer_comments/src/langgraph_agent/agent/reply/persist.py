"""SourceReplyGraph 的回复、草稿与审核落库（经 ReplyStore 端口）。"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID, uuid5

from langgraph_agent.ports.reply_store import ReplyStore
from langgraph_agent.ports.types import (
    ReplyDraftRecord,
    ResultRef,
    SaveReplyDraftResult,
    SaveReviewDecisionResult,
    SourceReplyRecord,
)
from langgraph_agent.schemas.run import GraphRunStatus


def stale_consistency_report(previous: object) -> dict[str, Any]:
    """手改后标记旧一致性报告失效，避免幽灵问题继续展示。

    注意：手改路径当前不会重新跑一致性检查；仅清空旧 issues 并打 stale 标记。
    """
    prev = previous if isinstance(previous, dict) else {}
    return {
        "is_consistent": True,
        "passed": True,
        "issues": [],
        "items": [],
        "cross_source_conflicts": [],
        "reminders": ["用户手改后尚未重新做一致性检查；保存前的旧检查结果已失效"],
        "summary": "用户手改后尚未重新检查，旧问题已清空，避免误导。",
        "stale": True,
        "stale_reason": "USER_EDIT_OR_REOPEN",
        "previous_is_consistent": prev.get("is_consistent", prev.get("passed")),
    }


def _draft_refs_from_records(
    reply: SourceReplyRecord,
    draft: ReplyDraftRecord,
) -> dict[str, Any]:
    return {
        "persisted_reply": {
            "reply_id": str(reply["reply_id"]),
            "draft_id": str(draft["draft_id"]),
            "version_no": draft["version_no"],
            "content": draft["content"],
            "language": draft["language"],
            "consistency_report": draft["consistency_report"],
        }
    }


def _normalize_result_refs(refs: list[ResultRef] | list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in refs:
        ref_type = str(item["type"])
        raw_id = item["id"]
        normalized.append({"type": ref_type, "id": str(raw_id)})
    return normalized


def persist_and_review(
    state: Mapping[str, Any],
    reply_store: ReplyStore,
) -> dict[str, object]:
    """经 ReplyStore 写入 SourceReply 与本次运行的 GENERATED 草稿。"""
    workspace_id = UUID(str(state["workspace_id"]))
    suggestion_id = UUID(str(state["suggestion_id"]))
    source_id = UUID(str(state["source_id"]))
    run_id = UUID(str(state["run_id"]))
    input_version = str(state["input_version"])
    user_id = str(state["user_id"])
    draft_refs = dict(state.get("draft_refs", {}))
    strategy = draft_refs.get("confirmed_strategy")
    response_facts = draft_refs.get("confirmed_response_facts")
    generated_draft = draft_refs.get("generated_draft")
    report = draft_refs.get("consistency_report")
    settings = draft_refs.get("expression_settings")
    if not all(
        isinstance(item, dict)
        for item in (strategy, response_facts, generated_draft, report, settings)
    ):
        raise ValueError("缺少已确认策略、事实、草稿或一致性结果")

    saved: SaveReplyDraftResult = reply_store.save_reply_draft(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        run_id=run_id,
        input_version=input_version,
        user_id=user_id,
        strategy=strategy,
        expression_settings=settings,
        response_facts=response_facts,
        generated_draft=generated_draft,
        consistency_report=report,
    )
    reply = saved["reply"]
    draft = saved["draft"]
    phase = str(saved.get("phase") or "REVIEW_DRAFT")
    return {
        "phase": phase,
        "pending_interaction_id": uuid5(
            run_id, f"REVIEW_DRAFT:{draft['version_no']}"
        ),
        "draft_refs": _draft_refs_from_records(reply, draft),
        "result_refs": _normalize_result_refs(list(saved.get("result_refs") or [])),
        "status": GraphRunStatus.WAITING_USER,
    }


def persist_review_decision(
    state: Mapping[str, Any],
    reply_store: ReplyStore,
) -> dict[str, object]:
    """经 ReplyStore 应用审核决定：approve 或 edit 后继续等待。"""
    workspace_id = UUID(str(state["workspace_id"]))
    run_id = UUID(str(state["run_id"]))
    user_id = str(state["user_id"])
    draft_refs = dict(state.get("draft_refs", {}))
    persisted = draft_refs.get("persisted_reply")
    decision = draft_refs.get("review_decision")
    if not isinstance(persisted, dict) or not isinstance(decision, dict):
        raise ValueError("缺少待审核草稿或审核决定")

    saved: SaveReviewDecisionResult = reply_store.save_review_decision(
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=user_id,
        reply_id=UUID(str(persisted["reply_id"])),
        draft_id=UUID(str(persisted["draft_id"])),
        decision=decision,
    )
    phase = str(saved.get("phase") or "")
    refs = _normalize_result_refs(list(saved.get("result_refs") or []))
    reply = saved["reply"]
    draft = saved["draft"]

    if phase == "SUCCEEDED" or str(decision.get("action")) == "approve":
        return {
            "phase": "SUCCEEDED",
            "pending_interaction_id": None,
            "draft_refs": {
                "synced_sources": list(saved.get("synced_sources") or []),
            },
            "result_refs": refs,
            "status": GraphRunStatus.SUCCEEDED,
            "error_code": None,
        }

    # edit 路径：回到 REVIEW_DRAFT 等待再次审核
    next_version = int(draft["version_no"])
    return {
        "phase": "REVIEW_DRAFT",
        "pending_interaction_id": uuid5(run_id, f"REVIEW_DRAFT:{next_version}"),
        "draft_refs": _draft_refs_from_records(reply, draft),
        "result_refs": refs,
        "status": GraphRunStatus.WAITING_USER,
    }
