from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from flask import current_app


KEY_VERSION = "aesgcm-v1"


def _encryption_key() -> bytes:
    configured = str(current_app.config.get("MODEL_CONFIG_ENCRYPTION_KEY") or "").strip()
    if configured:
        try:
            key = base64.urlsafe_b64decode(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("MODEL_CONFIG_ENCRYPTION_KEY 必须是 URL-safe Base64") from exc
        if len(key) != 32:
            raise RuntimeError("MODEL_CONFIG_ENCRYPTION_KEY 解码后必须为 32 字节")
        return key

    # Development fallback keeps local setup usable. Deployments should always set a dedicated key.
    return hashlib.sha256(
        f"zhiyan-model-config:{current_app.config['SECRET_KEY']}".encode("utf-8")
    ).digest()


def encrypt_api_key(api_key: str) -> tuple[bytes, bytes, str]:
    nonce = os.urandom(12)
    encrypted = AESGCM(_encryption_key()).encrypt(nonce, api_key.encode("utf-8"), KEY_VERSION.encode())
    return encrypted, nonce, KEY_VERSION


def decrypt_api_key(encrypted: bytes, nonce: bytes, key_version: str) -> str:
    if key_version != KEY_VERSION:
        raise RuntimeError("不支持的模型密钥版本")
    try:
        plaintext = AESGCM(_encryption_key()).decrypt(nonce, encrypted, key_version.encode())
    except InvalidTag as exc:
        raise RuntimeError("模型密钥无法使用当前加密主密钥解密") from exc
    return plaintext.decode("utf-8")
