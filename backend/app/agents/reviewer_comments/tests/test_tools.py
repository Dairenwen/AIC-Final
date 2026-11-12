"""A6 tools 纯函数单测：不依赖外网 / 真实 PDF / 真实 LLM。"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from langgraph_agent.tools.export_files import (
    export_download_meta,
    generate_export_files,
    group_external_replies_by_party,
    normalize_export_format,
    render_export_markdown,
    resolve_registered_export_path,
)
from langgraph_agent.tools.paper_card import (
    generate_paper_cards,
    generate_paper_cards_with_status,
    generate_rule_based_paper_cards,
)
from langgraph_agent.tools.paper_evidence import (
    build_card_route,
    build_section_route,
    select_paper_excerpts,
)
from langgraph_agent.tools.paper_schemas import (
    CardType,
    LlmPaperCardBatch,
    LlmPaperCardCandidate,
    PaperCard,
    PaperSection,
    ParsedPaper,
    SectionType,
)
from langgraph_agent.tools.pdf_parse import (
    build_parsed_paper_from_markdown_chunks,
    normalize_section_type,
    parse_pdf,
)


# ---------------------------------------------------------------------------
# paper_schemas / pdf_parse
# ---------------------------------------------------------------------------


def test_normalize_section_type_keywords() -> None:
    assert normalize_section_type("1 Introduction")[0] is SectionType.INTRODUCTION
    assert normalize_section_type("2.1 Related Work")[0] is SectionType.RELATED_WORK
    assert normalize_section_type("Methodology")[0] is SectionType.METHOD
    assert normalize_section_type("摘要")[0] is SectionType.ABSTRACT
    assert normalize_section_type("Random heading XYZ")[0] is SectionType.OTHER


def test_build_parsed_paper_from_markdown_chunks() -> None:
    chunks = [
        {
            "metadata": {"page_number": 1},
            "text": (
                "# AblationBench\n\n"
                "## Abstract\n"
                "We study automated ablation planning.\n\n"
                "## 1 Introduction\n"
                "Scientific papers need better evaluation protocols.\n"
            ),
        },
        {
            "metadata": {"page_number": 2},
            "text": (
                "## 2 Method\n"
                "Our pipeline converts PDF pages to Markdown.\n\n"
                "## 3 Experiments\n"
                "We measure section recall on a small set.\n\n"
                "## 4 Results\n"
                "The pipeline recovers major headings.\n"
            ),
        },
    ]
    paper = build_parsed_paper_from_markdown_chunks(chunks)
    assert paper.title == "AblationBench"
    assert "ablation" in paper.abstract.lower() or paper.abstract
    assert paper.full_text
    types = {section.normalized_type for section in paper.sections}
    assert SectionType.ABSTRACT in types
    assert SectionType.METHOD in types or SectionType.INTRODUCTION in types
    assert all(section.section_id for section in paper.sections)
    assert all(section.pages for section in paper.sections)
    payload = paper.to_dict()
    assert payload["title"] == "AblationBench"
    assert isinstance(payload["sections"], list)


def test_parse_pdf_missing_dependency_or_unreadable() -> None:
    """无真实 PDF 时：坏字节应进入明确降级，不抛未捕获异常。"""
    result = parse_pdf(b"not-a-pdf")
    assert isinstance(result, ParsedPaper)
    assert result.full_text == "" or result.parse_warnings
    assert any(
        token in warning
        for warning in result.parse_warnings
        for token in (
            "UNREADABLE_PDF",
            "DEGRADED_PARSER",
            "PARSER_UNAVAILABLE",
        )
    )


# ---------------------------------------------------------------------------
# paper_evidence
# ---------------------------------------------------------------------------


def test_build_section_and_card_route_primary() -> None:
    classification = {
        "primary_type": "METHOD_THEORY",
        "target_subtype": "METHOD_CLARITY",
    }
    # METHOD_CLARITY 只覆盖卡片路由；章节仍走主类型 METHOD_THEORY
    assert build_section_route(classification) == [SectionType.METHOD.value]
    assert build_card_route(classification) == [CardType.MAIN_METHOD.value]

    primary_only = {"primary_type": "DATA_SAMPLE", "target_subtype": ""}
    assert build_section_route(primary_only) == [
        SectionType.DATASET.value,
        SectionType.EXPERIMENTS.value,
    ]
    assert build_card_route(primary_only) == [CardType.DATASET_OR_SAMPLE.value]


def test_build_section_route_subtype_override() -> None:
    classification = {
        "primary_type": "EXPERIMENT_EVALUATION",
        "target_subtype": "ABLATION_STUDY",
    }
    assert build_section_route(classification) == [
        SectionType.ABLATION.value,
        SectionType.RESULTS.value,
        SectionType.EXPERIMENTS.value,
    ]
    assert build_card_route(classification) == [
        CardType.ABLATION_OR_SUPPLEMENTARY_ANALYSIS.value,
        CardType.MAIN_RESULTS.value,
    ]


def test_build_route_prefers_confirmed_result() -> None:
    classification = {
        "automatic_result": {
            "primary_type": "METHOD_THEORY",
            "target_subtype": "",
        },
        "confirmed_result": {
            "primary_type": "DATA_SAMPLE",
            "target_subtype": "",
        },
    }
    assert build_section_route(classification)[0] == SectionType.DATASET.value


def test_select_paper_excerpts_from_cards_and_abstract() -> None:
    classification = {
        "primary_type": "RESEARCH_POSITIONING_CONTRIBUTION",
        "target_subtype": "RESEARCH_QUESTION",
    }
    cards = [
        {
            "card_type": CardType.RESEARCH_QUESTION.value,
            "content": "论文研究自动消融规划。",
            "source_sections": ["Abstract"],
            "source_quote": "We ask whether language models can plan rigorous ablations.",
        },
        {
            "card_type": CardType.MAIN_METHOD.value,
            "content": "方法卡不应被研究问题路由选中。",
            "source_sections": ["Method"],
            "source_quote": "Our method converts pages to Markdown.",
        },
    ]
    sections = [
        {
            "original_heading": "Abstract",
            "normalized_type": SectionType.ABSTRACT.value,
            "pages": [1],
        },
        {
            "original_heading": "Method",
            "normalized_type": SectionType.METHOD.value,
            "pages": [2],
        },
    ]
    excerpts = select_paper_excerpts(
        classification,
        cards,
        sections,
        abstract="We study reliable parsing of scientific PDF documents.",
    )
    assert excerpts
    assert excerpts[0]["quote"].startswith("We ask whether")
    assert excerpts[0].get("section") == "Abstract"
    assert excerpts[0].get("location") == "p.1"
    # RESEARCH_QUESTION 子类型卡片路由仅 RESEARCH_QUESTION，不取 MAIN_METHOD
    assert all(
        "converts pages" not in item["quote"] for item in excerpts
    )


# ---------------------------------------------------------------------------
# paper_card（规则路径 + mock LLM）
# ---------------------------------------------------------------------------


ABSTRACT_TEXT = (
    "Automated ablation planning remains difficult because researchers must identify "
    "which design choices deserve controlled comparison. We ask whether language "
    "models can plan rigorous ablations. We introduce AblationBench, a benchmark for "
    "evaluating those plans."
)


def _abstract_paper() -> ParsedPaper:
    section = PaperSection(
        original_heading="Abstract",
        normalized_type=SectionType.ABSTRACT,
        text=ABSTRACT_TEXT,
        pages=[1],
        confidence=0.95,
        section_id="section-0001",
    )
    return ParsedPaper(
        title="AblationBench",
        abstract=ABSTRACT_TEXT,
        full_text=ABSTRACT_TEXT,
        sections=[section],
    )


def test_generate_rule_based_paper_cards() -> None:
    cards = generate_rule_based_paper_cards(_abstract_paper())
    assert cards
    assert all(isinstance(card, PaperCard) for card in cards)
    assert all(card.content != card.source_quote for card in cards)
    assert all(card.source_quote for card in cards)
    # 摘要证据通常覆盖研究问题/动机/贡献，不应凭空造 LIMITATIONS
    assert CardType.LIMITATIONS not in {card.card_type for card in cards}


def test_generate_paper_cards_with_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import langgraph_agent.tools.paper_card as card_module

    quote = "We ask whether language models can plan rigorous ablations."
    batch = LlmPaperCardBatch(
        cards=[
            LlmPaperCardCandidate(
                card_type=CardType.RESEARCH_QUESTION,
                content="论文研究语言模型能否规划严谨的消融实验。",
                source_section_id="section-0001",
                source_quote=quote,
                confidence=0.94,
            )
        ]
    )
    def _mock_invoke(purpose, schema, messages, **kwargs):
        # 仅对含 RESEARCH_QUESTION 的批次返回候选；其余返回空列表（证据不足，非失败）
        human = ""
        for role, content in messages:
            if role == "human":
                human = str(content)
        if "RESEARCH_QUESTION" in human:
            return batch
        return LlmPaperCardBatch(cards=[])

    monkeypatch.setattr(card_module, "invoke_structured", _mock_invoke)
    # 强制串行，避免线程池干扰 mock
    monkeypatch.setattr(
        card_module,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "MODEL_PAPER_CARD": "mock-model",
                "PAPER_CARD_LLM_TIMEOUT_SECONDS": 30.0,
                "PAPER_CARD_MAX_WORKERS": 1,
            },
        )(),
    )
    result = generate_paper_cards_with_status(_abstract_paper())
    assert result.fallback_used is False
    assert any(card.card_type is CardType.RESEARCH_QUESTION for card in result.cards)
    assert all(card.source_quote for card in result.cards)


def test_generate_paper_cards_falls_back_on_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langgraph_agent.tools.paper_card as card_module

    monkeypatch.setattr(
        card_module,
        "invoke_structured",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model down")),
    )
    monkeypatch.setattr(
        card_module,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "MODEL_PAPER_CARD": "mock-model",
                "PAPER_CARD_LLM_TIMEOUT_SECONDS": 30.0,
                "PAPER_CARD_MAX_WORKERS": 1,
            },
        )(),
    )
    result = generate_paper_cards_with_status(_abstract_paper())
    assert result.fallback_used is True
    assert result.cards  # 规则降级仍可产出
    assert generate_paper_cards(_abstract_paper())  # 兼容 API


def test_fabricated_source_quote_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import langgraph_agent.tools.paper_card as card_module

    batch = LlmPaperCardBatch(
        cards=[
            LlmPaperCardCandidate(
                card_type=CardType.RESEARCH_QUESTION,
                content="论文研究自动消融规划。",
                source_section_id="section-0001",
                source_quote="This sentence does not occur anywhere in the paper.",
                confidence=0.99,
            )
        ]
    )
    monkeypatch.setattr(
        card_module,
        "invoke_structured",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        card_module,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "MODEL_PAPER_CARD": "mock-model",
                "PAPER_CARD_LLM_TIMEOUT_SECONDS": 30.0,
                "PAPER_CARD_MAX_WORKERS": 1,
            },
        )(),
    )
    result = generate_paper_cards_with_status(_abstract_paper())
    assert result.fallback_used is True


# ---------------------------------------------------------------------------
# export_files
# ---------------------------------------------------------------------------


def test_normalize_export_format_and_meta() -> None:
    assert normalize_export_format("md") == "MARKDOWN"
    assert normalize_export_format("DOCX") == "WORD"
    assert normalize_export_format("xlsx") == "EXCEL"
    assert normalize_export_format("nope") is None
    name, content_type = export_download_meta("MARKDOWN")
    assert name.endswith(".md")
    assert "markdown" in content_type


def test_group_external_replies_by_party_ordering() -> None:
    replies = [
        {
            "party_id": "r1",
            "party_display_name": "Reviewer 1",
            "party_role": "REVIEWER",
            "reply_status": "APPROVED",
            "draft_status": "APPROVED",
            "content": "感谢意见，我们已修改方法描述。",
            "excerpt": "方法不够清晰",
        },
        {
            "party_id": "e1",
            "party_display_name": "Editor",
            "party_role": "EDITOR",
            "reply_status": "APPROVED",
            "draft_status": "APPROVED",
            "content": "感谢编辑的统筹意见。",
            "excerpt": "请统一回复格式",
            "localized_claim": "格式需统一",
        },
        {
            "party_id": "r1",
            "party_display_name": "Reviewer 1",
            "party_role": "REVIEWER",
            "reply_status": "APPROVED",
            "draft_status": "APPROVED",
            "content": "第二点已补充实验。",
            "excerpt": "缺少基线",
        },
        {
            "party_id": "skip",
            "party_display_name": "Draft",
            "party_role": "REVIEWER",
            "reply_status": "DRAFT",
            "draft_status": "DRAFT",
            "content": "不应导出",
        },
    ]
    groups = group_external_replies_by_party(replies)
    assert [g["party_id"] for g in groups] == ["e1", "r1"]
    assert groups[0]["replies"][0]["opinion_no"] == 1
    assert len(groups[1]["replies"]) == 2
    assert groups[1]["replies"][1]["opinion_no"] == 2


def test_render_export_markdown_and_generate_files() -> None:
    workspace_id = uuid4()
    snapshot_id = uuid4()
    snapshot = {
        "workspace_id": str(workspace_id),
        "export_snapshot_id": str(snapshot_id),
        "workspace_title": "样例工作区",
        "external_replies": [
            {
                "party_id": "r1",
                "party_display_name": "Reviewer 1",
                "party_role": "REVIEWER",
                "reply_status": "APPROVED",
                "draft_status": "APPROVED",
                "content": "我们已在第 3 节补充说明。",
                "excerpt": "方法描述不足",
            }
        ],
        "internal_revision_items": [
            {
                "canonical_text": "补充方法细节",
                "priority": "HIGH",
                "modification_facts": ["新增 3.2 节"],
                "source_labels": ["R1-1"],
            }
        ],
    }
    markdown = render_export_markdown(snapshot)
    assert "样例工作区" in markdown
    assert "Reviewer 1" in markdown
    assert "补充方法细节" in markdown

    outputs = generate_export_files(snapshot)
    assert {item["format"] for item in outputs} == {"MARKDOWN", "WORD", "EXCEL"}
    assert all(item["content_hash"] and item["size_bytes"] > 0 for item in outputs)

    # 路径安全：仅允许导出根下相对路径
    for item in outputs:
        path = resolve_registered_export_path(item["storage_uri"])
        assert path.exists()
    with pytest.raises(ValueError):
        resolve_registered_export_path("../secrets.txt")
    with pytest.raises(ValueError):
        resolve_registered_export_path(str(Path.cwd() / "abs.txt"))

    # 清理本测生成的导出目录
    export_dir = (
        Path(__file__).resolve().parents[1]
        / ".tmp"
        / "finalize_exports"
        / str(workspace_id)
    )
    if export_dir.exists():
        shutil.rmtree(export_dir, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.skip(reason="依赖真实 PDF 与 pymupdf4llm，本地手工验证时取消 skip")
def test_parse_real_pdf_skipped() -> None:
    parse_pdf("assets/examples/sample.pdf")
