"""SuggestionAnalysisGraph 纯逻辑节点单测（mock LLM，无外网）。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from langgraph_agent.agent.analysis import node as analysis_node
from langgraph_agent.schemas.analysis import (
    ActionRecommendations,
    ClassificationResult,
    Coverage,
    EvidenceAssessment,
    EvidenceJudgement,
    EvidenceRelation,
    Feasibility,
    LlmActionRecommendations,
    LlmClassificationResult,
    LlmEvidenceAssessment,
    LlmPriorityAssessment,
    PriorityAssessment,
    WorkPriority,
)


def _classification_payload(**updates) -> dict[str, object]:
    payload: dict[str, object] = {
        "primary_type": "METHOD_THEORY",
        "target_subtype": "METHOD_CLARITY",
        "secondary_types": [],
        "issue_natures": ["UNCLEAR"],
        "explicit_action": "clarify the method",
        "inferred_action": None,
        "implicit_concern": "the method cannot be reproduced",
        "classification_confidence": 0.9,
        "classification_reason": "The request targets method clarity.",
        "candidate_types": ["METHOD_THEORY"],
    }
    payload.update(updates)
    return payload


def _priority_payload(**updates) -> dict[str, object]:
    payload: dict[str, object] = {
        "academic_impact": "MAJOR",
        "handling_requirement": "MUST_ADDRESS",
        "revision_effort": "MEDIUM",
        "feasibility": "FEASIBLE",
        "work_priority": "P1",
        "schedule_flag": None,
        "risk_signals": ["核心方法不清晰"],
        "assessment_confidence": 0.88,
        "assessment_reason": "影响方法可复现性。",
    }
    payload.update(updates)
    return payload


def _action_payload(**updates) -> dict[str, object]:
    candidate: dict[str, object] = {
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
        "feasibility": "FEASIBLE",
        "expected_resolution_level": "FULL",
        "alternative_to_index": None,
        "alternative_actions": [],
    }
    candidate.update(updates)
    return {"recommendations": [candidate]}


def test_classify_suggestion_uses_analyze_purpose(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        captured.update(
            purpose=purpose,
            schema=schema,
            messages=messages,
            timeout_seconds=timeout_seconds,
        )
        return LlmClassificationResult.model_validate(_classification_payload())

    monkeypatch.setattr(analysis_node, "invoke_structured", fake_invoke)

    result = analysis_node.classify_suggestion(
        "Please clarify the method.",
        ["Clarify the pipeline steps."],
    )

    assert isinstance(result, ClassificationResult)
    assert result.primary_type.value == "METHOD_THEORY"
    assert result.target_subtype.value == "METHOD_CLARITY"
    assert result.automatic_result.primary_type.value == "METHOD_THEORY"
    assert result.confirmed_result is None
    assert captured["purpose"] == "analyze"
    assert captured["schema"] is LlmClassificationResult


def test_analyze_evidence_without_excerpts_keeps_user_facts_only() -> None:
    result = analysis_node.analyze_evidence(
        "Please clarify the method.",
        ["Clarify the pipeline steps."],
        user_facts=["We already have ablation results."],
    )

    assert isinstance(result, EvidenceAssessment)
    assert result.coverage is Coverage.UNKNOWN
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    assert item.relation is EvidenceRelation.USER_STATED
    assert item.evidence_judgement is EvidenceJudgement.USER_STATEMENT
    assert item.quote == "We already have ablation results."


def test_analyze_evidence_with_excerpts_maps_source_index(monkeypatch) -> None:
    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        assert schema is LlmEvidenceAssessment
        return LlmEvidenceAssessment.model_validate(
            {
                "evidence_items": [
                    {
                        "source_index": 1,
                        "relevance": 0.9,
                        "evidence_judgement": "PARTIALLY_ADDRESSES_CONCERN",
                        "relation": "supports",
                    }
                ],
                "coverage": "PARTIAL",
            }
        )

    monkeypatch.setattr(analysis_node, "invoke_structured", fake_invoke)

    result = analysis_node.analyze_evidence(
        "Please clarify the method.",
        paper_excerpts=[
            {
                "section": "Method",
                "location": "p.3",
                "quote": "We use a two-stage pipeline.",
                "surrounding_context": "Overall architecture.",
            }
        ],
    )

    assert result.coverage is Coverage.PARTIAL
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].quote == "We use a two-stage pipeline."
    assert result.evidence_items[0].section == "Method"
    assert result.evidence_items[0].relation is EvidenceRelation.SUPPORTS


def test_assess_priority_forces_unknown_feasibility_without_user_facts(
    monkeypatch,
) -> None:
    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        assert schema is LlmPriorityAssessment
        return LlmPriorityAssessment.model_validate(
            _priority_payload(feasibility="FEASIBLE")
        )

    monkeypatch.setattr(analysis_node, "invoke_structured", fake_invoke)

    base = _classification_payload()
    classification_result = ClassificationResult.model_validate(
        {
            **base,
            "automatic_result": {
                "primary_type": base["primary_type"],
                "target_subtype": base["target_subtype"],
                "secondary_types": base["secondary_types"],
                "issue_natures": base["issue_natures"],
                "explicit_action": base["explicit_action"],
                "inferred_action": base["inferred_action"],
                "implicit_concern": base["implicit_concern"],
            },
            "confirmed_result": None,
        }
    )
    evidence = EvidenceAssessment(evidence_items=[], coverage=Coverage.UNKNOWN)

    result = analysis_node.assess_priority(
        "Please clarify the method.",
        classification_result,
        evidence,
        repeated_reviewer_count=2,
    )

    assert isinstance(result, PriorityAssessment)
    assert result.work_priority is WorkPriority.P1
    assert result.feasibility is Feasibility.UNKNOWN
    assert result.repeated_reviewer_count == 2
    assert result.automatic_result.work_priority is WorkPriority.P1


def test_recommend_actions_assigns_stable_ids(monkeypatch) -> None:
    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        assert schema is LlmActionRecommendations
        return LlmActionRecommendations.model_validate(_action_payload())

    monkeypatch.setattr(analysis_node, "invoke_structured", fake_invoke)

    classification_result = ClassificationResult.model_validate(
        {
            **_classification_payload(),
            "automatic_result": {
                "primary_type": "METHOD_THEORY",
                "target_subtype": "METHOD_CLARITY",
                "secondary_types": [],
                "issue_natures": ["UNCLEAR"],
                "explicit_action": "clarify the method",
                "inferred_action": None,
                "implicit_concern": "the method cannot be reproduced",
            },
            "confirmed_result": None,
        }
    )
    evidence = EvidenceAssessment(evidence_items=[], coverage=Coverage.UNKNOWN)
    priority = PriorityAssessment.model_validate(
        {
            **_priority_payload(feasibility="UNKNOWN"),
            "repeated_reviewer_count": 1,
            "automatic_result": {
                "academic_impact": "MAJOR",
                "handling_requirement": "MUST_ADDRESS",
                "revision_effort": "MEDIUM",
                "feasibility": "UNKNOWN",
                "work_priority": "P1",
                "schedule_flag": None,
                "risk_signals": ["核心方法不清晰"],
                "repeated_reviewer_count": 1,
            },
            "confirmed_result": None,
        }
    )
    suggestion_id = str(uuid4())

    result = analysis_node.recommend_actions(
        "Please clarify the method.",
        classification_result,
        evidence,
        priority,
        suggestion_id=suggestion_id,
    )

    assert isinstance(result, ActionRecommendations)
    assert len(result.recommendations) == 1
    item = result.recommendations[0]
    assert item.recommendation_id == "A-01"
    assert item.suggestion_id == suggestion_id
    assert item.feasibility is Feasibility.UNKNOWN


def test_assess_priority_rejects_invalid_reviewer_count() -> None:
    base = _classification_payload()
    classification_result = ClassificationResult.model_validate(
        {
            **base,
            "automatic_result": {
                "primary_type": base["primary_type"],
                "target_subtype": base["target_subtype"],
                "secondary_types": base["secondary_types"],
                "issue_natures": base["issue_natures"],
                "explicit_action": base["explicit_action"],
                "inferred_action": base["inferred_action"],
                "implicit_concern": base["implicit_concern"],
            },
            "confirmed_result": None,
        }
    )
    evidence = EvidenceAssessment(evidence_items=[], coverage=Coverage.UNKNOWN)
    with pytest.raises(ValueError, match="repeated_reviewer_count"):
        analysis_node.assess_priority(
            "text",
            classification_result,
            evidence,
            repeated_reviewer_count=0,
        )
