from __future__ import annotations

from flask import Response

from app import create_app
from app.api.research_tools import analyze_text, convert_table, export_markdown_docx, format_citation, normalize_literature_ppt_options
from app.tools.literature_ppt import pptx_slide_count, valid_pptx
from app.services.catalog_setup import BUILTIN_RESEARCH_TOOLS


def _post(app, endpoint, payload):
    with app.test_request_context(endpoint, method="POST", json=payload):
        return app.ensure_sync({
            "citation": format_citation,
            "table": convert_table,
            "text": analyze_text,
            "docx": export_markdown_docx,
        }[endpoint.strip("/")])()


def test_citation_formatter_outputs_common_styles():
    app = create_app({"TESTING": True})
    response, status = _post(app, "/citation", {
        "title": "Dynamic Retrieval-Augmented Generation",
        "authors": "Li Ming; Wang Wei",
        "year": 2026,
        "venue": "Journal of AI Research",
        "entryType": "article",
        "doi": "10.1000/example",
    })
    data = response.get_json()["data"]
    assert status == 200
    assert "@article{" in data["bibtex"]
    assert "Journal of AI Research" in data["apa"]
    assert "[J]" in data["gbt7714"]


def test_table_converter_parses_tsv_and_escapes_outputs():
    app = create_app({"TESTING": True})
    response, status = _post(app, "/table", {
        "source": "Model\tAccuracy (%)\nBaseline\t82.3\nOurs\t91.6",
        "delimiter": "auto",
        "caption": "Results & comparison",
        "label": "main_results",
    })
    data = response.get_json()["data"]
    assert status == 200
    assert data["rowCount"] == 3
    assert "| Model | Accuracy (%) |" in data["markdown"]
    assert r"Results \& comparison" in data["latex"]
    assert r"\begin{tabular}{lc}" in data["latex"]


def test_text_statistics_handles_bilingual_academic_text():
    app = create_app({"TESTING": True})
    response, status = _post(app, "/text", {"text": "动态检索能够更新知识。Dynamic retrieval improves accuracy.\n\n第二段。"})
    data = response.get_json()["data"]
    assert status == 200
    assert data["chineseCharacters"] > 0
    assert data["englishWords"] == 4
    assert data["paragraphs"] == 2
    assert data["sentences"] >= 3


def test_markdown_export_returns_valid_docx_zip():
    app = create_app({"TESTING": True})
    result = _post(app, "/docx", {"markdown": "# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |", "filename": "实验报告"})
    assert isinstance(result, Response)
    assert result.status_code == 200
    result.direct_passthrough = False
    assert result.get_data()[:2] == b"PK"
    assert "application/vnd.openxmlformats" in result.content_type


def test_builtin_catalog_contains_six_routable_tools():
    assert len(BUILTIN_RESEARCH_TOOLS) == 6
    assert {item["config_json"]["route"] for item in BUILTIN_RESEARCH_TOOLS} == {
        "/tools/formula-to-latex",
        "/tools/citation-formatter",
        "/tools/table-converter",
        "/tools/text-statistics",
        "/tools/markdown-to-docx",
        "/tools/literature-ppt",
    }


def test_literature_ppt_options_validate_slide_range():
    values = normalize_literature_ppt_options({
        "audience": "课题组",
        "slides": "10",
        "focus": "方法,实验",
    })
    assert values["slides"] == 10
    assert values["audience"] == "课题组"


def test_invalid_file_is_not_a_pptx(tmp_path):
    path = tmp_path / "invalid.pptx"
    path.write_bytes(b"not-a-presentation")
    assert valid_pptx(path) is False
