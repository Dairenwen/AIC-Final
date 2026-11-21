"""WorkspaceTask 纯节点单测：extract / split / relation / persist 分组。"""

from __future__ import annotations

from uuid import uuid4

from langgraph_agent.agent.workspace_task import extract_node
from langgraph_agent.agent.workspace_task import split_node
from langgraph_agent.agent.workspace_task.persist import (
    choose_canonical_text,
    group_confirmed_suggestions,
)
from langgraph_agent.agent.workspace_task.relation_node import (
    apply_relation_confirmation,
    build_relation_proposals,
    detect_relation_type,
    detect_relations,
    explain_relation,
)
from langgraph_agent.schemas import LlmSplitResult, SplitCandidate


# ---- extract ----


def test_extract_numbered_items_and_parties() -> None:
    party_id = uuid4()
    input_id = uuid4()
    result = extract_node.extract_parties_and_items(
        [
            {
                "review_input_id": input_id,
                "party_id": party_id,
                "role": "REVIEWER",
                "display_name": "R1",
                "raw_label": "Reviewer #1",
                "raw_text": (
                    "1. Please clarify the sampling procedure.\n"
                    "2. Please report confidence intervals."
                ),
                "language": "en",
            }
        ]
    )
    assert len(result.parties) == 1
    assert result.parties[0].display_name == "R1"
    assert len(result.items) == 2
    assert result.items[0].original_item_number == "1"
    assert "sampling" in result.items[0].original_text
    assert result.items[1].source_order == 2


def test_extract_bullet_and_paragraph_fallback() -> None:
    party_id = uuid4()
    input_id = uuid4()
    result = extract_node.extract_parties_and_items(
        [
            {
                "review_input_id": input_id,
                "party_id": party_id,
                "role": "REVIEWER",
                "display_name": "R1",
                "raw_label": "R1",
                "raw_text": "- First issue about data.\n- Second issue about figures.",
                "language": None,
            }
        ]
    )
    assert len(result.items) == 2
    assert result.items[0].original_item_number is None


# ---- split（mock LLM）----


def test_split_skips_bad_source_quote(monkeypatch) -> None:
    original = "请补充各模块的消融实验，并写清训练集划分。"

    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        assert purpose == "split"
        return LlmSplitResult(
            review_points=[
                SplitCandidate(
                    atomic_concern="补充消融实验",
                    explicit_request="请补充消融实验",
                    implicit_concern=None,
                    source_quote="这是模型编造的引用，原文没有",
                    split_confidence=0.9,
                ),
                SplitCandidate(
                    atomic_concern="写清训练集划分",
                    explicit_request="写清训练集划分",
                    implicit_concern=None,
                    source_quote="并写清训练集划分",
                    split_confidence=0.9,
                ),
            ]
        )

    monkeypatch.setattr(split_node, "invoke_structured", fake_invoke)
    result = split_node.split_review_points(original)
    assert len(result.review_points) == 1
    assert result.review_points[0].atomic_concern == "写清训练集划分"
    assert "训练集划分" in result.review_points[0].source_quote


