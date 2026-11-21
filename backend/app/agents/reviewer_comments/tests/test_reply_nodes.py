"""SourceReplyGraph 纯逻辑节点与自动确认路径单测。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from langgraph_agent.agent import reply as reply_pkg
from langgraph_agent.agent.reply import node as reply_node
from langgraph_agent.agent.reply.graph import (
    confirm_response_facts,
    confirm_strategy,
    response_facts_interaction,
)
from langgraph_agent.agent.reply.persist import stale_consistency_report
from langgraph_agent.agent.reply.sync import (
    build_propagated_consistency_report,
    build_sibling_sync_item,
)
from langgraph_agent.agent.reply.thread_ids import (
    build_reply_thread_id,
    legacy_reply_thread_id,
)
from langgraph_agent.schemas.reply import (
    ApprovedSourceReply,
    ClaimInterpretation,
    ConfirmedAnalysis,
    ConfirmedModificationFact,
    ConsistencyIssue,
    ConsistencyIssueType,
    ConsistencyReport,
    LlmResponseDraft,
    LlmResponseFacts,
    ReplyDirection,
    ReplyStrategy,
    ResponseDraft,
    ResponseFacts,
    SourceClaim,
)
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import ResponseSettings, SettingsSource


def _settings() -> ResponseSettings:
    return ResponseSettings(
        response_language="英文",
        tone="正式、礼貌",
        author_reference="The authors",
        target_length="标准",
        terminology_preferences=[],
        source=SettingsSource.SYSTEM_DEFAULT,
    )


def _fact(suggestion_id, *, status="CONFIRMED", action_type="ACCEPT"):
    return ConfirmedModificationFact(
        fact_id=uuid4(),
        suggestion_id=suggestion_id,
        action_type=action_type,
        paper_change_summary="The protocol section was clarified.",
        response_fact_summary="The authors clarified the protocol.",
        constraints={"execution_status": "COMPLETED"},
        status=status,
        input_version="v1",
    )


def _analysis(suggestion_id) -> ConfirmedAnalysis:
    return ConfirmedAnalysis(
        analysis_id=uuid4(),
        suggestion_id=suggestion_id,
        input_version="v1",
        categories={"primary_type": "METHOD_THEORY"},
        evidence_items=[],
        coverage="UNKNOWN",
        priority="P1",
        recommended_actions=[{"title": "clarify"}],
        status="CONFIRMED",
    )


def _source(suggestion_id) -> SourceClaim:
    return SourceClaim(
        source_id=uuid4(),
        suggestion_id=suggestion_id,
        original_text="Clarify the protocol.",
        localized_claim="Clarify the protocol.",
    )


def _response_facts(source_id, fact_id) -> ResponseFacts:
    return ResponseFacts(
        response_facts_id=None,
        source_id=source_id,
        selected_direction=ReplyDirection.ACCEPT_AND_REVISE,
        acknowledgement="Thank you for the comment.",
        direct_answer="We clarified the protocol.",
        author_position="The concern is accepted.",
        linked_fact_ids=[fact_id],
        fact_item_ids=[],
        confirmed_revision_action_ids=[],
        evidence_item_ids=[],
        modification_locations=[],
        limitation_fact_ids=[],
        alternative_action_ids=[],
        unresolved_items=[],
        version=1,
        confirmation_status="CONFIRMED",
    )


def test_thread_ids_include_version_digest():
    workspace_id = uuid4()
    source_id = uuid4()
    legacy = legacy_reply_thread_id(workspace_id, source_id)
    versioned = build_reply_thread_id(workspace_id, source_id, "input-v1")
    assert legacy == f"workspace:{workspace_id}:reply:{source_id}"
    assert versioned.startswith(f"{legacy}:version:")
    assert len(versioned) > len(legacy)


def test_two_sources_generate_distinct_drafts_from_same_confirmed_fact(monkeypatch):
    suggestion_id = uuid4()
    fact = _fact(suggestion_id)
    sources = [_source(suggestion_id) for _ in range(2)]

    def fake_invoke(schema, _system, _task, context):
        assert schema is LlmResponseDraft
        allowed = context["allowed_confirmed_facts"]
        assert [item["status"] for item in allowed] == ["CONFIRMED"]
        return LlmResponseDraft(
            generated_content=f"Reply for {context['source']['source_id']}",
            used_fact_ids=[fact.fact_id],
        )

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    drafts = [
        reply_node.generate_draft(
            source,
            _response_facts(source.source_id, fact.fact_id),
            [fact],
            _settings(),
        )
        for source in sources
    ]
    assert drafts[0].source_id != drafts[1].source_id
    assert drafts[0].generated_content != drafts[1].generated_content
    assert drafts[0].used_fact_ids == drafts[1].used_fact_ids == [fact.fact_id]


def test_unconfirmed_fact_and_invalid_action_type_are_rejected():
    suggestion_id = uuid4()
    with pytest.raises(ValidationError):
        _fact(suggestion_id, status="PLANNED")
    with pytest.raises(ValidationError):
        _fact(suggestion_id, action_type="TRANSPARENCY_COMPLIANCE")


def test_generate_draft_allows_linked_facts_even_with_unresolved_items(monkeypatch):
    suggestion_id = uuid4()
    fact = _fact(suggestion_id)
    source = _source(suggestion_id)
    facts = _response_facts(source.source_id, fact.fact_id).model_copy(
        update={
            "unresolved_items": ["审稿人还提到了补充 ablation，但确认事实未覆盖"],
            "direct_answer": "The authors will clarify the protocol in the revision.",
        }
    )
    captured: dict = {}

    def fake_invoke(schema, _system, task, context):
        assert schema is LlmResponseDraft
        captured["task"] = task
        captured["unresolved"] = context.get("unresolved_reminders")
        return LlmResponseDraft(
            generated_content="We will clarify the protocol in the revised manuscript.",
            used_fact_ids=[fact.fact_id],
        )

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    draft = reply_node.generate_draft(source, facts, [fact], _settings())
    assert "will clarify" in draft.generated_content
    assert draft.used_fact_ids == [fact.fact_id]
    assert captured["unresolved"] == [
        "审稿人还提到了补充 ablation，但确认事实未覆盖"
    ]


def test_generate_draft_fails_without_linked_facts_when_not_acknowledge_only():
    suggestion_id = uuid4()
    fact = _fact(suggestion_id)
    source = _source(suggestion_id)
    empty_linked = _response_facts(source.source_id, fact.fact_id).model_copy(
        update={
            "linked_fact_ids": [],
            "unresolved_items": ["缺少对照实验说明"],
        }
    )
    with pytest.raises(ValueError, match="未绑定任何已确认修改事实") as exc:
        reply_node.generate_draft(source, empty_linked, [fact], _settings())
    assert "缺少对照实验说明" in str(exc.value)


def test_build_response_facts_rejects_unknown_fact_ids(monkeypatch):
    suggestion_id = uuid4()
    source = _source(suggestion_id)
    fact = _fact(suggestion_id)
    unknown = uuid4()

    def fake_invoke(schema, *_args, **_kwargs):
        assert schema is LlmResponseFacts
        return LlmResponseFacts(
            acknowledgement="Thanks",
            direct_answer="Will revise",
            author_position="Accept",
            linked_fact_ids=[unknown],
            modification_locations=[],
            unresolved_items=[],
        )

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    with pytest.raises(ValueError, match="未确认事实"):
        reply_node.build_response_facts(
            source, ReplyDirection.ACCEPT_AND_REVISE, [fact]
        )


def test_build_response_facts_requires_linked_for_non_ack(monkeypatch):
    suggestion_id = uuid4()
    source = _source(suggestion_id)
    fact = _fact(suggestion_id)

    def fake_invoke(schema, *_args, **_kwargs):
        return LlmResponseFacts(
            acknowledgement="Thanks",
            direct_answer="N/A",
            author_position="Ack",
            linked_fact_ids=[],
            modification_locations=[],
            unresolved_items=[],
        )

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    with pytest.raises(ValueError, match="至少一条已确认事实"):
        reply_node.build_response_facts(
            source, ReplyDirection.ACCEPT_AND_REVISE, [fact]
        )


def test_interpret_and_recommend_and_consistency(monkeypatch):
    suggestion_id = uuid4()
    source = _source(suggestion_id)
    analysis = _analysis(suggestion_id)
    fact = _fact(suggestion_id)

    def fake_invoke(schema, *_args, **_kwargs):
        if schema is ClaimInterpretation:
            return ClaimInterpretation(
                reviewer_intent_summary="要求澄清协议",
                implicit_concerns=["可复现性"],
                paper_coverage_summary="方法节部分覆盖",
                required_questions=["样本量如何确定"],
            )
        if schema is ReplyStrategy:
            return ReplyStrategy(
                recommended_direction=ReplyDirection.ACCEPT_AND_REVISE,
                direction_rationale="事实已确认可接受修改",
                emphasis_points=["补充步骤"],
                avoid_points=["承诺新实验"],
                risk_flags=[],
            )
        if schema is ConsistencyReport:
            return ConsistencyReport(
                is_consistent=True,
                issues=[],
                cross_source_conflicts=[],
                reminders=[],
            )
        raise AssertionError(schema)

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    interpretation = reply_node.interpret_claim(source, analysis)
    strategy = reply_node.recommend_strategy(
        source, analysis, interpretation, [fact]
    )
    assert strategy.recommended_direction is ReplyDirection.ACCEPT_AND_REVISE

    facts = _response_facts(source.source_id, fact.fact_id)
    draft = ResponseDraft(
        draft_version_id=None,
        source_id=source.source_id,
        response_facts_version=1,
        language="英文",
        expression_settings_version=None,
        generated_content="We clarified the protocol.",
        user_edited_content=None,
        used_fact_ids=[fact.fact_id],
        consistency_check_result=None,
        stale_reason=None,
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    report = reply_node.check_consistency(
        source,
        strategy,
        facts,
        draft,
        [fact],
        [
            ApprovedSourceReply(
                source_id=uuid4(),
                generated_content="Other reply",
                linked_fact_ids=[fact.fact_id],
            )
        ],
    )
    assert report.is_consistent is True


def test_consistency_forces_inconsistent_when_issues(monkeypatch):
    suggestion_id = uuid4()
    source = _source(suggestion_id)
    fact = _fact(suggestion_id)
    strategy = ReplyStrategy(
        recommended_direction=ReplyDirection.ACCEPT_AND_REVISE,
        direction_rationale="r",
        emphasis_points=[],
        avoid_points=[],
        risk_flags=[],
    )
    facts = _response_facts(source.source_id, fact.fact_id)
    draft = ResponseDraft(
        draft_version_id=None,
        source_id=source.source_id,
        response_facts_version=1,
        language="英文",
        expression_settings_version=None,
        generated_content="We invented a new dataset.",
        user_edited_content=None,
        used_fact_ids=[fact.fact_id],
        consistency_check_result=None,
        stale_reason=None,
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    def fake_invoke(schema, *_args, **_kwargs):
        return ConsistencyReport(
            is_consistent=True,
            issues=[
                ConsistencyIssue(
                    issue_type=ConsistencyIssueType.UNSUPPORTED_FACT,
                    description="出现未支持事实",
                    related_fact_ids=[fact.fact_id],
                )
            ],
            cross_source_conflicts=[],
            reminders=[],
        )

    monkeypatch.setattr(reply_node, "_invoke_structured", fake_invoke)
    report = reply_node.check_consistency(source, strategy, facts, draft, [fact])
    assert report.is_consistent is False


def test_confirm_strategy_auto_accepts_recommendation_without_interrupt():
    proposal = {
        "recommended_direction": ReplyDirection.ACCEPT_AND_REVISE.value,
        "direction_rationale": "系统推荐接受并修改",
        "emphasis_points": ["说明修改点"],
        "avoid_points": ["承诺未做实验"],
        "risk_flags": [],
    }
    state = {
        "workspace_id": uuid4(),
        "suggestion_id": uuid4(),
        "source_id": uuid4(),
        "user_id": "tester",
        "run_id": uuid4(),
        "input_version": "v1",
        "phase": "CONFIRM_STRATEGY",
        "pending_interaction_id": uuid4(),
        "draft_refs": {"strategy_proposal": proposal},
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }
    result = confirm_strategy(state)
    assert result["phase"] == "BUILD_RESPONSE_FACTS"
    assert result["pending_interaction_id"] is None
    assert (
        result["draft_refs"]["confirmed_strategy"]["recommended_direction"]
        == ReplyDirection.ACCEPT_AND_REVISE.value
    )


def test_confirm_response_facts_auto_clears_unresolved_without_interrupt():
    fact_id = uuid4()
    source_id = uuid4()
    suggestion_id = uuid4()
    proposal = {
        "response_facts_id": None,
        "source_id": str(source_id),
        "selected_direction": ReplyDirection.ACCEPT_AND_REVISE.value,
        "acknowledgement": "感谢意见。",
        "direct_answer": "我们将补充说明。",
        "author_position": "接受。",
        "linked_fact_ids": [str(fact_id)],
        "fact_item_ids": [],
        "confirmed_revision_action_ids": [],
        "evidence_item_ids": [],
        "modification_locations": [],
        "limitation_fact_ids": [],
        "alternative_action_ids": [],
        "unresolved_items": ["未决提醒应清空"],
        "version": 1,
        "confirmation_status": "PROPOSED",
    }
    state = {
        "workspace_id": uuid4(),
        "suggestion_id": suggestion_id,
        "source_id": source_id,
        "user_id": "tester",
        "run_id": uuid4(),
        "input_version": "v1",
        "phase": "CONFIRM_RESPONSE_FACTS",
        "pending_interaction_id": uuid4(),
        "draft_refs": {
            "response_facts_proposal": proposal,
            "confirmed_modification_facts": [
                {
                    "fact_id": str(fact_id),
                    "suggestion_id": str(suggestion_id),
                    "action_type": "ACCEPT",
                    "paper_change_summary": "补充说明",
                    "response_fact_summary": "将补充说明",
                    "constraints": {},
                    "status": "CONFIRMED",
                    "input_version": "v1",
                }
            ],
        },
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }
    result = confirm_response_facts(state)
    assert result["phase"] == "GENERATE_DRAFT"
    assert result["pending_interaction_id"] is None
    confirmed = result["draft_refs"]["confirmed_response_facts"]
    assert confirmed["confirmation_status"] == "CONFIRMED"
    assert confirmed["unresolved_items"] == []
    assert confirmed["linked_fact_ids"] == [str(fact_id)]


def test_response_facts_interaction_exposes_unresolved_and_clear_flag():
    suggestion_id = uuid4()
    source_id = uuid4()
    fact_id = uuid4()
    workspace_id = uuid4()
    state = {
        "workspace_id": str(workspace_id),
        "suggestion_id": str(suggestion_id),
        "source_id": str(source_id),
        "run_id": str(uuid4()),
        "user_id": "user-1",
        "input_version": "iv-test",
        "thread_id": f"workspace:{workspace_id}:reply:{source_id}:digest",
        "pending_interaction_id": str(uuid4()),
        "draft_refs": {
            "response_facts_proposal": {
                "response_facts_id": None,
                "source_id": str(source_id),
                "selected_direction": "ACCEPT_AND_REVISE",
                "acknowledgement": "感谢。",
                "direct_answer": "将修订。",
                "author_position": "接受。",
                "linked_fact_ids": [str(fact_id)],
                "fact_item_ids": [],
                "confirmed_revision_action_ids": [],
                "evidence_item_ids": [],
                "modification_locations": [],
                "limitation_fact_ids": [],
                "alternative_action_ids": [],
                "unresolved_items": ["具体缺口：缺少样本量说明"],
                "version": 1,
                "confirmation_status": None,
            },
            "confirmed_modification_facts": [
                {
                    "fact_id": str(fact_id),
                    "suggestion_id": str(suggestion_id),
                    "action_type": "ACCEPT",
                    "paper_change_summary": "计划补充",
                    "response_fact_summary": "作者将补充",
                    "constraints": {},
                    "status": "CONFIRMED",
                    "input_version": "v1",
                }
            ],
        },
        "status": "WAITING_USER",
        "phase": "CONFIRM_RESPONSE_FACTS",
    }
    interaction = response_facts_interaction(state)
    assert interaction.context["unresolved_items"] == ["具体缺口：缺少样本量说明"]
    assert any("具体缺口" in item for item in interaction.blockers)
    clear_field = next(
        field for field in interaction.editable_fields if field.key == "clear_unresolved"
    )
    assert clear_field.type.value == "checkbox"
    assert clear_field.default is True


def test_stale_consistency_report_and_sync_helpers():
    report = stale_consistency_report({"is_consistent": False, "issues": [{"x": 1}]})
    assert report["stale"] is True
    assert report["issues"] == []
    assert report["previous_is_consistent"] is False

    primary_source = uuid4()
    primary_draft = uuid4()
    propagated = build_propagated_consistency_report(
        {"is_consistent": True},
        primary_source_id=primary_source,
        primary_draft_id=primary_draft,
    )
    assert propagated["propagated_from_source_id"] == str(primary_source)

    item = build_sibling_sync_item(
        source_id=uuid4(),
        reply_id=uuid4(),
        draft_id=uuid4(),
        mode="copied",
        superseded_run_id="run-1",
    )
    assert item["mode"] == "copied"
    assert item["superseded_run_id"] == "run-1"


def test_package_exports():
    assert callable(reply_pkg.build_source_reply_graph)
    assert callable(reply_pkg.build_reply_thread_id)
