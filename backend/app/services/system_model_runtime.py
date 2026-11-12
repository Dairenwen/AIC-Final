from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import ModelConfig
from .model_credentials import decrypt_api_key
from .platform_model_runtime import get_platform_model_runtime
from .user_model_config import API_MODEL_VALUE, ensure_user_api_config, get_user_api_model_runtime


def get_system_model_runtime(user_id: UUID | str | None = None) -> dict[str, Any]:
    """Resolve the model selected as the system default in the model library."""
    account = ensure_user_api_config(user_id) if user_id is not None else None
    default_value = API_MODEL_VALUE
    if account is not None:
        default_value = str((account.settings or {}).get("system_default") or API_MODEL_VALUE)

    if default_value.startswith("model_config:") and user_id is not None:
        personal = _default_personal_runtime(default_value, user_id)
        if personal is not None:
            return personal

    if default_value == "vertical_domain":
        runtime = get_platform_model_runtime()
        return {
            "model": runtime.model_name,
            "model_name": runtime.model_name,
            "base_url": runtime.base_url,
            "api_key": runtime.api_key,
            "timeout_seconds": runtime.timeout_seconds,
            "source": "platform",
            "selector": "vertical_domain",
        }

    runtime = get_user_api_model_runtime()
    return {
        "model": runtime.model_name,
        "model_name": runtime.model_name,
        "base_url": runtime.base_url,
        "api_key": runtime.api_key,
        "timeout_seconds": runtime.timeout_seconds,
        "source": "account_api",
        "selector": API_MODEL_VALUE,
    }


def get_owned_model_runtime(config: ModelConfig, user_id: UUID | str) -> dict[str, Any]:
    """Resolve an explicitly selected, verified personal model."""
    if (
        config.owner_user_id != user_id
        or config.config_scope != "USER"
        or config.status != "ACTIVE"
        or config.deleted_at is not None
    ):
        raise RuntimeError("所选个人模型不存在、未验证或已停用")
    if not config.encrypted_api_key or not config.key_nonce or not config.key_version:
        raise RuntimeError("所选个人模型缺少有效的 API Key")
    settings = config.settings or {}
    return {
        "model": config.model_name,
        "model_name": config.model_name,
        "base_url": config.base_url,
        "api_key": decrypt_api_key(config.encrypted_api_key, config.key_nonce, config.key_version),
        "timeout_seconds": max(10, min(float(settings.get("timeout_seconds", 120)), 600)),
        "source": "personal",
        "selector": f"model_config:{config.id}",
    }


def _default_personal_runtime(default_value: str, user_id: UUID | str) -> dict[str, Any] | None:
    """A verified personal chat model chosen as the default; None falls back to the API model."""
    try:
        config_id = UUID(default_value.removeprefix("model_config:"))
        owner_id = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        config = db.session.get(ModelConfig, config_id)
        if config is None or config.model_type_code != "chat":
            return None
        return get_owned_model_runtime(config, owner_id)
    except (ValueError, RuntimeError, SQLAlchemyError):
        db.session.rollback()
        return None
