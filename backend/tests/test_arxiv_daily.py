from pathlib import Path
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
    assert Path(captured["cwd"]).parts[-2:] == ("arxiv_daily", "runtime")
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"


def test_arxiv_daily_cli_parser_and_catalog_contract():
    assert _parse_cli_payload('source log\n{"papers":[{"arxiv_id":"2607.1"}]}') == {
        "papers": [{"arxiv_id": "2607.1"}]
    }
    assert ARXIV_DAILY_AGENT["code"] == "arxiv_daily"
    assert ARXIV_DAILY_AGENT["config_json"]["route"] == "/agents/academic-daily"
    assert ARXIV_DAILY_AGENT["config_json"]["runtime"] == "arxiv-daily-agent-v0.1.0"


def _paper(arxiv_id: str) -> dict:
    return {"arxiv_id": arxiv_id, "title": f"Paper {arxiv_id}", "abstract": "We study things."}


def test_translate_papers_reports_real_progress_and_reuses_cache(monkeypatch):
    app = create_app()
    service = ArxivDailyService(app)
    events: list[tuple[int, dict]] = []
    monkeypatch.setattr(service, "emit", lambda task, event_type, progress, message, **payload: events.append((progress, payload)))
    monkeypatch.setattr(
        service,
        "_cached_translations",
        lambda ids: {"1": {"title_cn": "旧标题", "summary_cn": "旧总结", "abstract_cn": "旧摘要", "cn_generated": True}},
    )
    calls: list[str] = []

    def fake_translate(paper):
        calls.append(paper["arxiv_id"])
        if paper["arxiv_id"] == "3":
            return None
        return {"title_cn": "新标题", "summary_cn": "新总结", "abstract_cn": "新摘要", "cn_generated": True}

    monkeypatch.setattr("app.agents.arxiv_daily.service._translate_one", fake_translate)
    papers, missing = service._translate_papers(SimpleNamespace(id="t"), [_paper("1"), _paper("2"), _paper("3")])

    assert sorted(calls) == ["2", "3"]  # the cached paper is not sent to the model again
    assert missing == 1
    assert papers[0]["title_cn"] == "旧标题" and papers[1]["title_cn"] == "新标题"
    assert "title_cn" not in papers[2]
    counts = [payload["done"] for _, payload in events]
    assert counts == [1, 2, 3]  # one event before work, then one per finished paper
    assert all(payload["total"] == 3 for _, payload in events)
    progresses = [progress for progress, _ in events]
    assert progresses == sorted(progresses) and progresses[-1] == 95


def test_translate_one_parses_model_json(monkeypatch):
    monkeypatch.setattr(
        "app.agents.arxiv_daily.service.run_openai_compatible_chat",
        lambda **kwargs: {"content": '```json\n{"title_cn": "标题", "summary_cn": "总结", "abstract_cn": "摘要"}\n```'},
    )
    from app.agents.arxiv_daily.service import _translate_one

    assert _translate_one(_paper("9")) == {"title_cn": "标题", "summary_cn": "总结", "abstract_cn": "摘要", "cn_generated": True}
    assert _translate_one({"arxiv_id": "9", "title": "", "abstract": "x"}) is None


def test_arxiv_feed_parser_reads_official_atom_entries(monkeypatch):
    import sys

    runtime = Path(__file__).resolve().parents[1] / "app/agents/arxiv_daily/runtime/agent-core"
    monkeypatch.syspath_prepend(str(runtime))
    for name in [key for key in sys.modules if key in ("config", "src") or key.startswith(("config.", "src."))]:
        monkeypatch.delitem(sys.modules, name)
    from src.tools.arxivdaily import ArxivDailyScraper

    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2611.14519v2</id>
    <updated>2026-11-13T10:00:00Z</updated>
    <published>2026-11-11T10:00:00Z</published>
    <title>Medical   Image
      Segmentation</title>
    <summary>  Foundation models are adapted.  </summary>
    <author><name>Emre Kutlusan</name><arxiv:affiliation>Brunel University</arxiv:affiliation></author>
    <author><name>Yongmin Li</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2611.14519v2" rel="related"/>
    <category term="cs.CV"/><category term="cs.AI"/>
  </entry>
</feed>"""
    [paper] = ArxivDailyScraper._parse_feed(feed, "cs.AI")
    data = paper.to_dict()
    assert data["arxiv_id"] == "2611.14519"
    assert data["title"] == "Medical Image Segmentation"
    assert data["abstract"] == "Foundation models are adapted."
    assert data["authors"] == "Emre Kutlusan, Yongmin Li"
    assert data["pdf_url"] == "https://arxiv.org/pdf/2611.14519v2"
    assert data["categories"] == ["cs.CV", "cs.AI"]
    assert data["updated"] == "2026-11-11"
