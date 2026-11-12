from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib import error, request

from flask import current_app
from langchain_openai import ChatOpenAI

from .services.platform_model_runtime import get_platform_model_runtime
from .services.system_model_runtime import get_system_model_runtime
from .services.user_model_config import API_MODEL_VALUE, get_user_api_model_runtime


AUTO_MODEL_VALUES = {"", "auto", "platform", "personal", "自动选择模型"}
VERTICAL_DOMAIN_MODEL_VALUES = {"vertical_domain", "垂域模型", "qwen3.6-dpo"}


def resolve_chat_model_name(value: str | None) -> str:
    model = (value or "").strip()
    if model == API_MODEL_VALUE:
        return get_user_api_model_runtime().model_name
    if model in AUTO_MODEL_VALUES or model in VERTICAL_DOMAIN_MODEL_VALUES:
        return get_platform_model_runtime().model_name
    return model


def build_qwen_dpo_chat_model() -> ChatOpenAI:
    runtime = get_platform_model_runtime()
    return ChatOpenAI(
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model_name,
        temperature=0,
        timeout=runtime.timeout_seconds,
        max_retries=1,
    )


def build_system_chat_model(user_id) -> ChatOpenAI:
    runtime = get_system_model_runtime(user_id)
    return ChatOpenAI(
        api_key=runtime["api_key"],
        base_url=runtime["base_url"],
        model=runtime["model_name"],
        temperature=0,
        timeout=float(runtime["timeout_seconds"]),
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
    platform_runtime = get_platform_model_runtime()
    requested_model = (model or "").strip()
    uses_api_model = requested_model == API_MODEL_VALUE
    uses_platform_model = requested_model in AUTO_MODEL_VALUES or requested_model in VERTICAL_DOMAIN_MODEL_VALUES
    api_runtime = get_user_api_model_runtime()
    selected_runtime = api_runtime if uses_api_model else platform_runtime
    resolved_model = selected_runtime.model_name if uses_api_model or uses_platform_model else requested_model
    payload = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if uses_platform_model:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    effective_base_url = base_url or selected_runtime.base_url
    effective_api_key = str(selected_runtime.api_key if api_key is None else api_key).strip()
    effective_timeout = timeout_seconds or selected_runtime.timeout_seconds
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
            raise RuntimeError("模型服务返回了空回答")
    return {
        "model": data.get("model") or resolved_model,
        "content": content,
        "usage": data.get("usage") or {},
    }
