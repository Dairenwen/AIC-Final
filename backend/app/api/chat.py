from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from flask import Blueprint, Response, current_app, g, request, stream_with_context
from sqlalchemy import select

from ..extensions import db
from ..llm import run_openai_compatible_chat
from ..llm_stream import stream_openai_compatible_chat
from ..models import Message
from ..services.model_credentials import decrypt_api_key
from ..services.user_model_config import ensure_user_api_config
from ..services.user_model_config import API_MODEL_VALUE
from ..services.token_usage import (
    TokenQuotaExceeded,
    ensure_token_quota,
    estimate_messages_tokens,
    estimate_tokens,
    record_token_usage,
)
from .model_configs import find_default_chat_config, find_owned_config
from .projects import append_message, get_conversation_access
from .responses import error, ok


bp = Blueprint("chat", __name__)

SYSTEM_PROMPT = (
    "你是智研科研助手，回答应简洁、可靠、适合科研工作场景。"
    "当用户需要文献检索、报告或年度脉络图时，提醒用户可选择文献检索 Agent 进入专业工作台。"
)


class ChatRequest:
    """Everything a chat turn needs once the request has been validated."""

    def __init__(self) -> None:
        self.prompt = ""
        self.conversation_id: UUID | None = None
        self.messages: list[dict[str, str]] = []
        self.requested_model = API_MODEL_VALUE
        self.runtime: dict[str, Any] = dict()
        self.model_source = "platform"
        self.user_id = None
        self.role_code = ""


def prepare_chat_request():
    """Validate the payload and resolve history, model and quota.

    Returns `(chat_request, None)` or `(None, error_response)`.
    """
    payload = request.get_json(silent=True) or dict()
    prompt = str(payload.get("prompt", "")).strip()
    raw_messages = payload.get("messages") or []
    if not prompt and not raw_messages:
        return None, error("请输入对话内容", code="PROMPT_REQUIRED")

    # Existing account API rows may predate the current .env settings.
    # Synchronize them before resolving the requested/default model.
    ensure_user_api_config(g.current_user.id)

    chat_request = ChatRequest()
    chat_request.prompt = prompt
    chat_request.user_id = g.current_user.id
    chat_request.role_code = g.current_user.role_code

    if payload.get("conversation_id"):
        try:
            chat_request.conversation_id = UUID(str(payload["conversation_id"]))
        except ValueError:
            return None, error("对话标识无效", code="CONVERSATION_ID_INVALID")
        access = get_conversation_access(chat_request.conversation_id, edit=True)
        if access is None:
            return None, error("对话不存在", code="CONVERSATION_NOT_FOUND", status=404)
        if payload.get("project_id") and str(access[0].project_id) != str(payload["project_id"]):
            return None, error("对话不属于当前项目", code="CONVERSATION_PROJECT_MISMATCH", status=409)

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    persisted_messages = []
    if chat_request.conversation_id:
        persisted_messages = db.session.scalars(
            select(Message)
            .where(Message.conversation_id == chat_request.conversation_id)
            .order_by(Message.sequence.desc())
            .limit(10)
        ).all()[::-1]
    history_source = (
        [{"role": item.role.lower(), "content": item.content} for item in persisted_messages]
        if persisted_messages
        else raw_messages[-10:]
    )
    for item in history_source:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    if prompt:
        messages.append({"role": "user", "content": prompt})
    chat_request.messages = messages

    requested_model = str(payload.get("model") or API_MODEL_VALUE)
    if requested_model in {"auto", ""}:
        requested_model = API_MODEL_VALUE
    chat_request.requested_model = requested_model
    max_output_tokens = int(current_app.config.get("USER_API_MODEL_MAX_OUTPUT_TOKENS", 3072))
    if requested_model.startswith("model_config:"):
        config = find_owned_config(requested_model.removeprefix("model_config:"), active_only=True)
        if config is None:
            return None, error("所选对话模型不存在、未验证或已停用", code="MODEL_CONFIG_UNAVAILABLE", status=409)
        if not config.encrypted_api_key or not config.key_nonce or not config.key_version:
            return None, error("所选对话模型的密钥不可用", code="MODEL_CONFIG_KEY_INVALID", status=409)
        try:
            api_key = decrypt_api_key(config.encrypted_api_key, config.key_nonce, config.key_version)
        except RuntimeError:
            return None, error("所选对话模型的密钥不可用", code="MODEL_CONFIG_KEY_INVALID", status=409)
        chat_request.runtime = {
            "model": config.model_name,
            "base_url": config.base_url,
            "api_key": api_key,
            "timeout_seconds": float((config.settings or dict()).get("timeout_seconds", 120)),
        }
        max_output_tokens = int((config.settings or dict()).get("max_output_tokens", 3072))
        chat_request.model_source = "api"
    else:
        chat_request.runtime = {"model": requested_model}
    chat_request.runtime["max_tokens"] = max_output_tokens
    try:
        ensure_token_quota(
            g.current_user.id,
            g.current_user.role_code,
            estimate_messages_tokens(messages, max_output_tokens),
        )
    except TokenQuotaExceeded as exc:
        return None, error(
            f"本周期免费 Token 额度已用尽，剩余 {exc.snapshot['remaining']} Token",
            code="TOKEN_QUOTA_EXCEEDED",
            status=402,
        )
    if prompt and chat_request.conversation_id:
        append_message(chat_request.conversation_id, "user", prompt)
        db.session.commit()
    return chat_request, None