def test_split_praise_only_returns_empty(monkeypatch) -> None:
    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        return LlmSplitResult(
            review_points=[
                SplitCandidate(
                    atomic_concern="写得很好",
                    explicit_request=None,
                    implicit_concern=None,
                    source_quote="This paper is well written.",
                    split_confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(split_node, "invoke_structured", fake_invoke)
    result = split_node.split_review_points("This paper is well written.")
    assert result.review_points == []


def test_split_sqrt_symbol_normalization(monkeypatch) -> None:
    original = "请解释缩放点积中 1/√d_k 的动机。"

    def fake_invoke(purpose, schema, messages, *, timeout_seconds=None):
        return LlmSplitResult(
            review_points=[
                SplitCandidate(
                    atomic_concern="解释 1/sqrt(d_k) 的动机",
                    explicit_request="请解释动机",
                    implicit_concern=None,
                    # 归一化后 √→sqrt 且去空白，故 quote 用 sqrtd_k 形态
                    source_quote="1/sqrtd_k 的动机",
                    split_confidence=0.8,
                )
            ]
        )

    monkeypatch.setattr(split_node, "invoke_structured", fake_invoke)
    result = split_node.split_review_points(original)
    assert len(result.review_points) == 1


# ---- relation ----


def test_almost_identical_texts_are_shared() -> None:
    assert (
        detect_relation_type(
            "Please clarify the sampling procedure",
            "Please clarify the sampling procedure",
        )
        == "SHARED"
    )
    assert (
        detect_relation_type(
            "Please add ablation studies",
            "Please include an ablation study",
        )
        == "SHARED"
    )
    assert (
        detect_relation_type(
            "建议补充消融实验",
            "请增加消融实验以验证模块贡献",
        )
        == "SHARED"
    )


def test_same_topic_different_action_is_conflict() -> None:
    assert (
        detect_relation_type(
            "Please increase the sample size",
            "Please decrease the sample size",
        )
        == "CONFLICT"
    )
    assert detect_relation_type("请增加样本量", "请减少样本量") == "CONFLICT"


def test_weakly_related_and_unrelated() -> None:
    assert (
        detect_relation_type(
            "Clarify the sampling procedure",
            "The sampling procedure uses stratified folds",
        )
        == "RELATED"
    )
    assert (
        detect_relation_type(
            "Please report training time",
            "Please clarify dataset split",
        )
        is None
    )


def test_explanation_is_chinese_and_mentions_type() -> None:
    text = explain_relation(
        "Please increase the sample size",
        "Please decrease the sample size",
        "CONFLICT",
    )
    assert "CONFLICT" in text
    assert "相反" in text or "动作" in text


def test_build_relation_proposals_and_default_approve_all() -> None:
    suggestions = [
        {
            "proposal_id": "S-001-P-01",
            "canonical_text": "Please add ablation studies",
            "sources": [{"party_id": "p1"}],
        },
        {
            "proposal_id": "S-002-P-01",
            "canonical_text": "Please include an ablation study",
            "sources": [{"party_id": "p2"}],
        },
        {
            "proposal_id": "S-003-P-01",
            "canonical_text": "Please report training time",
            "sources": [{"party_id": "p3"}],
        },
    ]
    relations = build_relation_proposals(suggestions)
    assert len(relations) == 1
    assert relations[0]["type"] == "SHARED"

    persistable, confirmed = apply_relation_confirmation(
        suggestions, relations, payload={}
    )
    assert len(confirmed) == 1
    assert persistable[0]["merge_group_key"]
    assert persistable[1]["merge_group_key"] == persistable[0]["merge_group_key"]
    assert persistable[2].get("merge_group_key") is None


def test_detect_relations_node_writes_draft_refs() -> None:
    state = {
        "run_id": uuid4(),
        "workspace_id": uuid4(),
        "draft_refs": {
            "confirmed_suggestions": [
                {
                    "proposal_id": "S-001-P-01",
                    "canonical_text": "Please increase the sample size",
                },
                {
                    "proposal_id": "S-002-P-01",
                    "canonical_text": "Please decrease the sample size",
                },
            ]
        },
    }
    result = detect_relations(state)  # type: ignore[arg-type]
    assert result["phase"] == "CONFIRM_RELATIONS"
    relations = result["draft_refs"]["relation_proposals"]  # type: ignore[index]
    assert len(relations) == 1
    assert relations[0]["type"] == "CONFLICT"


# ---- persist pure helpers ----


def test_choose_canonical_text_prefers_longer_when_not_highly_similar() -> None:
    short = "补充消融实验"
    long = "建议补充各模块的消融实验，以说明各模块对最终性能的独立贡献。"
    assert choose_canonical_text([short, long]) == long
    assert choose_canonical_text([long, short]) == long


def test_choose_canonical_text_keeps_first_when_highly_similar() -> None:
    first = "Please clarify the sampling procedure."
    second = "Please clarify the sampling procedure"
    assert choose_canonical_text([first, second]) == first


def test_group_shared_merge_keeps_all_sources() -> None:
    group_key = "shared:R-S-001-P-01-S-002-P-01"
    proposals = [
        {
            "proposal_id": "S-001-P-01",
            "canonical_text": "Please add ablation studies",
            "merge_group_key": group_key,
            "conflict_group_key": None,
            "sources": [{"party_id": "reviewer-1", "excerpt": "a"}],
        },
        {
            "proposal_id": "S-002-P-01",
            "canonical_text": "Please include an ablation study to show module contributions.",
            "merge_group_key": group_key,
            "conflict_group_key": None,
            "sources": [{"party_id": "reviewer-2", "excerpt": "b"}],
        },
        {
            "proposal_id": "S-003-P-01",
            "canonical_text": "Please report training time",
            "merge_group_key": None,
            "conflict_group_key": None,
            "sources": [{"party_id": "reviewer-3", "excerpt": "c"}],
        },
    ]
    grouped = group_confirmed_suggestions(proposals)
    assert len(grouped) == 2
    merged = next(item for item in grouped if item.get("merge_group_key") == group_key)
    standalone = next(item for item in grouped if item.get("merge_group_key") is None)
    assert len(merged["sources"]) == 2
    assert len(standalone["sources"]) == 1
