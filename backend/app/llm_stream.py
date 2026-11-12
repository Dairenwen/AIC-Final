"""Streaming variant of the OpenAI-compatible chat call.

`run_openai_compatible_chat` waits for the whole answer; this module yields
the answer piece by piece so the browser can render it while it is written.
"""
from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from typing import Any
from urllib import error, request

from .llm import AUTO_MODEL_VALUES, VERTICAL_DOMAIN_MODEL_VALUES, run_openai_compatible_chat
from .services.platform_model_runtime import get_platform_model_runtime
from .services.user_model_config import API_MODEL_VALUE, get_user_api_model_runtime


CONNECT_ATTEMPTS = 3


def _open_stream(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Nothing has been sent to the browser yet, so a failed connection attempt
    # can be retried without the user noticing.
    for attempt in range(CONNECT_ATTEMPTS):
        api_request = request.Request(endpoint, data=body, method="POST", headers=headers)
        try:
            return request.urlopen(api_request, timeout=timeout)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < CONNECT_ATTEMPTS:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise _StreamHttpError(exc.code, detail) from exc
        except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt + 1 < CONNECT_ATTEMPTS:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise RuntimeError("无法连接模型服务，请稍后重试，或在模型库中测试该模型的连接") from exc
    raise RuntimeError("无法连接模型服务，请稍后重试")
class _StreamHttpError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"模型服务返回错误 {status}: {detail[:300]}")
        self.status = status
        self.detail = detail.lower()


def _delta_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in value)
    return ""


def _parse_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def stream_openai_compatible_chat(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield `reasoning` / `delta` events followed by one `done` event."""
    requested_model = (model or "").strip()
    uses_api_model = requested_model == API_MODEL_VALUE
    uses_platform_model = requested_model in AUTO_MODEL_VALUES or requested_model in VERTICAL_DOMAIN_MODEL_VALUES
    selected_runtime = get_user_api_model_runtime() if uses_api_model else get_platform_model_runtime()
    resolved_model = selected_runtime.model_name if uses_api_model or uses_platform_model else requested_model
    effective_base_url = base_url or selected_runtime.base_url
    effective_api_key = str(selected_runtime.api_key if api_key is None else api_key).strip()
    effective_timeout = timeout_seconds or selected_runtime.timeout_seconds
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Zhiyan-Backend/1.0",
    }
    if effective_api_key and effective_api_key.upper() != "EMPTY":
        headers["Authorization"] = f"Bearer {effective_api_key}"
    endpoint = f"{effective_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if uses_platform_model:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    try:
        response = _open_stream(endpoint, headers, payload, effective_timeout)
    except _StreamHttpError as exc:
        # Gateways differ in which optional fields they accept. Drop the
        # optional ones once before giving up.
        if exc.status not in {400, 404, 422}:
            raise
        slim = {key: value for key, value in payload.items() if key not in {"stream_options", "chat_template_kwargs"}}
        if "max_tokens" in exc.detail:
            slim["max_completion_tokens"] = slim.pop("max_tokens")
        response = _open_stream(endpoint, headers, slim, effective_timeout)

    parts: list[str] = []
    usage: dict[str, Any] = dict()
    reported_model = resolved_model
    try:
        with response:
            for raw_line in response:
                chunk = _parse_line(raw_line)
                if chunk is None:
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                reported_model = chunk.get("model") or reported_model
                choices = chunk.get("choices") or []
                delta = choices[0].get("delta") if choices and isinstance(choices[0], dict) else None
                if not isinstance(delta, dict):
                    continue
                reasoning = _delta_text(delta.get("reasoning_content"))
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                text = _delta_text(delta.get("content"))
                if text:
                    parts.append(text)
                    yield {"type": "delta", "text": text}
    except (TimeoutError, socket.timeout, OSError) as exc:
        if not parts:
            raise RuntimeError("模型服务连接中断，请重试") from exc

    content = "".join(parts)
    if not content.strip():
        # The stream carried no answer (reasoning only, or a gateway that
        # ignores `stream`). Fall back to the buffered call once.
        result = run_openai_compatible_chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        content = str(result.get("content") or "")
        usage = result.get("usage") or usage
        reported_model = result.get("model") or reported_model
        yield {"type": "delta", "text": content}
    yield {"type": "done", "content": content, "usage": usage, "model": reported_model}
