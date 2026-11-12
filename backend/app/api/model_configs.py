from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlparse
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import ModelConfig, ModelProvider, ModelType
from ..services.model_credentials import decrypt_api_key, encrypt_api_key
from ..services.platform_model_runtime import get_platform_model_runtime
from ..services.user_model_config import API_MODEL_VALUE, ensure_user_api_config, get_user_api_model_runtime
from .responses import error, ok


bp = Blueprint("model_configs", __name__)

@bp.get("/model-providers")
def list_model_providers():
    rows = db.session.scalars(
        select(ModelProvider)
        .where(
            ModelProvider.status == "ACTIVE",
            (ModelProvider.created_by.is_(None)) | (ModelProvider.created_by == g.current_user.id),
        )
        .order_by(ModelProvider.created_by.asc().nullsfirst(), ModelProvider.name)
    ).all()
    return ok([serialize_provider(item) for item in rows], meta={"total": len(rows)})


@bp.post("/model-providers")
def create_model_provider():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    default_base_url = normalize_base_url(payload.get("default_base_url"))
    if not name or len(name) > 120:
        return error("提供商名称不能为空且不能超过 120 个字符", code="MODEL_PROVIDER_NAME_INVALID")
    if default_base_url is None:
        return error("默认 API Base URL 必须是有效的 HTTP(S) 地址", code="MODEL_PROVIDER_URL_INVALID")
    duplicate = db.session.scalar(
        select(ModelProvider).where(
            ModelProvider.name == name,
            ModelProvider.status == "ACTIVE",
            (ModelProvider.created_by.is_(None)) | (ModelProvider.created_by == g.current_user.id),
        )
    )
    if duplicate is not None:
        return error("该模型提供商已存在", code="MODEL_PROVIDER_EXISTS", status=409)
    item = ModelProvider(
        code=f"custom_{uuid4().hex[:20]}",
        name=name,
        default_base_url=default_base_url,
        allow_custom_url=True,
        capabilities=["chat_completions"],
        config_schema={},
        created_by=g.current_user.id,
        status="ACTIVE",
    )
    db.session.add(item)
    db.session.commit()
    return ok(serialize_provider(item), status=201)


@bp.get("/model-types")
def list_model_types():
    rows = db.session.scalars(
        select(ModelType)
        .where(
            ModelType.status == "ACTIVE",
            (ModelType.created_by.is_(None)) | (ModelType.created_by == g.current_user.id),
        )
        .order_by(ModelType.created_by.asc().nullsfirst(), ModelType.name)
    ).all()
    return ok([serialize_model_type(item) for item in rows], meta={"total": len(rows)})


@bp.post("/model-types")
def create_model_type():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not name or len(name) > 120:
        return error("模型类型名称不能为空且不能超过 120 个字符", code="MODEL_TYPE_NAME_INVALID")
    duplicate = db.session.scalar(
        select(ModelType).where(
            ModelType.name == name,
            ModelType.status == "ACTIVE",
            (ModelType.created_by.is_(None)) | (ModelType.created_by == g.current_user.id),
        )
    )
    if duplicate is not None:
        return error("该模型类型已存在", code="MODEL_TYPE_EXISTS", status=409)
    item = ModelType(
        code=f"custom_{uuid4().hex[:20]}",
        name=name,
        description=description or None,
        created_by=g.current_user.id,
        status="ACTIVE",
    )
    db.session.add(item)
    db.session.commit()
    return ok(serialize_model_type(item), status=201)


@bp.get("/model-configs")
def list_model_configs():
    ensure_user_api_config(g.current_user.id)
    items = db.session.scalars(
        select(ModelConfig)
        .where(
            ModelConfig.owner_user_id == g.current_user.id,
            ModelConfig.config_scope == "USER",
            ModelConfig.deleted_at.is_(None),
        )
        .order_by(ModelConfig.updated_at.desc())
    ).all()
    return ok([serialize_config(item) for item in items], meta={"total": len(items)})


