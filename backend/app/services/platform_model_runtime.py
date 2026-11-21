from __future__ import annotations

import base64
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import text

from ..extensions import db
from .model_credentials import decrypt_api_key


SETTING_KEY = "platform_model_runtime"


@dataclass(frozen=True)
class PlatformModelRuntime:
    base_url: str
    model_name: str
    api_key: str
    timeout_seconds: float


def get_platform_model_runtime() -> PlatformModelRuntime:
    fallback = PlatformModelRuntime(
        base_url=str(current_app.config["QWEN_DPO_BASE_URL"]).rstrip("/"),
        model_name=str(current_app.config["QWEN_DPO_MODEL"]),
        api_key=str(current_app.config.get("QWEN_DPO_API_KEY") or "").strip(),
        timeout_seconds=float(current_app.config["QWEN_DPO_TIMEOUT_SECONDS"]),
    )
    if current_app.config.get("QWEN_DPO_ENV_OVERRIDE", False):
        return fallback
    try:
        value = db.session.execute(
            text(
                "SELECT value_json FROM zhiyan.system_settings "
                "WHERE setting_key = :setting_key"
            ),
            {"setting_key": SETTING_KEY},
        ).scalar_one_or_none()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("platform model database setting is unavailable", exc_info=True)
        return fallback
    if not isinstance(value, dict):
        return fallback
    try:
        encrypted = base64.b64decode(str(value["encrypted_api_key"]))
        nonce = base64.b64decode(str(value["key_nonce"]))
        api_key = decrypt_api_key(encrypted, nonce, str(value["key_version"]))
        return PlatformModelRuntime(
            base_url=str(value.get("base_url") or fallback.base_url).rstrip("/"),
            model_name=str(value.get("model_name") or fallback.model_name),
            api_key=api_key,
            timeout_seconds=float(value.get("timeout_seconds") or fallback.timeout_seconds),
        )
    except (KeyError, TypeError, ValueError, RuntimeError):
        current_app.logger.warning("platform model database setting is invalid", exc_info=True)
        return fallback
