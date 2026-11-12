"""Resolve the model a task runs on, without contacting its provider.

A task either names one of the user's own models ("model_config:<id>") or
leaves the choice open ("auto").  The fine-tuned platform model is an optional
deployment; when it is not enabled an open choice must land on the user's own
default model, otherwise every agent would be asked to call a service that
does not exist.
"""
from uuid import UUID

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import ModelConfig, Task
from .model_credentials import decrypt_api_key

# Values a client sends when it has not picked a specific model.
OPEN_MODEL_CHOICES = {"", "auto", "platform", "personal", "自动选择模型"}
# Values that name the fine-tuned platform model.
PLATFORM_MODEL_CHOICES = {"vertical_domain", "垂域模型", "qwen3.6-dpo"}

NO_MODEL_MESSAGE = "还没有可用的模型：请在「个人中心 / 模型库」添加并测试一个对话模型，再设为默认。"


def select_personal_chat_config(user_id: UUID) -> ModelConfig | None:
    """The user's default chat model, or their only verified one."""
    items = db.session.scalars(
        select(ModelConfig).where(
            ModelConfig.owner_user_id == user_id,
            ModelConfig.config_scope == "USER",
            ModelConfig.status == "ACTIVE",
            ModelConfig.deleted_at.is_(None),
        )
    ).all()
    chat_items = [
        item for item in items
        if (item.model_type_code or "chat") == "chat"
        and item.encrypted_api_key and item.key_nonce and item.key_version
    ]
    default = next((item for item in chat_items if "chat" in (item.default_for or [])), None)
    if default is not None:
        return default
    # Without an explicit default, a single verified model is unambiguous.
    return chat_items[0] if len(chat_items) == 1 else None


def falls_back_to_personal_model(requested_model: object) -> bool:
    """Whether a request that names no personal model should use the default one."""
    requested = str(requested_model or "").strip()
    if requested in OPEN_MODEL_CHOICES:
        return True
    platform_enabled = bool(current_app.config.get("PLATFORM_MODEL_ENABLED", False))
    return requested in PLATFORM_MODEL_CHOICES and not platform_enabled


def resolve_task_model_runtime(task: Task, user_id: UUID) -> dict[str, object] | None:
    if task.model_config_id is None:
        return None
    item = db.session.get(ModelConfig, task.model_config_id)
    if (item is None or item.owner_user_id != user_id or item.config_scope != "USER"
            or item.status != "ACTIVE" or item.deleted_at is not None):
        raise RuntimeError("所选个人模型不存在、未验证或已停用")
    if not item.encrypted_api_key or not item.key_nonce or not item.key_version:
        raise RuntimeError("所选个人模型缺少有效的 API Key")
    settings = item.settings or {}
    return {
        "base_url": item.base_url,
        "model_name": item.model_name,
        "api_key": decrypt_api_key(item.encrypted_api_key, item.key_nonce, item.key_version),
        "timeout_seconds": max(10, min(float(settings.get("timeout_seconds", 120)), 600)),
    }
