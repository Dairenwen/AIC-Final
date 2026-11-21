from __future__ import annotations

from flask import Blueprint, current_app, g, request

from ..services.model_credentials import decrypt_api_key
from ..services.token_usage import (
    TokenQuotaExceeded,
    ensure_token_quota,
    estimate_tokens,
    record_token_usage,
)
from .model_configs import find_default_chat_config, find_owned_config
from .responses import error, ok
from ..services.user_model_config import API_MODEL_VALUE


bp = Blueprint("rag", __name__)


@bp.post("/rag/answers")
def create_rag_answer():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return error("请输入知识库问题", code="RAG_QUESTION_REQUIRED")
    if len(question) > 4000:
        return error("知识库问题不能超过 4000 个字符", code="RAG_QUESTION_TOO_LONG")
    document_ids = payload.get("document_ids") or []
    if not isinstance(document_ids, list) or any(not isinstance(item, str) for item in document_ids):
        return error("文献范围格式无效", code="RAG_DOCUMENT_SCOPE_INVALID")
    if len(document_ids) != len(set(document_ids)):
        return error("文献范围不能包含重复项", code="RAG_DOCUMENT_SCOPE_INVALID")
    if payload.get("stream", False) is not False:
        return error("当前仅支持非流式知识库问答", code="RAG_STREAM_UNSUPPORTED")

    requested_model = str(payload.get("model") or API_MODEL_VALUE)
    if requested_model in {"auto", ""}:
        requested_model = API_MODEL_VALUE
    model_runtime = {}
    model_source = "platform"
    if requested_model.startswith("model_config:"):
        config = find_owned_config(requested_model.removeprefix("model_config:"), active_only=True)
        if config is None:
            return error("所选对话模型不存在、未验证或已停用", code="MODEL_CONFIG_UNAVAILABLE", status=409)
        if not config.encrypted_api_key or not config.key_nonce or not config.key_version:
            return error("所选对话模型的密钥不可用", code="MODEL_CONFIG_KEY_INVALID", status=409)
        try:
            api_key = decrypt_api_key(config.encrypted_api_key, config.key_nonce, config.key_version)
        except RuntimeError:
            return error("所选对话模型的密钥不可用", code="MODEL_CONFIG_KEY_INVALID", status=409)
        requested_model = config.model_name
        model_runtime = {
            "base_url": config.base_url,
            "api_key": api_key,
            "timeout_seconds": float((config.settings or {}).get("timeout_seconds", 120)),
        }
        model_source = "api"

    try:
        ensure_token_quota(
            g.current_user.id,
            g.current_user.role_code,
            estimate_tokens(question) + 1200,
        )
    except TokenQuotaExceeded as exc:
        return error(
            f"本周期免费 Token 额度已用尽，剩余 {exc.snapshot['remaining']} Token",
            code="TOKEN_QUOTA_EXCEEDED",
            status=402,
        )

    service = current_app.extensions["personal_academic_rag"]
    try:
        result = service.answer(
            question=question,
            user_id=str(g.current_user.id),
            role=str(g.current_user.role_code),
            document_ids=document_ids,
            model=requested_model,
            model_runtime=model_runtime,
        )
    except PermissionError:
        return error("请求包含未授权文献", code="RAG_FORBIDDEN_SCOPE", status=403)
    except RuntimeError as exc:
        current_app.logger.exception("personal RAG request failed")
        return error(str(exc), code="RAG_UNAVAILABLE", status=503)
    if result.get("model") or result.get("usage"):
        try:
            record_token_usage(
                user_id=g.current_user.id,
                role=g.current_user.role_code,
                source=model_source,
                model_name=str(result.get("model") or requested_model),
                usage=result.get("usage"),
                request_kind="rag",
                fallback_input=estimate_tokens(question),
                fallback_output=estimate_tokens(result.get("answer")),
            )
        except Exception:  # noqa: BLE001
            current_app.logger.warning("token usage recording failed", exc_info=True)
    return ok(result)