def public_model_name(chat_request: ChatRequest, reported: Any) -> str:
    """The fine-tuned platform model is always shown under its own name."""
    if chat_request.requested_model == "vertical_domain":
        return str(current_app.config.get("PLATFORM_MODEL_DISPLAY_NAME") or "Qwen3.8-27B-SFT")
    return str(reported or chat_request.runtime.get("model") or "platform")


def finish_chat_turn(chat_request: ChatRequest, result: dict[str, Any]) -> dict[str, Any]:
    """Record usage and persist the assistant message; returns extra fields."""
    try:
        record_token_usage(
            user_id=chat_request.user_id,
            role=chat_request.role_code,
            source=chat_request.model_source,
            model_name=str(result.get("model") or chat_request.runtime.get("model") or "platform"),
            usage=result.get("usage"),
            request_kind="chat",
            fallback_input=estimate_messages_tokens(chat_request.messages),
            fallback_output=estimate_tokens(result.get("content")),
        )
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.warning("token usage recording failed", exc_info=True)
    extra: dict[str, Any] = dict()
    assistant_content = str(result.get("content") or "").strip()
    if chat_request.conversation_id and assistant_content:
        assistant_message = append_message(chat_request.conversation_id, "assistant", assistant_content)
        db.session.commit()
        extra = {"message_id": str(assistant_message.id), "conversation_id": str(chat_request.conversation_id)}
    return extra


@bp.post("/chat")
def chat():
    chat_request, failure = prepare_chat_request()
    if failure is not None:
        return failure
    # Do not keep a pooled connection checked out while the model is thinking.
    db.session.remove()
    try:
        result = run_openai_compatible_chat(messages=chat_request.messages, **chat_request.runtime)
    except RuntimeError as exc:
        return error(str(exc), code="MODEL_UNAVAILABLE", status=502)
    result = {**result, "model": public_model_name(chat_request, result.get("model"))}
    result = {**result, **finish_chat_turn(chat_request, result)}
    return ok(result)


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@bp.post("/chat/stream")
def chat_stream():
    """Server-sent events: `meta`, then `reasoning`/`delta` chunks, then `done` or `error`."""
    chat_request, failure = prepare_chat_request()
    if failure is not None:
        return failure
    display_model = public_model_name(chat_request, None)
    db.session.remove()

    @stream_with_context
    def generate():
        yield sse("meta", {"model": display_model})
        final: dict[str, Any] | None = None
        try:
            for item in stream_openai_compatible_chat(messages=chat_request.messages, **chat_request.runtime):
                if item["type"] == "done":
                    final = item
                else:
                    yield sse(item["type"], {"text": item["text"]})
        except RuntimeError as exc:
            yield sse("error", {"code": "MODEL_UNAVAILABLE", "message": str(exc)})
            return
        except Exception:  # noqa: BLE001
            current_app.logger.exception("chat stream failed")
            yield sse("error", {"code": "MODEL_UNAVAILABLE", "message": "模型服务暂时不可用，请稍后重试"})
            return
        if final is None or not str(final.get("content") or "").strip():
            yield sse("error", {"code": "MODEL_UNAVAILABLE", "message": "模型服务返回了空回答"})
            return
        result = {**final, "model": public_model_name(chat_request, final.get("model"))}
        extra = finish_chat_turn(chat_request, result)
        db.session.remove()
        yield sse("done", {"model": result["model"], "usage": result.get("usage") or dict(), **extra})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Tell nginx not to buffer this response so chunks reach the browser at once.
            "X-Accel-Buffering": "no",
        },
    )
