"""SuggestionAnalysisGraph 编译与 FakeStore 最小路径 / interrupt 可达性。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from langgraph_agent.agent.analysis import node as analysis_node
from langgraph_agent.agent.analysis.graph import (
    _confirmed_fact_proposals,
    _facts_interaction,
    _resume_payload,
    build_suggestion_analysis_graph,
)
from langgraph_agent.ports.types import (
    AnalysisContext,
    AnalysisSnapshotRecord,
    ModificationFactRecord,
    PaperBaseline,
    ResultRef,
    SaveAnalysisResult,
    SuggestionBundle,
    SuggestionRecord,
    SuggestionSourceRecord,
)
from langgraph_agent.schemas.analysis import (
    LlmActionRecommendations,
    LlmClassificationResult,
    LlmPriorityAssessment,
)
from langgraph_agent.schemas.interaction import PendingInteraction, ResumeCommand
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import WorkspaceMode


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


def _suggestion_record(
    *,
    workspace_id: UUID,
    suggestion_id: UUID,
    input_version: str,
) -> SuggestionRecord:
    return {
        "suggestion_id": suggestion_id,
        "workspace_id": workspace_id,
        "canonical_text": "Please clarify the evaluation protocol.",
        "status": "READY",
        "merge_group_key": None,
        "conflict_group_key": None,
        "priority": None,
        "category_ids": [],
        "input_version": input_version,
        "current_analysis_id": None,
    }


def _source_record(
    *,
    workspace_id: UUID,
    suggestion_id: UUID,
) -> SuggestionSourceRecord:
    return {
        "source_id": uuid4(),
        "suggestion_id": suggestion_id,
        "workspace_id": workspace_id,
        "party_id": uuid4(),
        "review_input_id": uuid4(),
        "excerpt": "Please clarify the evaluation protocol.",
        "content_hash": "hash-1",
        "localized_claim": "Clarify the evaluation protocol.",
        "stance": None,
        "span_refs": {},
        "status": "ACTIVE",
        "expression_settings_override": None,
    }


class FakeAnalysisStore:
    """最小 AnalysisStore：支持 load 与 save，可配置复用。"""

    def __init__(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        input_version: str,
        reusable: bool = False,
        paper_baseline: PaperBaseline | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.suggestion_id = suggestion_id
        self.input_version = input_version
        self.reusable = reusable
        self.paper_baseline = paper_baseline or {
            "has_baseline": False,
            "manuscript_version_id": None,
            "abstract": "",
            "sections": [],
            "cards": [],
        }
        self.saved: dict[str, Any] | None = None
        self._snapshot_id = uuid4()
        self._fact_id = uuid4()

    def load_analysis_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        input_version: str,
        manuscript_version_id: UUID | None = None,
    ) -> AnalysisContext:
        if workspace_id != self.workspace_id or suggestion_id != self.suggestion_id:
            raise ValueError("Suggestion 不存在或不属于当前 Workspace")
        snapshot: AnalysisSnapshotRecord | None = None
        facts: list[ModificationFactRecord] = []
        if self.reusable:
            snapshot = {
                "analysis_id": self._snapshot_id,
                "suggestion_id": suggestion_id,
                "workspace_id": workspace_id,
                "run_id": uuid4(),
                "input_version": input_version,
                "categories": {},
                "evidence_items": [],
                "coverage": "UNKNOWN",
                "priority": "P2",
                "recommended_actions": [],
                "confidence": 0.9,
                "status": "CONFIRMED",
                "confirmed_at": None,
                "confirmed_by": "tester",
            }
            facts = [
                {
                    "fact_id": self._fact_id,
                    "suggestion_id": suggestion_id,
                    "workspace_id": workspace_id,
                    "action_type": "ACCEPT",
                    "paper_change_summary": "已补充说明",
                    "response_fact_summary": "已完成修改",
                    "constraints": {},
                    "status": "CONFIRMED",
                    "input_version": input_version,
                    "confirmed_at": None,
                    "confirmed_by": "tester",
                }
            ]
        return {
            "suggestion": _suggestion_record(
                workspace_id=workspace_id,
                suggestion_id=suggestion_id,
                input_version=input_version,
            ),
            "sources": [
                _source_record(
                    workspace_id=workspace_id,
                    suggestion_id=suggestion_id,
                )
            ],
            "current_snapshot": snapshot,
            "confirmed_facts": facts,
            "paper_baseline": self.paper_baseline,
            "reusable": self.reusable and input_version == self.input_version,
        }

    def save_analysis_snapshot(self, **kwargs: Any) -> AnalysisSnapshotRecord:
        raise NotImplementedError

    def save_modification_facts(self, **kwargs: Any) -> list[ModificationFactRecord]:
        raise NotImplementedError

    def save_analysis_result(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        classification: dict[str, Any],
        evidence: dict[str, Any],
        priority: dict[str, Any],
        recommended_actions: dict[str, Any] | list[dict[str, Any]],
        fact_proposals: list[dict[str, Any]],
        classification_confirmed_by_user: bool = False,
    ) -> SaveAnalysisResult:
        self.saved = {
            "suggestion_id": suggestion_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "input_version": input_version,
            "user_id": user_id,
            "classification": classification,
            "evidence": evidence,
            "priority": priority,
            "recommended_actions": recommended_actions,
            "fact_proposals": fact_proposals,
            "classification_confirmed_by_user": classification_confirmed_by_user,
        }
        snapshot: AnalysisSnapshotRecord = {
            "analysis_id": self._snapshot_id,
            "suggestion_id": suggestion_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "input_version": input_version,
            "categories": classification,
            "evidence_items": list(evidence.get("evidence_items", [])),
            "coverage": str(evidence.get("coverage", "UNKNOWN")),
            "priority": str(priority["work_priority"]),
            "recommended_actions": (
                list(recommended_actions.get("recommendations", []))
                if isinstance(recommended_actions, dict)
                else list(recommended_actions)
            ),
            "confidence": float(classification["classification_confidence"]),
            "status": "CONFIRMED",
            "confirmed_at": None,
            "confirmed_by": user_id,
        }
        facts: list[ModificationFactRecord] = [
            {
                "fact_id": self._fact_id,
                "suggestion_id": suggestion_id,
                "workspace_id": workspace_id,
                "action_type": str(fact_proposals[0]["action_type"]),
                "paper_change_summary": str(fact_proposals[0]["paper_change_summary"]),
                "response_fact_summary": str(
                    fact_proposals[0]["response_fact_summary"]
                ),
                "constraints": dict(fact_proposals[0]["constraints"]),
                "status": "CONFIRMED",
                "input_version": input_version,
                "confirmed_at": None,
                "confirmed_by": user_id,
            }
        ]
        refs: list[ResultRef] = [
            {"type": "analysis", "id": str(self._snapshot_id)},
            {"type": "modification_fact", "id": str(self._fact_id)},
        ]
        return {
            "snapshot": snapshot,
            "facts": facts,
            "result_refs": refs,
            "reused": False,
        }


class FakeSuggestionStore:
    def __init__(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        input_version: str,
    ) -> None:
        self.workspace_id = workspace_id
        self.suggestion_id = suggestion_id
        self.input_version = input_version
        self.calls = 0

    def load_suggestion_bundle(
        self,
        suggestion_id: UUID,
        *,
        workspace_id: UUID | None = None,
        source_status: str | None = "ACTIVE",
    ) -> SuggestionBundle:
        self.calls += 1
        if suggestion_id != self.suggestion_id:
            raise ValueError("Suggestion 不存在")
        if workspace_id is not None and workspace_id != self.workspace_id:
            raise ValueError("Suggestion 不属于当前 Workspace")
        return {
            "suggestion": _suggestion_record(
                workspace_id=self.workspace_id,
                suggestion_id=self.suggestion_id,
                input_version=self.input_version,
            ),
            "sources": [
                _source_record(
                    workspace_id=self.workspace_id,
                    suggestion_id=self.suggestion_id,
                )
            ],
        }


# ---------------------------------------------------------------------------
# LLM fixtures
# ---------------------------------------------------------------------------


def _classification_payload(*, confidence: float = 0.9) -> dict[str, object]:
    return {
        "primary_type": "METHOD_THEORY",
        "target_subtype": "METHOD_CLARITY",
        "secondary_types": [],
        "issue_natures": ["UNCLEAR"],
        "explicit_action": "clarify the method",
        "inferred_action": None,
        "implicit_concern": "the method cannot be reproduced",
        "classification_confidence": confidence,
        "classification_reason": "The request targets method clarity.",
        "candidate_types": ["METHOD_THEORY"],
    }


def _priority_payload() -> dict[str, object]:
    return {
        "academic_impact": "MAJOR",
        "handling_requirement": "MUST_ADDRESS",
        "revision_effort": "MEDIUM",
        "feasibility": "UNKNOWN",
        "work_priority": "P1",
        "schedule_flag": None,
        "risk_signals": ["核心方法不清晰"],
        "assessment_confidence": 0.88,
        "assessment_reason": "影响方法可复现性。",
    }


def _action_payload() -> dict[str, object]:
    return {
        "recommendations": [
            {
                "action_type": "METHOD_DATA_ANALYSIS",
                "title": "补充方法细节",
                "description": "在方法章节补充关键步骤与伪代码。",
                "addressed_concern": "方法描述不清",
                "necessity": "CORE",
                "recommendation_basis": ["审稿人明确要求"],
                "required_facts": [],
                "prerequisites": [],
                "expected_outputs": ["修订后的方法段落"],
                "estimated_cost": "MEDIUM",
                "feasibility": "UNKNOWN",
                "expected_resolution_level": "FULL",
                "alternative_to_index": None,
                "alternative_actions": [],
            }
        ]
    }


def _install_llm_mocks(monkeypatch, *, confidence: float = 0.9) -> None:
    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        if schema is LlmClassificationResult:
            return LlmClassificationResult.model_validate(
                _classification_payload(confidence=confidence)
            )
        if schema is LlmPriorityAssessment:
            return LlmPriorityAssessment.model_validate(_priority_payload())
        if schema is LlmActionRecommendations:
            return LlmActionRecommendations.model_validate(_action_payload())
        raise AssertionError(f"未 mock 的 schema: {schema}")

    monkeypatch.setattr(analysis_node, "invoke_structured", fake_invoke)


def _base_state(
    *,
    workspace_id: UUID,
    suggestion_id: UUID,
    run_id: UUID,
    input_version: str,
    mode: WorkspaceMode = WorkspaceMode.FAST,
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "suggestion_id": suggestion_id,
        "user_id": "tester",
        "mode": mode,
        "manuscript_version_id": None,
        "thread_id": f"workspace:{workspace_id}:suggestion:{suggestion_id}:analysis:{run_id}",
        "run_id": run_id,
        "input_version": input_version,
        "phase": "START",
        "pending_interaction_id": None,
        "draft_refs": {},
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }


def _pending_from_interrupt(output: dict[str, Any]) -> PendingInteraction:
    interrupts = output.get("__interrupt__", ())
    assert isinstance(interrupts, (list, tuple)) and len(interrupts) == 1
    return PendingInteraction.model_validate(interrupts[0].value)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_graph_requires_checkpointer() -> None:
    with pytest.raises(ValueError, match="Checkpointer"):
        build_suggestion_analysis_graph(
            stores={"analysis_store": object()},
            checkpointer=None,
        )


def test_reuse_path_returns_existing_refs() -> None:
    workspace_id = uuid4()
    suggestion_id = uuid4()
    run_id = uuid4()
    input_version = "v1"
    analysis_store = FakeAnalysisStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=input_version,
        reusable=True,
    )
    graph = build_suggestion_analysis_graph(
        stores={"analysis_store": analysis_store},
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        _base_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            run_id=run_id,
            input_version=input_version,
        ),
        config={"configurable": {"thread_id": f"t-{run_id}"}},
    )
    assert result["phase"] == "REUSED"
    assert result["status"] == GraphRunStatus.SUCCEEDED
    assert any(ref["type"] == "analysis" for ref in result["result_refs"])
    assert any(ref["type"] == "modification_fact" for ref in result["result_refs"])


def test_fast_path_reaches_confirm_facts_interrupt(monkeypatch) -> None:
    _install_llm_mocks(monkeypatch, confidence=0.9)
    workspace_id = uuid4()
    suggestion_id = uuid4()
    run_id = uuid4()
    input_version = "v1"
    analysis_store = FakeAnalysisStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=input_version,
    )
    suggestion_store = FakeSuggestionStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=input_version,
    )
    graph = build_suggestion_analysis_graph(
        stores={
            "analysis_store": analysis_store,
            "suggestion_store": suggestion_store,
        },
        checkpointer=InMemorySaver(),
    )
    thread_id = f"t-{run_id}"
    output = graph.invoke(
        _base_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            run_id=run_id,
            input_version=input_version,
            mode=WorkspaceMode.FAST,
        ),
        config={"configurable": {"thread_id": thread_id}},
    )
    pending = _pending_from_interrupt(output)
    assert pending.interaction_type == "CONFIRM_MODIFICATION_FACTS"
    assert pending.resume_action == "confirm_facts"
    assert suggestion_store.calls == 1

    # resume 后落库成功
    fact = {
        "recommendation_id": "A-01",
        "decision_status": "ACCEPTED",
        "execution_status": "PLANNED",
        "action_type": "ACCEPT",
        "paper_change_summary": "在方法章节补充关键步骤。",
        "response_fact_summary": "计划补充方法细节。",
        "constraints": {},
    }
    command = ResumeCommand(
        workspace_id=pending.workspace_id,
        thread_id=pending.thread_id,
        interaction_id=pending.interaction_id,
        input_version=pending.input_version,
        payload={"facts": [fact]},
    )
    final = graph.invoke(
        Command(resume=command.model_dump(mode="json")),
        config={"configurable": {"thread_id": thread_id}},
    )
    assert final["phase"] == "SUCCEEDED"
    assert final["status"] == GraphRunStatus.SUCCEEDED
    assert analysis_store.saved is not None
    assert analysis_store.saved["fact_proposals"]
    assert any(ref["type"] == "analysis" for ref in final["result_refs"])


def test_low_confidence_classification_interrupt(monkeypatch) -> None:
    _install_llm_mocks(monkeypatch, confidence=0.5)
    workspace_id = uuid4()
    suggestion_id = uuid4()
    run_id = uuid4()
    input_version = "v1"
    analysis_store = FakeAnalysisStore(
        workspace_id=workspace_id,
        suggestion_id=suggestion_id,
        input_version=input_version,
    )
    graph = build_suggestion_analysis_graph(
        stores={"analysis_store": analysis_store},
        checkpointer=InMemorySaver(),
    )
    thread_id = f"t-{run_id}"
    output = graph.invoke(
        _base_state(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            run_id=run_id,
            input_version=input_version,
        ),
        config={"configurable": {"thread_id": thread_id}},
    )
    pending = _pending_from_interrupt(output)
    assert pending.interaction_type == "CONFIRM_CLASSIFICATION"
    assert pending.resume_action == "confirm_classification"


def test_facts_interaction_defaults_and_resume_contract() -> None:
    workspace_id = uuid4()
    suggestion_id = uuid4()
    run_id = uuid4()
    state = {
        "workspace_id": workspace_id,
        "suggestion_id": suggestion_id,
        "thread_id": f"workspace:{workspace_id}:suggestion:{suggestion_id}:analysis:{run_id}",
        "pending_interaction_id": uuid4(),
        "input_version": "analysis-input-v1",
        "draft_refs": {
            "action_recommendations": {
                "recommendations": [
                    {
                        "recommendation_id": "A-01",
                        "title": "补充数据划分说明",
                        "description": "在实验部分补充训练、验证和测试集的划分方法。",
                        "addressed_concern": "数据划分不清晰。",
                        "expected_resolution_level": "FULL",
                        "required_facts": ["实际划分比例"],
                        "prerequisites": ["核对实验配置"],
                    }
                ]
            }
        },
    }
    interaction = _facts_interaction(state)  # type: ignore[arg-type]
    assert interaction.interaction_type == "CONFIRM_MODIFICATION_FACTS"
    field = interaction.editable_fields[0]
    assert field.key == "facts"
    assert field.default[0]["action_type"] == "ACCEPT"  # type: ignore[index]

    fact = {
        "recommendation_id": "A-01",
        "decision_status": "ACCEPTED",
        "execution_status": "COMPLETED",
        "action_type": "ACCEPT",
        "paper_change_summary": "已补充数据划分方法。",
        "response_fact_summary": "修订稿已说明训练、验证和测试集划分。",
        "constraints": {"note": "沿用现有实验数据"},
    }
    command = ResumeCommand(
        workspace_id=interaction.workspace_id,
        thread_id=interaction.thread_id,
        interaction_id=interaction.interaction_id,
        input_version=interaction.input_version,
        payload={"facts": [fact]},
    )
    payload = _resume_payload(command.model_dump(mode="json"), interaction)
    confirmed = _confirmed_fact_proposals(
        state["draft_refs"]["action_recommendations"]["recommendations"],  # type: ignore[index]
        payload,
    )
    assert confirmed[0]["action_type"] == "ACCEPT"
    assert confirmed[0]["constraints"]["required_facts"] == ["实际划分比例"]
