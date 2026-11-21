"""SourceReplyGraph 编译与 FakeStore 路径验证。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from langgraph_agent.agent.reply import node as reply_node
from langgraph_agent.agent.reply.graph import build_source_reply_graph
from langgraph_agent.agent.reply.thread_ids import build_reply_thread_id
from langgraph_agent.ports.types import (
    AnalysisSnapshotRecord,
    ModificationFactRecord,
    ReplyContext,
    ReplyDraftRecord,
    SaveReplyDraftResult,
    SaveReviewDecisionResult,
    SourceReplyRecord,
    SuggestionSourceRecord,
)
from langgraph_agent.schemas.reply import (
    ClaimInterpretation,
    ConsistencyReport,
    LlmResponseDraft,
    LlmResponseFacts,
    ReplyDirection,
    ReplyStrategy,
)
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import ResponseSettings, SettingsSource


def _settings_dict() -> dict[str, Any]:
    return ResponseSettings(
        response_language="英文",
        tone="正式、礼貌",
        author_reference="The authors",
        target_length="标准",
        terminology_preferences=[],
        source=SettingsSource.SYSTEM_DEFAULT,
    ).model_dump(mode="json")


class FakeReplyStore:
    """内存 ReplyStore：覆盖 load / save_draft / save_review。"""

    def __init__(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        fact_id: UUID,
        analysis_ready: bool = True,
    ) -> None:
        self.workspace_id = workspace_id
        self.suggestion_id = suggestion_id
        self.source_id = source_id
        self.fact_id = fact_id
        self.analysis_ready = analysis_ready
        self.saved_drafts: list[dict[str, Any]] = []
        self.saved_reviews: list[dict[str, Any]] = []
        self._reply_id = uuid4()
        self._draft_id = uuid4()
        self._version = 0

    def load_reply_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        expression_settings: dict[str, Any] | None = None,
    ) -> ReplyContext:
        assert workspace_id == self.workspace_id
        assert suggestion_id == self.suggestion_id
        assert source_id == self.source_id
        settings = _settings_dict()
        if expression_settings is not None and expression_settings != settings:
            raise ValueError("回复表达设置已变化，请重新运行")

        source: SuggestionSourceRecord = {
            "source_id": source_id,
            "suggestion_id": suggestion_id,
            "workspace_id": workspace_id,
            "party_id": uuid4(),
            "review_input_id": uuid4(),
            "excerpt": "The sampling procedure is not sufficiently clear.",
            "content_hash": "hash",
            "localized_claim": "Clarify how samples were selected.",
            "stance": "REQUEST",
            "span_refs": {},
            "status": "ACTIVE",
            "expression_settings_override": None,
        }
        snapshot: AnalysisSnapshotRecord | None = None
        facts: list[ModificationFactRecord] = []
        if self.analysis_ready:
            snapshot = {
                "analysis_id": uuid4(),
                "suggestion_id": suggestion_id,
                "workspace_id": workspace_id,
                "run_id": uuid4(),
                "input_version": "v1",
                "categories": {"primary_type": "METHOD_THEORY"},
                "evidence_items": [],
                "coverage": "UNKNOWN",
                "priority": "P1",
                "recommended_actions": [{"title": "补充采样流程"}],
                "confidence": 0.9,
                "status": "CONFIRMED",
                "confirmed_at": datetime.now(timezone.utc),
                "confirmed_by": "tester",
            }
            facts = [
                {
                    "fact_id": self.fact_id,
                    "suggestion_id": suggestion_id,
                    "workspace_id": workspace_id,
                    "action_type": "ACCEPT",
                    "paper_change_summary": "已在方法部分补充样本筛选步骤。",
                    "response_fact_summary": "作者已补充并澄清样本筛选流程。",
                    "constraints": {"execution_status": "COMPLETED"},
                    "status": "CONFIRMED",
                    "input_version": "v1",
                    "confirmed_at": datetime.now(timezone.utc),
                    "confirmed_by": "tester",
                }
            ]
        return {
            "analysis_ready": self.analysis_ready and bool(facts),
            "source": source,
            "expression_settings": settings,
            "confirmed_analysis": snapshot,
            "confirmed_modification_facts": facts,
            "other_approved_replies": [],
        }

    def save_reply_draft(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        strategy: dict[str, Any],
        expression_settings: dict[str, Any],
        response_facts: dict[str, Any] | list[Any],
        generated_draft: dict[str, Any],
        consistency_report: dict[str, Any] | list[Any],
    ) -> SaveReplyDraftResult:
        self._version += 1
        self._draft_id = uuid4()
        payload = {
            "workspace_id": workspace_id,
            "suggestion_id": suggestion_id,
            "source_id": source_id,
            "run_id": run_id,
            "input_version": input_version,
            "user_id": user_id,
            "strategy": strategy,
            "expression_settings": expression_settings,
            "response_facts": response_facts,
            "generated_draft": generated_draft,
            "consistency_report": consistency_report,
        }
        self.saved_drafts.append(payload)
        reply: SourceReplyRecord = {
            "reply_id": self._reply_id,
            "source_id": source_id,
            "suggestion_id": suggestion_id,
            "workspace_id": workspace_id,
            "strategy": strategy,
            "expression_settings": expression_settings,
            "response_facts": response_facts,
            "status": "REVIEW_WAITING",
            "current_draft_id": self._draft_id,
            "input_version": input_version,
        }
        draft: ReplyDraftRecord = {
            "draft_id": self._draft_id,
            "reply_id": self._reply_id,
            "version_no": self._version,
            "content": str(generated_draft["generated_content"]),
            "language": str(generated_draft["language"]),
            "consistency_report": consistency_report,
            "status": "GENERATED",
            "run_id": run_id,
            "approved_at": None,
            "approved_by": None,
        }
        return {
            "reply": reply,
            "draft": draft,
            "result_refs": [
                {"type": "source_reply", "id": str(self._reply_id)},
                {"type": "reply_draft", "id": str(self._draft_id)},
            ],
            "phase": "REVIEW_DRAFT",
            "reused": False,
        }

    def save_review_decision(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        user_id: str,
        reply_id: UUID,
        draft_id: UUID,
        decision: dict[str, Any],
    ) -> SaveReviewDecisionResult:
        self.saved_reviews.append(
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "user_id": user_id,
                "reply_id": reply_id,
                "draft_id": draft_id,
                "decision": decision,
            }
        )
        action = decision.get("action")
        if action == "approve":
            reply: SourceReplyRecord = {
                "reply_id": reply_id,
                "source_id": self.source_id,
                "suggestion_id": self.suggestion_id,
                "workspace_id": workspace_id,
                "strategy": {},
                "expression_settings": _settings_dict(),
                "response_facts": {},
                "status": "APPROVED",
                "current_draft_id": draft_id,
                "input_version": "v1",
            }
            draft: ReplyDraftRecord = {
                "draft_id": draft_id,
                "reply_id": reply_id,
                "version_no": self._version,
                "content": "approved content",
                "language": "英文",
                "consistency_report": {"is_consistent": True, "issues": []},
                "status": "APPROVED",
                "run_id": run_id,
                "approved_at": datetime.now(timezone.utc),
                "approved_by": user_id,
            }
            return {
                "reply": reply,
                "draft": draft,
                "result_refs": [
                    {"type": "source_reply", "id": str(reply_id)},
                    {"type": "reply_draft", "id": str(draft_id)},
                ],
                "phase": "SUCCEEDED",
                "synced_sources": [],
            }

        if action != "edit":
            raise ValueError("审核 action 只能是 approve 或 edit")
        self._version += 1
        new_draft_id = uuid4()
        content = str(decision.get("content") or "").strip()
        reply = {
            "reply_id": reply_id,
            "source_id": self.source_id,
            "suggestion_id": self.suggestion_id,
            "workspace_id": workspace_id,
            "strategy": {},
            "expression_settings": _settings_dict(),
            "response_facts": {},
            "status": "REVIEW_WAITING",
            "current_draft_id": new_draft_id,
            "input_version": "v1",
        }
        draft = {
            "draft_id": new_draft_id,
            "reply_id": reply_id,
            "version_no": self._version,
            "content": content,
            "language": "英文",
            "consistency_report": {"stale": True, "issues": []},
            "status": "EDITED",
            "run_id": run_id,
            "approved_at": None,
            "approved_by": None,
        }
        self._draft_id = new_draft_id
        return {
            "reply": reply,
            "draft": draft,
            "result_refs": [
                {"type": "source_reply", "id": str(reply_id)},
                {"type": "reply_draft", "id": str(new_draft_id)},
            ],
            "phase": "REVIEW_DRAFT",
            "synced_sources": [],
        }


def _install_llm_fakes(monkeypatch, fact_id: UUID) -> None:
    def fake_invoke(schema, _system, _task, context):
        if schema is ClaimInterpretation:
            return ClaimInterpretation(
                reviewer_intent_summary="要求澄清采样流程",
                implicit_concerns=["可复现"],
                paper_coverage_summary="部分覆盖",
                required_questions=[],
            )
        if schema is ReplyStrategy:
            return ReplyStrategy(
                recommended_direction=ReplyDirection.ACCEPT_AND_REVISE,
                direction_rationale="已有确认修改事实",
                emphasis_points=["说明补充位置"],
                avoid_points=["承诺新实验"],
                risk_flags=[],
            )
        if schema is LlmResponseFacts:
            return LlmResponseFacts(
                acknowledgement="Thank you for the comment.",
                direct_answer="We clarified the sampling procedure.",
                author_position="The concern is accepted.",
                linked_fact_ids=[fact_id],
                modification_locations=["Methods"],
                unresolved_items=["可忽略的未决"],
            )
        if schema is LlmResponseDraft:
            return LlmResponseDraft(
                generated_content="We clarified the sampling procedure in Methods.",
                used_fact_ids=[fact_id],
            )
        if schema is ConsistencyReport:
            return ConsistencyReport(
                is_consistent=True,
                issues=[],
                cross_source_conflicts=[],
                reminders=[],
            )
        raise AssertionError(f"unexpected schema {schema}")

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)


def _initial_state(
    *,
    workspace_id: UUID,
    suggestion_id: UUID,
    source_id: UUID,
    run_id: UUID,
    input_version: str,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "suggestion_id": suggestion_id,
        "source_id": source_id,
        "thread_id": build_reply_thread_id(workspace_id, source_id, input_version),
        "user_id": "tester",
        "run_id": run_id,
        "input_version": input_version,
        "phase": "START",
        "pending_interaction_id": None,
        "draft_refs": {},
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }


def test_build_source_reply_graph_requires_checkpointer():
    store = FakeReplyStore(
        workspace_id=uuid4(),
        suggestion_id=uuid4(),
        source_id=uuid4(),
        fact_id=uuid4(),
    )
    with pytest.raises(ValueError, match="Checkpointer"):
        build_source_reply_graph(stores=store, checkpointer=None)


def test_graph_blocks_when_analysis_not_ready():
    workspace_id = uuid4()
    suggestion_id = uuid4()
    source_id = uuid4()
    run_id = uuid4()
    store = FakeReplyStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        fact_id=uuid4(),
        analysis_ready=False,
    )
    graph = build_source_reply_graph(stores=store, checkpointer=MemorySaver())
    result = graph.invoke(
        _initial_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            run_id=run_id,
            input_version="v1",
        ),
        config={"configurable": {"thread_id": "blocked-test"}},
    )
    assert result["phase"] == "BLOCKED_ANALYSIS"
    assert result["status"] == GraphRunStatus.SUCCEEDED
    assert store.saved_drafts == []


def test_graph_reaches_review_interrupt_then_approve(monkeypatch):
    workspace_id = uuid4()
    suggestion_id = uuid4()
    source_id = uuid4()
    fact_id = uuid4()
    run_id = uuid4()
    input_version = "v1"
    store = FakeReplyStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        fact_id=fact_id,
    )
    _install_llm_fakes(monkeypatch, fact_id)
    checkpointer = MemorySaver()
    graph = build_source_reply_graph(stores=store, checkpointer=checkpointer)
    thread_id = build_reply_thread_id(workspace_id, source_id, input_version)
    config = {"configurable": {"thread_id": thread_id}}

    interrupted = graph.invoke(
        _initial_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            run_id=run_id,
            input_version=input_version,
        ),
        config=config,
    )
    # LangGraph interrupt 后 state 停在 WAITING；通过 snapshot 取 tasks
    snapshot = graph.get_state(config)
    assert snapshot.next == ("review_draft",)
    assert len(store.saved_drafts) == 1
    assert interrupted["phase"] == "REVIEW_DRAFT"
    assert interrupted["status"] == GraphRunStatus.WAITING_USER

    # resume: approve
    final = graph.invoke(
        Command(resume={"action": "approve"}),
        config=config,
    )
    assert final["phase"] == "SUCCEEDED"
    assert final["status"] == GraphRunStatus.SUCCEEDED
    assert len(store.saved_reviews) == 1
    assert store.saved_reviews[0]["decision"]["action"] == "approve"


def test_graph_edit_then_approve_loop(monkeypatch):
    workspace_id = uuid4()
    suggestion_id = uuid4()
    source_id = uuid4()
    fact_id = uuid4()
    run_id = uuid4()
    input_version = "v-edit"
    store = FakeReplyStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        fact_id=fact_id,
    )
    _install_llm_fakes(monkeypatch, fact_id)
    graph = build_source_reply_graph(stores=store, checkpointer=MemorySaver())
    thread_id = build_reply_thread_id(workspace_id, source_id, input_version)
    config = {"configurable": {"thread_id": thread_id}}

    graph.invoke(
        _initial_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            run_id=run_id,
            input_version=input_version,
        ),
        config=config,
    )
    assert graph.get_state(config).next == ("review_draft",)

    edited = graph.invoke(
        Command(
            resume={
                "action": "edit",
                "content": "Edited reply body for reviewers.",
            }
        ),
        config=config,
    )
    assert edited["phase"] == "REVIEW_DRAFT"
    assert edited["status"] == GraphRunStatus.WAITING_USER
    assert edited["draft_refs"]["persisted_reply"]["content"] == (
        "Edited reply body for reviewers."
    )
    assert graph.get_state(config).next == ("review_draft",)

    final = graph.invoke(
        Command(resume={"action": "approve"}),
        config=config,
    )
    assert final["phase"] == "SUCCEEDED"
    assert [item["decision"]["action"] for item in store.saved_reviews] == [
        "edit",
        "approve",
    ]


def test_stores_aggregation_protocol(monkeypatch):
    """stores.reply 聚合注入也能 compile 并跑到 interrupt。"""
    workspace_id = uuid4()
    suggestion_id = uuid4()
    source_id = uuid4()
    fact_id = uuid4()
    run_id = uuid4()
    reply_store = FakeReplyStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        source_id=source_id,
        fact_id=fact_id,
    )

    class Bundle:
        reply = reply_store

    _install_llm_fakes(monkeypatch, fact_id)
    graph = build_source_reply_graph(stores=Bundle(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "bundle-test"}}
    result = graph.invoke(
        _initial_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            run_id=run_id,
            input_version="v1",
        ),
        config=config,
    )
    assert result["phase"] == "REVIEW_DRAFT"
    assert len(reply_store.saved_drafts) == 1
