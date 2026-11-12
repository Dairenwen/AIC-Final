"""SuggestionAnalysisGraph 已确认结果的落库（走 AnalysisStore 端口）。"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from langgraph_agent.ports.analysis_store import AnalysisStore
from langgraph_agent.schemas.run import GraphRunStatus


def persist_analysis(
    state: Mapping[str, Any],
    analysis_store: AnalysisStore,
) -> dict[str, object]:
    """以 run_id 为幂等闸门，经 AnalysisStore 单事务写分析与事实。"""
    run_id = UUID(str(state["run_id"]))
    workspace_id = UUID(str(state["workspace_id"]))
    suggestion_id = UUID(str(state["suggestion_id"]))
    input_version = str(state["input_version"])
    user_id = str(state["user_id"])
    draft_refs = dict(state.get("draft_refs", {}))

    classification = draft_refs.get("classification")
    evidence = draft_refs.get("evidence")
    priority = draft_refs.get("priority")
    actions = draft_refs.get("action_recommendations")
    fact_proposals = draft_refs.get("confirmed_fact_proposals")
    if not isinstance(classification, dict):
        raise ValueError("缺少分类结果")
    if not isinstance(evidence, dict):
        raise ValueError("缺少证据判断")
    if not isinstance(priority, dict):
        raise ValueError("缺少优先级结果")
    if not isinstance(actions, dict):
        raise ValueError("缺少动作推荐")
    if not isinstance(fact_proposals, list) or not fact_proposals:
        raise ValueError("缺少已确认的 ModificationFact 提案")

    result = analysis_store.save_analysis_result(
        suggestion_id=suggestion_id,
        workspace_id=workspace_id,
        run_id=run_id,
        input_version=input_version,
        user_id=user_id,
        classification=classification,
        evidence=evidence,
        priority=priority,
        recommended_actions=actions,
        fact_proposals=fact_proposals,
        classification_confirmed_by_user=bool(
            draft_refs.get("classification_confirmed_by_user")
        ),
    )

    result_refs: list[dict[str, str]] = []
    for ref in result.get("result_refs", []):
        if isinstance(ref, dict):
            result_refs.append(
                {"type": str(ref["type"]), "id": str(ref["id"])}
            )
        else:
            result_refs.append({"type": str(ref.type), "id": str(ref.id)})

    return {
        "phase": "SUCCEEDED",
        "pending_interaction_id": None,
        "draft_refs": {},
        "result_refs": result_refs,
        "status": GraphRunStatus.SUCCEEDED,
        "error_code": None,
    }


__all__ = ["persist_analysis"]
