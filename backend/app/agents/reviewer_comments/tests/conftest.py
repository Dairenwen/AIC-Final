"""pytest 共享夹具与 integration 判定。"""

from __future__ import annotations

import os

import pytest

# 真实数据库连通：业务表 / checkpointer 共用 DATABASE_URL
HAS_DATABASE_URL = bool(os.getenv("DATABASE_URL", "").strip())

# 真实 LLM 调用：需同时具备端点与密钥
HAS_LLM_CREDENTIALS = bool(
    os.getenv("LLM_BASE_URL", "").strip() and os.getenv("LLM_API_KEY", "").strip()
)

requires_database = pytest.mark.skipif(
    not HAS_DATABASE_URL,
    reason="未配置 DATABASE_URL，跳过数据库集成测试",
)

requires_llm = pytest.mark.skipif(
    not HAS_LLM_CREDENTIALS,
    reason="未配置 LLM_BASE_URL / LLM_API_KEY，跳过 LLM 集成测试",
)
