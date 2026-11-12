from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from flask import current_app
from langchain_openai import ChatOpenAI

from .services.platform_model_runtime import get_platform_model_runtime
from .services.task_model_runtime import NO_MODEL_MESSAGE


AUTO_MODEL_VALUES = {"", "auto", "platform", "personal", "自动选择模型"}
VERTICAL_DOMAIN_MODEL_VALUES = {"vertical_domain", "垂域模型", "qwen3.6-dpo"}


def resolve_chat_model_name(value: str | None) -> str:
    model = (value or "").strip()
    if model in AUTO_MODEL_VALUES or model in VERTICAL_DOMAIN_MODEL_VALUES:
        _require_platform_model()
        return get_platform_model_runtime().model_name
    return model


def _require_platform_model() -> None:
    if not current_app.config.get("PLATFORM_MODEL_ENABLED", False):
        raise RuntimeError(NO_MODEL_MESSAGE)


def build_qwen_dpo_chat_model() -> ChatOpenAI:
    _require_platform_model()
    runtime = get_platform_model_runtime()
    return ChatOpenAI(
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model_name,
        temperature=0,
        timeout=runtime.timeout_seconds,
        max_retries=1,
    )


# Below this output budget a reasoning model can spend everything on its
# hidden reasoning and return no answer at all.
SMALL_OUTPUT_BUDGET = 4096
THINKING_OFF = {"type": "disabled"}


def reasoning_can_starve_answer(base_url: str | None, max_tokens: int) -> bool:
    """Whether to switch hidden reasoning off before the first request.

    DeepSeek counts reasoning tokens against max_tokens and accepts
    `thinking: {"type": "disabled"}`.  Other gateways are left alone here and
    are handled after the fact, because an unknown parameter may be rejected.
    """
    host = (urlparse(base_url or "").hostname or "").lower()
    return max_tokens <= SMALL_OUTPUT_BUDGET and (host == "deepseek.com" or host.endswith(".deepseek.com"))


def build_personal_chat_model(runtime: dict[str, object]) -> ChatOpenAI:
    """A LangChain chat model on one of the user's own OpenAI-compatible endpoints."""
    return ChatOpenAI(
        api_key=str(runtime["api_key"]),
        base_url=str(runtime["base_url"]),
        model=str(runtime["model_name"]),
        temperature=0,
        timeout=float(runtime.get("timeout_seconds") or 120),
        max_retries=1,
    )


def run_openai_compatible_chat(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    requested_model = (model or "").strip()
    uses_platform_model = requested_model in AUTO_MODEL_VALUES or requested_model in VERTICAL_DOMAIN_MODEL_VALUES
    if uses_platform_model or not base_url:
        _require_platform_model()
    platform_runtime = get_platform_model_runtime() if uses_platform_model or not base_url else None
    resolved_model = platform_runtime.model_name if uses_platform_model else requested_model
    payload = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if uses_platform_model:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    effective_base_url = base_url or platform_runtime.base_url
    if not uses_platform_model and reasoning_can_starve_answer(effective_base_url, max_tokens):
        payload["thinking"] = dict(THINKING_OFF)
    effective_api_key = str((platform_runtime.api_key if platform_runtime else "") if api_key is None else api_key).strip()
    effective_timeout = timeout_seconds or (platform_runtime.timeout_seconds if platform_runtime else 120)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "close",
        "User-Agent": "Zhiyan-Backend/1.0",
    }
    if effective_api_key and effective_api_key.upper() != "EMPTY":
        headers["Authorization"] = f"Bearer {effective_api_key}"
    endpoint = f"{effective_base_url.rstrip('/')}/chat/completions"
    max_retries = max(0, int(current_app.config.get("MODEL_HTTP_MAX_RETRIES", 3)))

    def call(body_payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
        attempts = max_retries + 1
        raw = ""
        for attempt in range(attempts):
            api_request = request.Request(endpoint, data=body, method="POST", headers=headers)
            try:
                with request.urlopen(api_request, timeout=effective_timeout) as response:
                    raw = response.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                    raise RuntimeError(f"模型服务返回错误 {exc.code}: {detail[:300]}") from exc
            except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                if attempt + 1 >= attempts:
                    reason = getattr(exc, "reason", None) or str(exc)
                    raise RuntimeError(f"无法连接模型服务（已尝试 {attempts} 次）: {reason}") from exc
            time.sleep(min(0.5 * (2**attempt), 2.0))
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("模型服务返回了无效 JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("模型服务返回格式无效")
        return parsed

    try:
        data = call(payload)
    except RuntimeError as exc:
        # Some OpenAI-compatible gateways expose newer reasoning models and
        # reject `max_tokens` in favor of `max_completion_tokens`.
        message = str(exc).lower()
        if "max_tokens" in message:
            retry_payload = {**payload, "max_completion_tokens": payload.pop("max_tokens")}
            data = call(retry_payload)
        elif "chat_template_kwargs" in payload and any(
            marker in message for marker in ("chat_template", "unknown parameter", "extra")
        ):
            retry_payload = {key: value for key, value in payload.items() if key != "chat_template_kwargs"}
            data = call(retry_payload)
        else:
            raise

    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    if not isinstance(content, str):
        content = str(content or "")
    if not choices or not content.strip():
        # A few Qwen gateways ignore the first disable-thinking hint. Retry
        # once without a reasoning-only response before reporting failure.
        if uses_platform_model and "chat_template_kwargs" in payload:
            retry_payload = {**payload, "chat_template_kwargs": {"enable_thinking": False}, "max_tokens": max(max_tokens, 64)}
            retry_data = call(retry_payload)
            retry_choices = retry_data.get("choices") or []
            retry_message = retry_choices[0].get("message", {}) if retry_choices and isinstance(retry_choices[0], dict) else {}
            retry_content = retry_message.get("content", "") if isinstance(retry_message, dict) else ""
            if isinstance(retry_content, list):
                retry_content = "\n".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in retry_content)
            if str(retry_content or "").strip():
                data = retry_data
                content = str(retry_content)
            else:
                raise RuntimeError("模型服务返回了空回答")
        else:
            # A reasoning model used the whole budget before answering.  Ask
            # again without reasoning; if the gateway does not know that
            # switch, ask again with room for both reasoning and the answer.
            content = ""
            attempts: list[dict[str, Any]] = []
            if "thinking" not in payload:
                attempts.append({**payload, "thinking": dict(THINKING_OFF)})
            attempts.append({
                **{key: value for key, value in payload.items() if key != "thinking"},
                "max_tokens": min(max(max_tokens * 4, SMALL_OUTPUT_BUDGET), 16384),
            })
            for retry_payload in attempts:
                try:
                    retry_data = call(retry_payload)
                except RuntimeError:
                    continue
                retry_choices = retry_data.get("choices") or []
                retry_message = retry_choices[0].get("message", {}) if retry_choices and isinstance(retry_choices[0], dict) else {}
                retry_content = retry_message.get("content", "") if isinstance(retry_message, dict) else ""
                if isinstance(retry_content, list):
                    retry_content = "\n".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in retry_content)
                if str(retry_content or "").strip():
                    data = retry_data
                    content = str(retry_content)
                    break
            if not content.strip():
                raise RuntimeError("模型服务返回了空回答")
    return {
        "model": data.get("model") or resolved_model,
        "content": content,
        "usage": data.get("usage") or {},
    }
