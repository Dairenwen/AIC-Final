from __future__ import annotations

import sys
import base64
import json
from pathlib import Path

from dotenv import load_dotenv
from flask import current_app
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app import create_app
from app.extensions import db
from app.models import ModelConfig, User
from app.services.model_credentials import encrypt_api_key
from app.services.user_model_config import ensure_user_api_config


PROVIDERS = (
    ("openai_compatible", "OpenAI Compatible", ""),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    ("dashscope", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)
MODEL_TYPES = (
    ("chat", "对话模型", "OpenAI-compatible Chat Completions 模型"),
    ("embedding", "嵌入模型", "文本向量化与语义检索模型"),
    ("rerank", "重排序模型", "检索结果相关性重排序模型"),
)
PLATFORM_MODEL_PLACEHOLDER_API_KEY = "aaa"


def migrate() -> None:
    app = create_app()
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS zhiyan.model_types (
                code VARCHAR(64) PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                description TEXT,
                created_by UUID,
                status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("ALTER TABLE zhiyan.model_providers ADD COLUMN IF NOT EXISTS created_by UUID"))
        connection.execute(text("ALTER TABLE zhiyan.model_configs ADD COLUMN IF NOT EXISTS model_type_code VARCHAR(64)"))
        connection.execute(text("ALTER TABLE zhiyan.model_configs ADD COLUMN IF NOT EXISTS is_account_api BOOLEAN NOT NULL DEFAULT false"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS zhiyan.token_usage_records (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES zhiyan.users(id) ON DELETE CASCADE,
                period_start DATE NOT NULL,
                source VARCHAR(16) NOT NULL,
                model_name VARCHAR(200),
                input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT NOT NULL DEFAULT 0,
                request_kind VARCHAR(32) NOT NULL DEFAULT 'chat',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_token_usage_records_user_period ON zhiyan.token_usage_records (user_id, period_start)"))
        for code, name, description in MODEL_TYPES:
            connection.execute(
                text("""
                    INSERT INTO zhiyan.model_types (code, name, description, status)
                    VALUES (:code, :name, :description, 'ACTIVE')
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        status = 'ACTIVE',
                        updated_at = now()
                """),
                {"code": code, "name": name, "description": description},
            )
        for code, name, base_url in PROVIDERS:
            connection.execute(
                text("""
                    INSERT INTO zhiyan.model_providers (
                        code, name, default_base_url, allow_custom_url, capabilities,
                        config_schema, status, created_at, updated_at
                    ) VALUES (
                        :code, :name, :base_url, true, '["chat_completions"]'::jsonb,
                        '{}'::jsonb, 'ACTIVE', now(), now()
                    )
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name,
                        default_base_url = EXCLUDED.default_base_url,
                        allow_custom_url = true,
                        status = 'ACTIVE',
                        updated_at = now()
                """),
                {"code": code, "name": name, "base_url": base_url},
            )
        connection.execute(text("UPDATE zhiyan.model_configs SET model_type_code = 'chat' WHERE model_type_code IS NULL"))
        connection.execute(text("UPDATE zhiyan.model_configs SET is_account_api = false WHERE is_account_api IS NULL"))
        connection.execute(text("ALTER TABLE zhiyan.model_configs ALTER COLUMN model_type_code SET DEFAULT 'chat'"))
        connection.execute(text("ALTER TABLE zhiyan.model_configs ALTER COLUMN model_type_code SET NOT NULL"))
        configured_api_key = str(app.config.get("QWEN_DPO_API_KEY") or PLATFORM_MODEL_PLACEHOLDER_API_KEY).strip()
        encrypted, nonce, key_version = encrypt_api_key(configured_api_key)
        value_json = {
            "base_url": app.config["QWEN_DPO_BASE_URL"],
            "model_name": app.config["QWEN_DPO_MODEL"],
            "encrypted_api_key": base64.b64encode(encrypted).decode("ascii"),
            "key_nonce": base64.b64encode(nonce).decode("ascii"),
            "key_version": key_version,
            "timeout_seconds": float(app.config["QWEN_DPO_TIMEOUT_SECONDS"]),
        }
        connection.execute(
            text("""
                INSERT INTO zhiyan.system_settings(
                    setting_key, value_json, description, updated_at
                ) VALUES (
                    'platform_model_runtime', CAST(:value_json AS jsonb),
                    '平台通用模型运行配置，API Key 使用 AES-GCM 加密保存', now()
                )
                ON CONFLICT (setting_key) DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    description = EXCLUDED.description,
                    updated_at = now()
            """),
            {"value_json": json.dumps(value_json)},
        )
        connection.execute(text("""
            DO $$ BEGIN
                ALTER TABLE zhiyan.model_configs
                ADD CONSTRAINT fk_model_configs_model_type
                FOREIGN KEY (model_type_code) REFERENCES zhiyan.model_types(code);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))

    # Backfill the reserved API slot for users created before this migration.
    with app.app_context():
        user_ids = db.session.scalars(select(User.id)).all()
        for user_id in user_ids:
            ensure_user_api_config(user_id)
        updated = migrate_account_api_defaults()
        if updated:
            print(f"Updated {updated} account API model configuration(s) to the configured default.")


def migrate_account_api_defaults() -> int:
    """Update the reserved per-account API slot after changing its deployment default.

    Personal model configurations are intentionally left untouched. Reserved account
    slots are marked DRAFT when their endpoint/model changes so an operator must test
    the new connection before selecting it as the account default.
    """
    base_url = str(current_app.config.get("USER_API_MODEL_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
    model_name = str(current_app.config.get("USER_API_MODEL_NAME") or "deepseek-ai/DeepSeek-V4-Flash").strip()
    api_key = str(current_app.config.get("USER_API_MODEL_API_KEY") or "").strip()
    updated = 0
    items = db.session.scalars(
        select(ModelConfig).where(
            ModelConfig.config_scope == "USER",
            ModelConfig.is_account_api.is_(True),
            ModelConfig.deleted_at.is_(None),
        )
    ).all()
    for item in items:
        connection_changed = item.base_url != base_url or item.model_name != model_name
        missing_key = bool(api_key) and not item.encrypted_api_key
        if not connection_changed and not missing_key:
            continue
        item.provider_code = "openai_compatible"
        item.model_type_code = "chat"
        item.base_url = base_url
        item.model_name = model_name
        item.settings = {
            **(item.settings or {}),
            "timeout_seconds": int(current_app.config.get("USER_API_MODEL_TIMEOUT_SECONDS", 120)),
            "max_output_tokens": int(current_app.config.get("USER_API_MODEL_MAX_OUTPUT_TOKENS", 3072)),
        }
        if api_key:
            item.encrypted_api_key, item.key_nonce, item.key_version = encrypt_api_key(api_key)
            item.key_last_four = api_key[-4:]
        item.status = "DRAFT"
        item.last_verified_at = None
        item.last_error_code = None
        item.default_for = [value for value in (item.default_for or []) if value != "chat"]
        updated += 1
    if updated:
        db.session.commit()
    return updated


if __name__ == "__main__":
    migrate()