@bp.get("/model-configs/default")
def get_default_model_config():
    item = ensure_user_api_config(g.current_user.id)
    if item is not None and (item.settings or {}).get("system_default", API_MODEL_VALUE) == API_MODEL_VALUE:
        runtime = get_user_api_model_runtime()
        return ok({
            "value": API_MODEL_VALUE,
            "name": "API 对话模型",
            "model_name": runtime.model_name,
            "source": "account_api",
            "config_id": str(item.id),
        })
    return ok(builtin_vertical_model())


@bp.post("/model-configs/default")
def set_default_model_config():
    payload = request.get_json(silent=True) or {}
    config_id = str(payload.get("config_id") or API_MODEL_VALUE)
    if config_id not in {API_MODEL_VALUE, "vertical_domain"}:
        return error("系统模型由平台统一管理，不能切换个人模型", code="MODEL_DEFAULT_LOCKED", status=403)

    account_item = ensure_user_api_config(g.current_user.id)
    if account_item is not None:
        account_item.settings = {**(account_item.settings or {}), "system_default": config_id}
    db.session.commit()
    return get_default_model_config()


@bp.post("/model-configs")
def create_model_config():
    payload = request.get_json(silent=True) or {}
    validated, validation_error = validate_payload(payload, require_api_key=True)
    if validation_error:
        return validation_error

    duplicate = db.session.scalar(
        select(ModelConfig).where(
            ModelConfig.owner_user_id == g.current_user.id,
            ModelConfig.config_scope == "USER",
            ModelConfig.name == validated["name"],
            ModelConfig.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        return error("配置名称已存在", code="MODEL_CONFIG_NAME_EXISTS", status=409)

    encrypted, nonce, version = encrypt_api_key(validated.pop("api_key"))
    item = ModelConfig(
        config_scope="USER",
        owner_user_id=g.current_user.id,
        capabilities=capabilities_for_type(validated["model_type_code"]),
        default_for=[],
        encrypted_api_key=encrypted,
        key_nonce=nonce,
        key_version=version,
        key_last_four=validated.pop("key_last_four"),
        allow_platform_fallback=False,
        external_processing_acknowledged_at=datetime.now(UTC),
        status="DRAFT",
        **validated,
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("配置名称已存在", code="MODEL_CONFIG_NAME_EXISTS", status=409)
    return ok(serialize_config(item), status=201)


@bp.patch("/model-configs/<config_id>")
def update_model_config(config_id: str):
    item = find_owned_config(config_id)
    if item is None:
        return error("模型配置不存在", code="MODEL_CONFIG_NOT_FOUND", status=404)
    if item.is_account_api:
        return error("平台 API 对话模型由系统统一管理，不能编辑", code="MODEL_CONFIG_READ_ONLY", status=403)
    payload = request.get_json(silent=True) or {}
    merged = {
        "provider_code": payload.get("provider_code", item.provider_code),
        "model_type_code": payload.get("model_type_code", item.model_type_code),
        "name": payload.get("name", item.name),
        "base_url": payload.get("base_url", item.base_url),
        "model_name": payload.get("model_name", item.model_name),
        "api_key": payload.get("api_key"),
        "timeout_seconds": payload.get("timeout_seconds", (item.settings or {}).get("timeout_seconds", 120)),
        "max_output_tokens": payload.get("max_output_tokens", (item.settings or {}).get("max_output_tokens", 3072)),
    }
    validated, validation_error = validate_payload(merged, require_api_key=False)
    if validation_error:
        return validation_error
    connection_changed = any(
        [
            item.provider_code != validated["provider_code"],
            item.base_url != validated["base_url"],
            item.model_name != validated["model_name"],
            bool(validated.get("api_key")),
        ]
    )
    item.provider_code = validated["provider_code"]
    item.model_type_code = validated["model_type_code"]
    item.name = validated["name"]
    item.base_url = validated["base_url"]
    item.model_name = validated["model_name"]
    item.settings = validated["settings"]
    item.capabilities = capabilities_for_type(validated["model_type_code"])
    api_key = validated.get("api_key")
    if api_key:
        item.encrypted_api_key, item.key_nonce, item.key_version = encrypt_api_key(api_key)
        item.key_last_four = validated["key_last_four"]
    if connection_changed:
        item.status = "DRAFT"
        item.last_verified_at = None
        item.last_error_code = None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("配置名称已存在", code="MODEL_CONFIG_NAME_EXISTS", status=409)
    return ok(serialize_config(item))


@bp.post("/model-configs/<config_id>/verify")
def verify_model_config(config_id: str):
    item = find_owned_config(config_id)
    if item is None:
        return error("模型配置不存在", code="MODEL_CONFIG_NOT_FOUND", status=404)
    try:
        api_key = decrypt_config_api_key(item)
    except RuntimeError:
        item.status = "INVALID"
        item.last_error_code = "CREDENTIAL_DECRYPTION_FAILED"
        db.session.commit()
        return error("无法读取该配置的 API Key，请重新填写", code="MODEL_KEY_INVALID", status=422)

    item.status = "VERIFYING"
    item.last_error_code = None
    db.session.commit()
    try:
        verify_openai_compatible(
            base_url=item.base_url,
            model_name=item.model_name,
            api_key=api_key,
            timeout=min(float((item.settings or {}).get("timeout_seconds", 120)), 30.0),
        )
    except ModelVerificationError as exc:
        item.status = "INVALID"
        item.last_verified_at = None
        item.last_error_code = exc.code
        db.session.commit()
        return error(exc.public_message, code=exc.code, status=422)

    item.status = "ACTIVE"
    item.last_verified_at = datetime.now(UTC)
    item.last_error_code = None
    db.session.commit()
    return ok(serialize_config(item))


@bp.post("/model-configs/vertical/verify")
def verify_vertical_model():
    runtime = get_platform_model_runtime()
    try:
        verify_openai_compatible(
            base_url=runtime.base_url,
            model_name=runtime.model_name,
            api_key=runtime.api_key,
            timeout=min(
                runtime.timeout_seconds,
                float(current_app.config["MODEL_VERIFY_TIMEOUT_SECONDS"]),
            ),
        )
    except ModelVerificationError as exc:
        return error(exc.public_message, code=exc.code, status=422)
    return ok(builtin_vertical_model())


@bp.post("/model-configs/api/verify")
def verify_api_model():
    runtime = get_user_api_model_runtime()
    try:
        verify_openai_compatible(
            base_url=runtime.base_url,
            model_name=runtime.model_name,
            api_key=runtime.api_key,
            timeout=min(runtime.timeout_seconds, float(current_app.config["MODEL_VERIFY_TIMEOUT_SECONDS"])),
        )
    except ModelVerificationError as exc:
        return error(exc.public_message, code=exc.code, status=422)
    return ok({
        "value": API_MODEL_VALUE,
        "name": "API 对话模型",
        "model_name": runtime.model_name,
        "source": "account_api",
        "config_id": None,
    })


@bp.post("/model-configs/<config_id>/status")
def change_model_config_status(config_id: str):
    item = find_owned_config(config_id)
    if item is None:
        return error("模型配置不存在", code="MODEL_CONFIG_NOT_FOUND", status=404)
    if item.is_account_api:
        return error("平台 API 对话模型由系统统一管理，不能停用", code="MODEL_CONFIG_READ_ONLY", status=403)
    status = str((request.get_json(silent=True) or {}).get("status") or "").upper()
    if status not in {"ACTIVE", "DISABLED"}:
        return error("仅支持启用或停用模型配置", code="MODEL_STATUS_INVALID")
    if status == "ACTIVE" and (item.status != "DISABLED" or item.last_verified_at is None):
        return error("请先验证连接后再启用", code="MODEL_VERIFICATION_REQUIRED", status=409)
    item.status = status
    if status == "DISABLED":
        item.default_for = [value for value in (item.default_for or []) if value != "chat"]
    db.session.commit()
    return ok(serialize_config(item))


@bp.delete("/model-configs/<config_id>")
def delete_model_config(config_id: str):
    item = find_owned_config(config_id)
    if item is None:
        return error("模型配置不存在", code="MODEL_CONFIG_NOT_FOUND", status=404)
    if item.is_account_api:
        return error("账号基础 API 对话模型不能删除，请编辑或停用它", code="MODEL_CONFIG_ACCOUNT_REQUIRED", status=409)
    item.status = "DELETED"
    item.default_for = [value for value in (item.default_for or []) if value != "chat"]
    item.deleted_at = datetime.now(UTC)
    db.session.commit()
    return ok({"id": str(item.id), "deleted": True})


def find_owned_config(config_id: str, *, active_only: bool = False) -> ModelConfig | None:
    try:
        parsed_id = UUID(str(config_id))
    except ValueError:
        return None
    filters = [
        ModelConfig.id == parsed_id,
        ModelConfig.owner_user_id == g.current_user.id,
        ModelConfig.config_scope == "USER",
        ModelConfig.deleted_at.is_(None),
    ]
    if active_only:
        filters.append(ModelConfig.status == "ACTIVE")
    return db.session.scalar(select(ModelConfig).where(*filters))


def find_default_chat_config(user_id) -> ModelConfig | None:
    # Synchronize the deployment-managed account API row before selecting it.
    ensure_user_api_config(user_id)
    items = db.session.scalars(
        select(ModelConfig).where(
            ModelConfig.owner_user_id == user_id,
            ModelConfig.config_scope == "USER",
            ModelConfig.status == "ACTIVE",
            ModelConfig.deleted_at.is_(None),
        )
    ).all()
    return next((item for item in items if "chat" in (item.default_for or [])), None)


def builtin_vertical_model() -> dict:
    return {
        "value": "vertical_domain",
        "name": "平台专用模型",
        "model_name": str(current_app.config.get("PLATFORM_MODEL_DISPLAY_NAME") or "qwen3.6-27B-sft"),
        "source": "builtin",
        "config_id": None,
    }


def decrypt_config_api_key(item: ModelConfig) -> str:
    if not item.encrypted_api_key or not item.key_nonce or not item.key_version:
        raise RuntimeError("模型配置缺少 API Key")
    return decrypt_api_key(item.encrypted_api_key, item.key_nonce, item.key_version)


def validate_payload(payload: dict, *, require_api_key: bool):
    provider_code = str(payload.get("provider_code") or "").strip().lower()
    model_type_code = str(payload.get("model_type_code") or "chat").strip().lower()
    name = str(payload.get("name") or "").strip()
    model_name = str(payload.get("model_name") or "").strip()
    base_url = normalize_base_url(payload.get("base_url"))
    api_key = str(payload.get("api_key") or "").strip()
    provider = db.session.scalar(
        select(ModelProvider).where(
            ModelProvider.code == provider_code,
            ModelProvider.status == "ACTIVE",
            (ModelProvider.created_by.is_(None)) | (ModelProvider.created_by == g.current_user.id),
        )
    )
    if provider is None:
        return None, error("不支持的模型提供商", code="MODEL_PROVIDER_INVALID")
    model_type = db.session.scalar(
        select(ModelType).where(
            ModelType.code == model_type_code,
            ModelType.status == "ACTIVE",
            (ModelType.created_by.is_(None)) | (ModelType.created_by == g.current_user.id),
        )
    )
    if model_type is None:
        return None, error("不支持的模型类型", code="MODEL_TYPE_INVALID")
    if not name or len(name) > 120:
        return None, error("配置名称不能为空且不能超过 120 个字符", code="MODEL_NAME_INVALID")
    if not model_name or len(model_name) > 200:
        return None, error("模型名称不能为空且不能超过 200 个字符", code="MODEL_ID_INVALID")
    if base_url is None:
        return None, error("API Base URL 必须是有效的 HTTP(S) 地址", code="MODEL_BASE_URL_INVALID")
    if require_api_key and not api_key:
        return None, error("请输入 API Key", code="MODEL_API_KEY_REQUIRED")
    try:
        timeout_seconds = max(10, min(int(payload.get("timeout_seconds") or 120), 600))
        max_output_tokens = max(256, min(int(payload.get("max_output_tokens") or 3072), 16384))
    except (TypeError, ValueError):
        return None, error("超时或输出长度配置无效", code="MODEL_SETTINGS_INVALID")
    validated = {
        "provider_code": provider_code,
        "model_type_code": model_type_code,
        "name": name,
        "base_url": base_url,
        "model_name": model_name,
        "settings": {
            "timeout_seconds": timeout_seconds,
            "max_output_tokens": max_output_tokens,
        },
    }
    if api_key:
        validated["api_key"] = api_key
        validated["key_last_four"] = api_key[-4:]
    return validated, None


def normalize_base_url(value: object) -> str | None:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return None
    return raw


def capabilities_for_type(model_type_code: str) -> list[str]:
    return {
        "chat": ["chat_completions"],
        "embedding": ["embeddings"],
        "rerank": ["rerank"],
    }.get(model_type_code, [model_type_code])


def serialize_provider(item: ModelProvider) -> dict:
    return {
        "code": item.code,
        "name": item.name,
        "default_base_url": item.default_base_url or "",
        "allow_custom_url": item.allow_custom_url,
        "is_custom": item.created_by is not None,
    }


def serialize_model_type(item: ModelType) -> dict:
    return {
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "is_custom": item.created_by is not None,
    }


def serialize_config(item: ModelConfig) -> dict:
    default_for = getattr(item, "default_for", None) or []
    return {
        "id": str(item.id),
        "provider_code": item.provider_code,
        "model_type_code": getattr(item, "model_type_code", "chat"),
        "name": item.name,
        "base_url": item.base_url,
        "model_name": item.model_name,
        "status": item.status,
        "default_for": default_for,
        "is_default": "chat" in default_for,
        "is_account_api": bool(getattr(item, "is_account_api", False)),
        "settings": item.settings or {},
        "has_api_key": bool(item.encrypted_api_key),
        "masked_api_key": f"••••{item.key_last_four}" if item.key_last_four else None,
        "last_verified_at": item.last_verified_at.isoformat() if item.last_verified_at else None,
        "last_error_code": item.last_error_code,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


class ModelVerificationError(RuntimeError):
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def verify_openai_compatible(*, base_url: str, model_name: str, api_key: str | None, timeout: float) -> None:
    body = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0,
            "max_tokens": 64,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    normalized_api_key = str(api_key or "").strip()
    if normalized_api_key and normalized_api_key.upper() != "EMPTY":
        headers["Authorization"] = f"Bearer {normalized_api_key}"
    api_request = url_request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with url_request.urlopen(api_request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except url_error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ModelVerificationError("MODEL_AUTH_FAILED", "API Key 校验失败") from exc
        raise ModelVerificationError("MODEL_HTTP_ERROR", f"模型服务返回 HTTP {exc.code}") from exc
    except (url_error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ModelVerificationError("MODEL_CONNECTION_FAILED", "无法连接模型服务，请检查地址和网络") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelVerificationError("MODEL_RESPONSE_INVALID", "模型服务返回了无效响应") from exc
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelVerificationError("MODEL_RESPONSE_INVALID", "接口不是兼容的 Chat Completions 服务")
    first_message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = first_message.get("content") if isinstance(first_message, dict) else None
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    if not str(content or "").strip():
        raise ModelVerificationError("MODEL_RESPONSE_INVALID", "模型服务未返回可用文本内容")
