from types import SimpleNamespace

import pytest

from app import create_app
from app.agents.arxiv_daily.service import (
    ARXIV_CS_CATEGORIES,
    ArxivDailyService,
    _parse_cli_payload,
    normalize_arxiv_daily_options,
)
from app.api.academic_daily import (
    arxiv_article_id,
    candidate_pdf_urls,
    is_allowed_arxiv_pdf,
)
from app.services.catalog_setup import ARXIV_DAILY_AGENT


def test_arxiv_daily_options_validate_category_and_refresh():
    options = normalize_arxiv_daily_options(
        {"arxiv_category": "cs.LG", "arxiv_search": " foundation model ", "arxiv_refresh": "true"}
    )
    assert options == {
        "category": "cs.LG",
        "category_name": "机器学习",
        "search_query": "foundation model",
        "refresh": True,
    }
    assert len(ARXIV_CS_CATEGORIES) == 40
    with pytest.raises(ValueError, match="有效"):
        normalize_arxiv_daily_options({"arxiv_category": "math.AG"})


def test_arxiv_pdf_proxy_accepts_only_original_arxiv_pdf_urls():
    assert is_allowed_arxiv_pdf("https://arxiv.org/pdf/2607.01234")
    assert is_allowed_arxiv_pdf("https://cn.arxiv.org/pdf/2607.01234v2.pdf")
    assert is_allowed_arxiv_pdf("http://xxx.itp.ac.cn/pdf/cs/0601001")
    assert not is_allowed_arxiv_pdf("https://example.com/pdf/2607.01234")
    assert not is_allowed_arxiv_pdf("https://arxiv.org/abs/2607.01234")
    assert not is_allowed_arxiv_pdf("https://user@arxiv.org/pdf/2607.01234")
    assert arxiv_article_id("https://arxiv.org/pdf/2607.01234.pdf") == "2607.01234"
    candidates = candidate_pdf_urls("https://arxiv.org/pdf/2607.01234")
    assert "https://export.arxiv.org/pdf/2607.01234" in candidates
    assert "https://cn.arxiv.org/pdf/2607.01234" in candidates


def test_arxiv_daily_runtime_command_is_isolated(monkeypatch):
    app = create_app({"TESTING": True, "ARXIV_DAILY_TIMEOUT_SECONDS": 37})
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout='{"categories":[{"code":"cs.AI","name_cn":"人工智能"}],"papers":[]}',
            stderr="",
        )

    monkeypatch.setattr("app.agents.arxiv_daily.service.subprocess.run", fake_run)
    with app.app_context():
        payload = ArxivDailyService(app)._fetch_snapshot("cs.AI")
    assert payload["categories"][0]["code"] == "cs.AI"
    assert captured["command"][-2:] == ["--category", "cs.AI"]
    assert captured["timeout"] == 37
    assert captured["cwd"].endswith("arxiv_daily\\runtime")
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"


def test_arxiv_daily_cli_parser_and_catalog_contract():
    assert _parse_cli_payload('source log\n{"papers":[{"arxiv_id":"2607.1"}]}') == {
        "papers": [{"arxiv_id": "2607.1"}]
    }
    assert ARXIV_DAILY_AGENT["code"] == "arxiv_daily"
    assert ARXIV_DAILY_AGENT["config_json"]["route"] == "/agents/academic-daily"
    assert ARXIV_DAILY_AGENT["config_json"]["runtime"] == "arxiv-daily-agent-v0.1.0"
