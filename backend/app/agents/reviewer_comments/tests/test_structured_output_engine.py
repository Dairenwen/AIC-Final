"""StructuredOutputEngine 离线能力协商与韧性测试。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict, Field

from config.settings import get_settings
from langgraph_agent.llm.resilience import LlmFinalError, LlmRetryableError
from langgraph_agent.llm.structured_output import StructuredOutputEngine


class ProbeResult(BaseModel):
    answer: str = Field(min_length=2)
    confidence: float = Field(ge=0, le=1)
    tags: list[str] = Field(min_length=1)


class LlmResponseDraft(BaseModel):
    """本地最小草稿 schema（对齐 backend LlmResponseDraft，避免跨包依赖 A3）。"""

    model_config = ConfigDict(extra="forbid")

    generated_content: str = Field(min_length=1)
    used_fact_ids: list[UUID] = Field(min_length=1)


class FakeCompletions:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.handler(kwargs, len(self.calls))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _response(*, content: str | None = None, tool_arguments: dict | None = None):
    message: dict[str, Any] = {"content": content}
    if tool_arguments is not None:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    "name": "ProbeResult",
                    "arguments": json.dumps(tool_arguments, ensure_ascii=False),
                },
            }
        ]
    return {"choices": [{"message": message}]}


def _valid_payload() -> dict[str, Any]:
    return {"answer": "ok", "confidence": 0.9, "tags": ["probe"]}


def _bad_request(message: str, *, param: str, code: str = "unsupported_parameter"):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(
        message,
        response=response,
        body={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": param,
                "code": code,
            }
        },
    )


def _rate_limit_error():
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("rate limited", response=response, body=None)


def _server_error():
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(503, request=request)
    return openai.InternalServerError("unavailable", response=response, body=None)


@pytest.fixture
def configured_llm(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "super-secret-key")
    monkeypatch.setattr(settings, "MODEL_SPLIT", "model-a")
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 12.0)
    monkeypatch.setattr(settings, "LLM_EXTRA_BODY", {})


def _engine(handler, *, max_retries: int = 3):
    completions = FakeCompletions(handler)
    factory_calls: list[dict[str, Any]] = []

    def client_factory(**kwargs):
        factory_calls.append(kwargs)
        return FakeClient(completions)

    engine = StructuredOutputEngine(
        client_factory=client_factory,
        sleep=lambda _delay: None,
        max_retries_by_purpose={"split": max_retries},
    )
    return engine, completions, factory_calls


def _invoke(engine: StructuredOutputEngine) -> ProbeResult:
    return engine.invoke_structured(
        "split",
        ProbeResult,
        [{"role": "user", "content": "Return the probe result."}],
    )


def test_native_json_schema_is_selected(configured_llm):
    engine, completions, factory_calls = _engine(
        lambda _kwargs, _index: _response(content=json.dumps(_valid_payload()))
    )

    assert _invoke(engine) == ProbeResult.model_validate(_valid_payload())

    request = completions.calls[0]
    assert request["model"] == "model-a"
    assert request["response_format"]["type"] == "json_schema"
    assert "tools" not in request
    assert "tool_choice" not in request
    assert factory_calls[0]["max_retries"] == 0
    assert factory_calls[0]["timeout"] == 12.0


def test_falls_back_to_forced_tool(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _bad_request(
                "Unsupported parameter: response_format.type=json_schema",
                param="response_format.type",
            )
        return _response(tool_arguments=_valid_payload())

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    forced = completions.calls[-1]
    assert forced["tools"][0]["function"]["name"] == "ProbeResult"
    assert forced["tool_choice"] == {
        "type": "function",
        "function": {"name": "ProbeResult"},
    }


def test_tools_can_be_used_when_tool_choice_is_rejected(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _bad_request("json_schema is not supported", param="response_format.type")
        if "tool_choice" in kwargs:
            return _bad_request("Unsupported parameter: tool_choice", param="tool_choice")
        return _response(tool_arguments=_valid_payload())

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    unforced = completions.calls[-1]
    assert "tools" in unforced
    assert "tool_choice" not in unforced


def test_falls_back_to_json_object(configured_llm):
    def handler(kwargs, _index):
        response_type = kwargs.get("response_format", {}).get("type")
        if response_type == "json_schema":
            return _bad_request("json_schema is not supported", param="response_format.type")
        if "tools" in kwargs:
            return _bad_request("Unsupported parameter: tools", param="tools")
        if response_type == "json_object":
            return _response(content=json.dumps(_valid_payload()))
        raise AssertionError(f"unexpected request: {kwargs}")

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    request = completions.calls[-1]
    assert request["response_format"] == {"type": "json_object"}
    assert any(
        "JSON" in str(message.get("content", "")) for message in request["messages"]
    )


def test_falls_back_to_prompt_json_without_structured_parameters(configured_llm):
    def handler(kwargs, _index):
        if "tools" in kwargs:
            return _bad_request("Unsupported parameter: tools", param="tools")
        if "response_format" in kwargs:
            return _bad_request(
                "Unsupported parameter: response_format", param="response_format"
            )
        return _response(content=f"```json\n{json.dumps(_valid_payload())}\n```")

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    request = completions.calls[-1]
    assert "response_format" not in request
    assert "tools" not in request
    assert "tool_choice" not in request
    assert any(
        "JSON Schema" in str(message.get("content", ""))
        for message in request["messages"]
    )


def test_unsupported_parameter_downgrade_is_cached(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _bad_request("json_schema is not supported", param="response_format.type")
        return _response(tool_arguments=_valid_payload())

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    first_call_count = len(completions.calls)
    assert _invoke(engine).answer == "ok"

    assert first_call_count == 2
    assert len(completions.calls) == 3
    assert "tools" in completions.calls[-1]
    assert completions.calls[-1]["tool_choice"]["function"]["name"] == "ProbeResult"


@pytest.mark.parametrize(
    "temporary_error",
    [TimeoutError("timeout"), _rate_limit_error(), _server_error()],
)
def test_temporary_errors_do_not_pollute_capability_cache(
    configured_llm,
    temporary_error,
):
    outcomes: list[Any] = [temporary_error, _response(content=json.dumps(_valid_payload()))]
    engine, completions, _factory_calls = _engine(
        lambda _kwargs, _index: outcomes.pop(0),
        max_retries=0,
    )

    with pytest.raises(LlmRetryableError):
        _invoke(engine)
    assert _invoke(engine).answer == "ok"

    assert all(
        call["response_format"]["type"] == "json_schema"
        for call in completions.calls
    )


def test_generic_bad_request_does_not_trigger_downgrade(configured_llm):
    error = _bad_request(
        "Invalid schema: required must list every property",
        param="response_format",
        code="invalid_json_schema",
    )
    engine, completions, _factory_calls = _engine(lambda _kwargs, _index: error)

    with pytest.raises(LlmFinalError):
        _invoke(engine)

    assert len(completions.calls) == 1


def test_pydantic_failure_is_added_to_next_prompt(configured_llm):
    outcomes = [
        _response(
            content=json.dumps(
                {"answer": "x", "confidence": 2, "tags": []},
                ensure_ascii=False,
            )
        ),
        _response(content=json.dumps(_valid_payload())),
    ]
    engine, completions, _factory_calls = _engine(
        lambda _kwargs, _index: outcomes.pop(0)
    )

    assert _invoke(engine).answer == "ok"

    retry_messages = completions.calls[1]["messages"]
    retry_prompt = "\n".join(str(item.get("content", "")) for item in retry_messages)
    assert "结构化输出校验失败" in retry_prompt
    assert "answer" in retry_prompt
    assert "confidence" in retry_prompt
    assert "tags" in retry_prompt
    assert len(completions.calls) == 2


def test_strips_thinking_tags_before_json_parse(configured_llm):
    content = (
        "<think>\n内部推理\n</think>\n"
        + json.dumps(_valid_payload(), ensure_ascii=False)
    )
    engine, completions, _factory_calls = _engine(
        lambda _kwargs, _index: _response(content=content)
    )

    assert _invoke(engine).answer == "ok"
    assert completions.calls[0]["response_format"]["type"] == "json_schema"


def test_fake_native_json_schema_downgrades_on_non_json(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _response(content="label='structured-output-probe' and ok=true")
        return _response(tool_arguments=_valid_payload())

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    assert completions.calls[0]["response_format"]["type"] == "json_schema"
    assert "tools" in completions.calls[1]
    first_count = len(completions.calls)
    assert _invoke(engine).answer == "ok"
    assert len(completions.calls) == first_count + 1
    assert "tools" in completions.calls[-1]


def test_response_format_unavailable_downgrades_only_json_schema(configured_llm):
    def handler(kwargs, _index):
        response_type = kwargs.get("response_format", {}).get("type")
        if response_type == "json_schema":
            return _bad_request(
                "This response_format type is unavailable now",
                param="response_format",
                code="invalid_request_error",
            )
        if "tools" in kwargs:
            return _bad_request("Unsupported parameter: tools", param="tools")
        if response_type == "json_object":
            return _response(content=json.dumps(_valid_payload()))
        raise AssertionError(f"unexpected request: {kwargs}")

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    assert completions.calls[-1]["response_format"] == {"type": "json_object"}


def test_forced_tool_accepts_json_in_content_when_tool_not_called(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _response(content="not json at all")
        if "tool_choice" in kwargs:
            return _response(content=json.dumps(_valid_payload()))
        raise AssertionError(f"unexpected request: {kwargs}")

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    assert "tool_choice" in completions.calls[-1]


def test_forced_tool_not_called_and_no_content_json_downgrades(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _response(content="not json")
        if "tool_choice" in kwargs:
            return _response(content="我拒绝用工具，只聊天。")
        if "tools" in kwargs:
            return _response(content=json.dumps(_valid_payload()))
        raise AssertionError(f"unexpected request: {kwargs}")

    engine, completions, _factory_calls = _engine(handler)

    assert _invoke(engine).answer == "ok"
    last = completions.calls[-1]
    assert "tools" in last
    assert "tool_choice" not in last


def test_transport_schema_removes_remote_constraints_but_keeps_business_schema(
    configured_llm,
):
    engine, completions, _factory_calls = _engine(
        lambda _kwargs, _index: _response(content=json.dumps(_valid_payload()))
    )

    _invoke(engine)

    transport_schema = completions.calls[0]["response_format"]["json_schema"]["schema"]
    serialized = json.dumps(transport_schema)
    assert "minLength" not in serialized
    assert "minItems" not in serialized
    assert "minimum" not in serialized
    assert "maximum" not in serialized
    original = json.dumps(ProbeResult.model_json_schema())
    assert "minLength" in original
    assert "minItems" in original
    assert "minimum" in original
    assert "maximum" in original


def test_cache_key_changes_with_model_or_base_url(configured_llm, monkeypatch):
    engine, completions, _factory_calls = _engine(
        lambda kwargs, _index: (
            _bad_request("json_schema is not supported", param="response_format.type")
            if kwargs.get("response_format", {}).get("type") == "json_schema"
            else _response(tool_arguments=_valid_payload())
        )
    )

    settings = get_settings()
    _invoke(engine)
    monkeypatch.setattr(settings, "MODEL_SPLIT", "model-b")
    _invoke(engine)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://other.example/v1")
    _invoke(engine)

    native_calls = [
        call
        for call in completions.calls
        if call.get("response_format", {}).get("type") == "json_schema"
    ]
    assert len(native_calls) == 3


def test_diagnostics_do_not_log_key_or_request_body(configured_llm, caplog):
    sensitive_text = "confidential full paper and reviewer comments"
    engine, _completions, _factory_calls = _engine(
        lambda _kwargs, _index: _response(content=json.dumps(_valid_payload()))
    )

    with caplog.at_level("INFO"):
        engine.invoke_structured(
            "split",
            ProbeResult,
            [{"role": "user", "content": sensitive_text}],
        )

    assert "model=model-a" in caplog.text
    assert "endpoint_host=example.test" in caplog.text
    assert "selected_strategy=native_json_schema" in caplog.text
    assert "capability_cache_hit=false" in caplog.text
    assert "tool_name=ProbeResult" in caplog.text
    assert "super-secret-key" not in caplog.text
    assert sensitive_text not in caplog.text


def test_no_provider_or_model_name_branching_in_engine_source():
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "langgraph_agent"
        / "llm"
        / "structured_output.py"
    )
    source = source_path.read_text(encoding="utf-8")
    lowered = source.casefold()
    for provider_marker in (
        "deepseek",
        "nvidia",
        "vllm",
        "sglang",
        "ollama",
        "openai.com",
    ):
        assert provider_marker not in lowered

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            condition = ast.get_source_segment(source, node.test) or ""
            assert "model_name" not in condition
            assert "base_url" not in condition
            assert "endpoint_host" not in condition


def test_wraps_top_level_array_for_list_root_field(configured_llm):
    """模型直接返回顶层数组时，按 schema 唯一 list 字段自动包一层。"""

    class CardBatch(BaseModel):
        cards: list[dict[str, object]]

    payload = [
        {
            "card_type": "CORE_CONTRIBUTIONS",
            "content": "论文提出 Transformer。",
            "source_section_id": "section-0001",
            "source_quote": "We propose a new simple network architecture.",
            "confidence": 0.95,
        }
    ]
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    def handler(kwargs, _index):
        assert kwargs["response_format"]["type"] == "json_schema"
        assert any(
            "JSON 对象根节点" in str(message.get("content", ""))
            for message in kwargs["messages"]
            if isinstance(message, dict)
        )
        return _response(content=fenced)

    engine, completions, _factory = _engine(handler)
    result = engine.invoke_structured(
        "split",
        CardBatch,
        [{"role": "user", "content": "生成卡片"}],
    )
    assert len(result.cards) == 1
    assert result.cards[0]["card_type"] == "CORE_CONTRIBUTIONS"
    assert completions.calls[0]["response_format"]["json_schema"]["name"] == "CardBatch"


def test_ignores_thinking_content_blocks_and_parses_text_json(configured_llm):
    """Anthropic 风格 content blocks：跳过 thinking，只解析 text JSON。"""

    class CardBatch(BaseModel):
        cards: list[dict[str, object]]

    anthropic_style = {
        "choices": [
            {
                "message": {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "long reasoning that must be ignored",
                        },
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "cards": [
                                        {
                                            "card_type": "MAIN_METHOD",
                                            "content": "使用缩放点积注意力。",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            }
        ]
    }

    engine, _completions, _factory = _engine(lambda _kwargs, _index: anthropic_style)
    result = engine.invoke_structured(
        "split",
        CardBatch,
        [{"role": "user", "content": "生成卡片"}],
    )
    assert result.cards[0]["card_type"] == "MAIN_METHOD"


def test_forced_tool_request_reminds_object_root(configured_llm):
    def handler(kwargs, _index):
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            return _bad_request("json_schema is not supported", param="response_format.type")
        if "tool_choice" in kwargs:
            assert any(
                "不能是顶层数组" in str(message.get("content", ""))
                for message in kwargs["messages"]
                if isinstance(message, dict)
            )
            return _response(tool_arguments=_valid_payload())
        raise AssertionError(kwargs)

    engine, completions, _factory = _engine(handler)
    assert _invoke(engine).answer == "ok"
    assert "tool_choice" in completions.calls[-1]


def test_draft_aliases_response_draft_and_strips_extras(configured_llm):
    """草稿节点：response_draft → generated_content，并丢掉多余字段。"""
    fact_id = "539c2a73-49fe-449b-a7b2-96caf5cf031d"
    messy = {
        "source_id": "3dcf8d4f-6f72-40e7-ac27-60df78c594f6",
        "suggestion_id": "73a49388-4108-4f9f-8925-98f0b3810d9a",
        "selected_direction": "ACCEPT_AND_REVISE",
        "response_draft": "感谢审稿人意见。修订稿中将补充正弦位置编码的选用理由。",
        "used_fact_ids": [fact_id],
    }

    engine, completions, _factory = _engine(
        lambda _kwargs, _index: _response(content=json.dumps(messy, ensure_ascii=False))
    )
    result = engine.invoke_structured(
        "draft",
        LlmResponseDraft,
        [{"role": "user", "content": "生成草稿"}],
    )
    assert "补充正弦位置编码" in result.generated_content
    assert [str(item) for item in result.used_fact_ids] == [fact_id]
    assert len(completions.calls) == 1


def test_draft_purpose_retries_at_most_once_on_validation_error(configured_llm):
    """draft 用途最多 1 次重试（共 2 次调用）。"""
    good = {
        "generated_content": "修订稿中将补充说明。",
        "used_fact_ids": ["539c2a73-49fe-449b-a7b2-96caf5cf031d"],
    }
    bad = {"response_text": "错误字段名"}
    outcomes = [
        _response(content=json.dumps(bad, ensure_ascii=False)),
        _response(content=json.dumps(good, ensure_ascii=False)),
        _response(content=json.dumps(good, ensure_ascii=False)),
    ]

    def handler(_kwargs, index):
        return outcomes[min(index - 1, len(outcomes) - 1)]

    engine, completions, _factory = _engine(handler)
    result = engine.invoke_structured(
        "draft",
        LlmResponseDraft,
        [{"role": "user", "content": "生成草稿"}],
    )
    assert "补充说明" in result.generated_content
    assert len(completions.calls) == 2
