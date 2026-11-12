"""LLM 错误分类与安全诊断辅助。"""

from __future__ import annotations

import httpx
import openai
from pydantic import ValidationError


class LlmRetryableError(RuntimeError):
    """有限次重试耗尽后仍可由上层重新运行的模型失败。"""

    status = "FAILED_RETRYABLE"

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause_type, self.status_code, self.safe_summary = (
            _safe_exception_context(cause)
        )


class LlmFinalError(RuntimeError):
    """不可重试或持续结构化校验失败的最终模型失败。"""

    status = "FAILED_FINAL"

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause_type, self.status_code, self.safe_summary = (
            _safe_exception_context(cause)
        )


class StructuredOutputParseError(ValueError):
    """Chat Completions 未返回可供 Pydantic 校验的结构化载荷。"""


def _exception_status_code(error: Exception | None) -> int | None:
    if error is None:
        return None
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(error, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _safe_exception_context(
    error: Exception | None,
) -> tuple[str | None, int | None, str]:
    """提取可安全持久化/记录的异常摘要，不包含请求与响应正文。"""
    if error is None:
        return None, None, "模型调用失败"

    cause_type = type(error).__name__
    status_code = _exception_status_code(error)
    if isinstance(
        error,
        (openai.APITimeoutError, TimeoutError, httpx.TimeoutException),
    ):
        summary = "模型请求超时"
    elif status_code is not None:
        summary = f"模型服务返回 HTTP {status_code}"
    elif isinstance(
        error,
        (
            openai.APIConnectionError,
            ConnectionError,
            httpx.TransportError,
        ),
    ):
        summary = "模型服务连接失败"
    elif isinstance(
        error,
        (ValidationError, StructuredOutputParseError),
    ):
        summary = "模型结构化输出校验失败"
    else:
        summary = "模型调用失败"
    return cause_type, status_code, summary


def llm_error_context(error: Exception) -> tuple[str, int | None, str]:
    """返回上层可用于安全日志和用户 warning 的根因信息。"""
    cause = error.__cause__ if error.__cause__ is not None else error
    cause_type = getattr(error, "cause_type", None)
    status_code = getattr(error, "status_code", None)
    safe_summary = getattr(error, "safe_summary", None)
    if cause_type and safe_summary:
        return cause_type, status_code, safe_summary
    fallback_type, fallback_status, fallback_summary = _safe_exception_context(cause)
    return fallback_type or type(error).__name__, fallback_status, fallback_summary


def format_llm_failure_reason(
    error: Exception,
    *,
    timeout_seconds: float,
) -> str:
    """将模型失败转换为不泄露 payload 的可操作降级原因。"""
    cause_type, status_code, safe_summary = llm_error_context(error)
    if "Timeout" in cause_type or safe_summary == "模型请求超时":
        return f"{cause_type}，模型在 {timeout_seconds:g} 秒内未返回"
    if status_code is not None:
        return f"{cause_type}，模型服务返回 HTTP {status_code}"
    return f"{cause_type}，{safe_summary}"


def is_retryable_llm_error(error: Exception) -> bool:
    """判断模型调用异常是否属于超时、限流或临时网络/服务错误。"""
    if isinstance(error, (TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    if isinstance(
        error,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return True
    if isinstance(error, openai.APIStatusError):
        return error.status_code == 429 or error.status_code >= 500
    return False


__all__ = [
    "LlmFinalError",
    "LlmRetryableError",
    "StructuredOutputParseError",
    "format_llm_failure_reason",
    "is_retryable_llm_error",
    "llm_error_context",
]
