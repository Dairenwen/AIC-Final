"""llm 包公开接口与韧性辅助的冒烟测试。"""

from __future__ import annotations

import httpx
import openai

from langgraph_agent.llm import invoke_structured
from langgraph_agent.llm.resilience import (
    LlmFinalError,
    LlmRetryableError,
    StructuredOutputParseError,
    format_llm_failure_reason,
    is_retryable_llm_error,
    llm_error_context,
)
from langgraph_agent.llm.structured_output import StructuredOutputEngine


def test_package_exports_invoke_structured():
    assert callable(invoke_structured)
    assert invoke_structured is not None


def test_structured_output_engine_constructs_with_defaults():
    engine = StructuredOutputEngine(sleep=lambda _delay: None)
    assert engine is not None


def test_is_retryable_for_timeout_and_rate_limit():
    assert is_retryable_llm_error(TimeoutError("timeout")) is True
    assert is_retryable_llm_error(ConnectionError("conn")) is True

    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    rate = openai.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )
    assert is_retryable_llm_error(rate) is True

    bad = openai.BadRequestError(
        "bad",
        response=httpx.Response(400, request=request),
        body=None,
    )
    assert is_retryable_llm_error(bad) is False


def test_llm_error_context_and_format_reason_are_payload_safe():
    err = LlmRetryableError("retry exhausted", cause=TimeoutError("timeout"))
    cause_type, status_code, summary = llm_error_context(err)
    assert cause_type == "TimeoutError"
    assert status_code is None
    assert "超时" in summary
    reason = format_llm_failure_reason(err, timeout_seconds=30)
    assert "30" in reason
    assert "TimeoutError" in reason


def test_final_error_carries_safe_summary():
    parse_err = StructuredOutputParseError("模型未返回可解析的 JSON 内容。")
    final = LlmFinalError("最终失败", cause=parse_err)
    assert final.status == "FAILED_FINAL"
    assert final.cause_type == "StructuredOutputParseError"
    assert "结构化" in final.safe_summary
