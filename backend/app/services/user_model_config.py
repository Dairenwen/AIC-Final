from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import ModelConfig
from .model_credentials import decrypt_api_key, encrypt_api_key


ACCOUNT_API_CONFIG_NAME = "API 对话模型"
API_MODEL_VALUE = "api_model"


@dataclass(frozen=True)
class UserApiModelRuntime:
    base_url: str
    model_name: str
    api_key: str
    timeout_seconds: float


def get_user_api_model_runtime() -> UserApiModelRuntime:
    """Return the deployment-managed API model used by platform chat."""
    return UserApiModelRuntime(
        base_url=str(current_app.config.get("USER_API_MODEL_BASE_URL") or "").strip().rstrip("/"),
        model_name=str(current_app.config.get("USER_API_MODEL_NAME") or "").strip(),
        api_key=str(current_app.config.get("USER_API_MODEL_API_KEY") or "").strip(),
        timeout_seconds=float(current_app.config.get("USER_API_MODEL_TIMEOUT_SECONDS", 120)),
    )


def ensure_user_api_config(user_id: UUID | str) -> ModelConfig | None:
    """Provision and synchronize the deployment-managed API chat slot."""
    try:
        item = db.session.scalar(
            select(ModelConfig).where(
                ModelConfig.owner_user_id == user_id,
                ModelConfig.config_scope == "USER",
                ModelConfig.is_account_api.is_(True),
                ModelConfig.deleted_at.is_(None),
            )
        )
        if item is not None:
            if current_app.config.get("USER_API_MODEL_ENV_OVERRIDE", True):
                _synchronize_from_environment(item)
            return item

        api_key = str(current_app.config.get("USER_API_MODEL_API_KEY") or "").strip()
        encrypted = nonce = key_version = None
        key_last_four = None
        if api_key:
            encrypted, nonce, key_version = encrypt_api_key(api_key)
            key_last_four = api_key[-4:]
        item = ModelConfig(
            config_scope="USER",
            owner_user_id=user_id,
            provider_code="openai_compatible",
            model_type_code="chat",
            name=ACCOUNT_API_CONFIG_NAME,
            base_url=str(current_app.config.get("USER_API_MODEL_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/"),
            model_name=str(current_app.config.get("USER_API_MODEL_NAME") or "deepseek-flash"),
            capabilities=["chat_completions"],
            settings={
                "timeout_seconds": int(current_app.config.get("USER_API_MODEL_TIMEOUT_SECONDS", 120)),
                "max_output_tokens": int(current_app.config.get("USER_API_MODEL_MAX_OUTPUT_TOKENS", 3072)),
                "system_default": API_MODEL_VALUE,
            },
            default_for=["chat"],
            encrypted_api_key=encrypted,
            key_nonce=nonce,
            key_version=key_version,
            key_last_four=key_last_four,
            allow_platform_fallback=False,
            status="DRAFT",
            is_account_api=True,
        )
        db.session.add(item)
        db.session.commit()
        return item
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning("account API model provisioning is unavailable", exc_info=True)
        return None


def _synchronize_from_environment(item: ModelConfig) -> None:
    """Apply current deployment settings to an existing account API row."""
    base_url = str(current_app.config.get("USER_API_MODEL_BASE_URL") or "").strip().rstrip("/")
    model_name = str(current_app.config.get("USER_API_MODEL_NAME") or "").strip()
    configured_key = str(current_app.config.get("USER_API_MODEL_API_KEY") or "").strip()
    timeout_seconds = int(current_app.config.get("USER_API_MODEL_TIMEOUT_SECONDS", 120))
    max_output_tokens = int(current_app.config.get("USER_API_MODEL_MAX_OUTPUT_TOKENS", 3072))
    if not base_url or not model_name:
        return

    settings = dict(item.settings or {})
    changed = item.base_url != base_url or item.model_name != model_name
    if settings.get("timeout_seconds") != timeout_seconds:
        changed = True
    if settings.get("max_output_tokens") != max_output_tokens:
        changed = True

    if configured_key:
        try:
            current_key = decrypt_api_key(item.encrypted_api_key, item.key_nonce, item.key_version)
        except (RuntimeError, TypeError, ValueError):
            current_key = ""
        if current_key != configured_key:
            encrypted, nonce, key_version = encrypt_api_key(configured_key)
            item.encrypted_api_key = encrypted
            item.key_nonce = nonce
            item.key_version = key_version
            item.key_last_four = configured_key[-4:]
            changed = True

    if not changed:
        return

    item.base_url = base_url
    item.model_name = model_name
    item.settings = {
        **settings,
        "timeout_seconds": timeout_seconds,
        "max_output_tokens": max_output_tokens,
    }
    # A changed deployment credential/model must be tested before use.
    item.status = "DRAFT"
    item.last_verified_at = None
    item.last_error_code = None
    db.session.commit()
