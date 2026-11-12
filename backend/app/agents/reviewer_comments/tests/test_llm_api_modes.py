"""LLM 公共接口与 Chat Completions 模式边界测试（自 backend/tests 迁入）。"""

from __future__ import annotations

import inspect
from pathlib import Path

from pydantic import BaseModel

from langgraph_agent import llm
from langgraph_agent.llm import structured_output
from config.settings import get_settings


class _Schema(BaseModel):
    value: str


def test_invoke_structured_is_the_only_public_llm_interface():
    assert llm.__all__ == ["invoke_structured"]
    assert llm.invoke_structured is structured_output.invoke_structured
    assert list(inspect.signature(llm.invoke_structured).parameters) == [
        "purpose",
        "schema",
        "messages",
        "timeout_seconds",
    ]


def test_module_interface_delegates_to_default_engine(monkeypatch):
    expected = _Schema(value="ok")
    captured = {}

    class FakeEngine:
        def invoke_structured(self, purpose, schema, messages, *, timeout_seconds=None):
            captured.update(
                purpose=purpose,
                schema=schema,
                messages=messages,
                timeout_seconds=timeout_seconds,
            )
            return expected

    monkeypatch.setattr(structured_output, "_DEFAULT_ENGINE", FakeEngine())

    result = llm.invoke_structured(
        "draft",
        _Schema,
        [("human", "draft")],
        timeout_seconds=7,
    )

    assert result is expected
    assert captured == {
        "purpose": "draft",
        "schema": _Schema,
        "messages": [("human", "draft")],
        "timeout_seconds": 7,
    }


def test_all_business_purposes_resolve_models_from_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "MODEL_SPLIT", "split-model")
    monkeypatch.setattr(settings, "MODEL_ANALYZE", "analysis-model")
    monkeypatch.setattr(settings, "MODEL_DRAFT", "draft-model")
    monkeypatch.setattr(settings, "MODEL_PAPER_CARD", "card-model")

    assert settings.model_for("split") == "split-model"
    assert settings.model_for("analyze") == "analysis-model"
    assert settings.model_for("draft") == "draft-model"
    assert settings.model_for("paper_card") == "card-model"



def test_public_path_contains_no_responses_or_legacy_structured_clients():
    llm_dir = (
        Path(__file__).resolve().parents[1] / "src" / "langgraph_agent" / "llm"
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in llm_dir.glob("*.py"))

    assert "use_responses_api" not in source
    assert "with_structured_output" not in source
    assert "get_structured_llm" not in source
    assert "get_reply_structured_llm" not in source
    assert "get_card_structured_llm" not in source
    assert not (llm_dir / "client.py").exists()
