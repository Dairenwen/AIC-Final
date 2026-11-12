"""StructuredOutputEngine 临时错误与重试语义回归（自 backend/tests 迁入）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel, Field

from langgraph_agent.llm.resilience import LlmFinalError, LlmRetryableError
from langgraph_agent.llm.structured_output import StructuredOutputEngine


class StructuredResult(BaseModel):
    answer: str
    confidence: float = Field(ge=0, le=1)


class FakeCompletions:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response(answer: str = "已恢复", confidence: float = 0.9):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"answer": answer, "confidence": confidence},
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.test/v1/chat/completions")


def _rate_limit_error() -> openai.RateLimitError:
    response = httpx.Response(429, request=_request())
    return openai.RateLimitError("请求过于频繁", response=response, body=None)


def _authentication_error() -> openai.AuthenticationError:
    response = httpx.Response(401, request=_request())
    return openai.AuthenticationError("鉴权失败", response=response, body=None)


@pytest.fixture(autouse=True)
def configured_llm(monkeypatch):
    from config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "MODEL_SPLIT", "test-model")
    monkeypatch.setattr(settings, "MODEL_PAPER_CARD", "card-model")
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 12.0)
    monkeypatch.setattr(settings, "PAPER_CARD_LLM_TIMEOUT_SECONDS", 17.0)
    monkeypatch.setattr(settings, "LLM_EXTRA_BODY", {})



def _engine(outcomes, *, max_retries=3, base_delay=0.5, sleeps=None):
    completions = FakeCompletions(outcomes)
    sleep_calls = [] if sleeps is None else sleeps
    engine = StructuredOutputEngine(
        client_factory=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
        sleep=sleep_calls.append,
        base_delay=base_delay,
        max_retries_by_purpose={"split": max_retries},
    )
    return engine, completions, sleep_calls


def _invoke(engine, purpose="split", timeout_seconds=None):
    return engine.invoke_structured(
        purpose,
        StructuredResult,
        [{"role": "user", "content": "测试"}],
        timeout_seconds=timeout_seconds,
    )


def test_retryable_error_retries_then_returns_validated_model():
    engine, completions, sleeps = _engine(
        [_rate_limit_error(), _response()],
        max_retries=2,
        base_delay=0.25,
    )

    result = _invoke(engine)

    assert result == StructuredResult(answer="已恢复", confidence=0.9)
    assert len(completions.calls) == 2
    assert sleeps == [0.25]


def test_retryable_error_exhaustion_raises_retryable_failure():
    errors = [httpx.ConnectError("临时网络错误", request=_request()) for _ in range(3)]
    engine, completions, sleeps = _engine(errors, max_retries=2)

    with pytest.raises(LlmRetryableError) as exc_info:
        _invoke(engine)

    assert exc_info.value.status == "FAILED_RETRYABLE"
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    assert exc_info.value.cause_type == "ConnectError"
    assert exc_info.value.status_code is None
    assert exc_info.value.safe_summary == "模型服务连接失败"
    assert len(completions.calls) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize(
    ("error", "cause_type", "status_code"),
    [
        (_rate_limit_error(), "RateLimitError", 429),
        (
            openai.InternalServerError(
                "上游失败",
                response=httpx.Response(502, request=_request()),
                body=None,
            ),
            "InternalServerError",
            502,
        ),
    ],
)
def test_retryable_error_preserves_safe_cause_type_and_status(
    error,
    cause_type,
    status_code,
):
    engine, _completions, _sleeps = _engine([error], max_retries=0)

    with pytest.raises(LlmRetryableError) as exc_info:
        _invoke(engine)

    assert exc_info.value.cause_type == cause_type
    assert exc_info.value.status_code == status_code
    assert str(status_code) in exc_info.value.safe_summary


def test_authentication_error_fails_immediately_without_retry():
    engine, completions, sleeps = _engine(
        [_authentication_error(), _response(answer="不应调用")],
        max_retries=3,
        base_delay=1,
    )

    with pytest.raises(LlmFinalError) as exc_info:
        _invoke(engine)

    assert isinstance(exc_info.value.__cause__, openai.AuthenticationError)
    assert len(completions.calls) == 1
    assert sleeps == []


def test_exponential_backoff_is_called_once_per_retry():
    engine, _completions, sleeps = _engine(
        [
            TimeoutError("第一次超时"),
            TimeoutError("第二次超时"),
            _response(answer="成功", confidence=1),
        ],
        max_retries=3,
        base_delay=0.2,
    )

    result = _invoke(engine)

    assert result.answer == "成功"
    assert sleeps == [0.2, 0.4]


def test_paper_card_keeps_one_accepted_call_and_explicit_timeout():
    completions = FakeCompletions([TimeoutError("card timeout"), _response()])
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    engine = StructuredOutputEngine(
        client_factory=factory,
        sleep=lambda _delay: None,
    )

    with pytest.raises(LlmRetryableError):
        _invoke(engine, purpose="paper_card", timeout_seconds=17)

    assert len(completions.calls) == 1
    assert factory_calls[0]["timeout"] == 17
    assert factory_calls[0]["max_retries"] == 0
