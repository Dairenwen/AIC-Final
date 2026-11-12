"""次要置信度字段允许缺失；证据字段仍必填（自 backend/tests 迁入）。"""

from __future__ import annotations

from langgraph_agent.llm.structured_output import (
    StructuredOutputStrategy,
    _DEFAULT_STRATEGY_ORDER,
    _transport_schema,
)
from langgraph_agent.schemas.analysis import (
    LlmClassificationResult,
    LlmPriorityAssessment,
)
from langgraph_agent.tools.paper_schemas import (
    CardType,
    LlmPaperCardBatch,
    LlmPaperCardCandidate,
)


def test_strategy_order_keeps_json_schema_first() -> None:
    assert _DEFAULT_STRATEGY_ORDER[0] == StructuredOutputStrategy.NATIVE_JSON_SCHEMA.value
    assert StructuredOutputStrategy.JSON_OBJECT.value in _DEFAULT_STRATEGY_ORDER
    assert _DEFAULT_STRATEGY_ORDER.index(
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA.value
    ) < _DEFAULT_STRATEGY_ORDER.index(StructuredOutputStrategy.JSON_OBJECT.value)


def test_paper_card_confidence_defaults_when_missing() -> None:
    card = LlmPaperCardCandidate.model_validate(
        {
            "card_type": CardType.LIMITATIONS,
            "content": "论文承认极长序列优化仍属未来工作，尚未实现。",
            "source_section_id": "section-0014",
            "source_quote": "We plan to investigate this approach further in future work.",
        }
    )
    assert card.confidence == 0.75


def test_paper_card_confidence_null_becomes_default() -> None:
    card = LlmPaperCardCandidate.model_validate(
        {
            "card_type": CardType.MAIN_RESULTS,
            "content": "论文报告英德翻译 BLEU 达到 28.4。",
            "source_section_id": "section-0001",
            "source_quote": (
                "Our model achieves 28.4 BLEU on the WMT 2014 "
                "English-to-German translation task."
            ),
            "confidence": None,
        }
    )
    assert card.confidence == 0.75


def test_paper_card_transport_schema_does_not_require_confidence() -> None:
    schema = _transport_schema(LlmPaperCardBatch)
    defs = schema.get("$defs") or schema.get("definitions") or {}
    items = schema["properties"]["cards"]["items"]
    if "$ref" in items:
        candidate = defs[items["$ref"].split("/")[-1]]
    else:
        candidate = items
    assert "confidence" in candidate["properties"]
    assert "confidence" not in candidate.get("required", [])
    for required_field in (
        "card_type",
        "content",
        "source_section_id",
        "source_quote",
    ):
        assert required_field in candidate.get("required", [])


def test_classification_confidence_defaults_when_missing() -> None:
    result = LlmClassificationResult.model_validate(
        {
            "primary_type": "METHOD_THEORY",
            "target_subtype": "METHOD_CLARITY",
            "secondary_types": [],
            "issue_natures": ["UNCLEAR"],
            "explicit_action": "clarify the method",
            "inferred_action": None,
            "implicit_concern": "the method cannot be reproduced",
            "classification_reason": "The request targets method clarity.",
            "candidate_types": ["METHOD_THEORY"],
        }
    )
    assert result.classification_confidence == 0.75


def test_priority_assessment_confidence_defaults_when_missing() -> None:
    result = LlmPriorityAssessment.model_validate(
        {
            "academic_impact": "MAJOR",
            "handling_requirement": "MUST_ADDRESS",
            "revision_effort": "MEDIUM",
            "feasibility": "FEASIBLE",
            "work_priority": "P1",
            "schedule_flag": None,
            "risk_signals": ["可能影响实验可信度"],
            "assessment_reason": "实验缺口会削弱结论说服力。",
        }
    )
    assert result.assessment_confidence == 0.75
