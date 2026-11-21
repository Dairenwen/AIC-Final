from __future__ import annotations

from langchain_openai import ChatOpenAI

from config.settings import Settings, get_settings


def build_bailian_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Build the Alibaba Cloud Model Studio OpenAI-compatible chat model."""
    resolved = settings or get_settings()
    if not resolved.dashscope_api_key or is_placeholder_api_key(resolved.dashscope_api_key):
        raise ValueError("DASHSCOPE_API_KEY is required for query rewriting and report generation")
    return ChatOpenAI(
        api_key=resolved.dashscope_api_key,
        base_url=resolved.bailian_base_url,
        model=resolved.bailian_model,
        temperature=0,
        timeout=resolved.bailian_timeout_seconds,
        max_retries=resolved.bailian_max_retries,
    )


def is_placeholder_api_key(value: str) -> bool:
    normalized = value.strip().casefold()
    markers = ("replace", "your_", "your-", "example", "placeholder", "填入", "填写")
    return any(marker in normalized for marker in markers)
