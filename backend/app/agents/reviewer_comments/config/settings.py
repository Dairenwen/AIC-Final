"""运行时配置（pydantic-settings）。

行为对齐 backend/app/config.py：
- 正数校验（超时 / 并发 workers）
- LLM_EXTRA_BODY JSON 对象解析
- sqlalchemy_url / libpq_url 连接串归一化
- require_database / require_llm / model_for / validate
- 论文本地存储路径

密钥与连接串一律从环境变量读取，不在代码中硬编码。
"""

from __future__ import annotations

import json
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any, Self

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from langgraph_agent.utils.exceptions import ConfigError

# 默认论文存储目录：langgraph-agent/.storage/manuscripts
_DEFAULT_MANUSCRIPT_DIR = str(
    Path(__file__).resolve().parent.parent / ".storage" / "manuscripts"
)


class Settings(BaseSettings):
    """集中管理包运行时配置。

    字段全部来自环境变量；模型名提供合理默认值，密钥与连接串必须显式提供。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- OpenAI-compatible Chat Completions 端点 ----
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    # 论文卡片结构化调用使用独立超时，不影响公共 LLM 路径。
    PAPER_CARD_LLM_TIMEOUT_SECONDS: float = 240.0
    # split / analyze / FAST Reply 共用超时。
    # 自建慢模型（含 thinking）建议在 .env 中显式设为 180~300。
    LLM_TIMEOUT_SECONDS: float = 30.0
    # 多条原文条目拆分时的并发 LLM 调用上限；1 表示串行。
    SPLIT_MAX_WORKERS: int = 3
    # 论文卡片子批次并发 LLM 上限；默认 5，1 表示串行。
    PAPER_CARD_MAX_WORKERS: int = 5
    # 批量分析建议时后台并行 run 上限；默认 8，1 表示串行。
    ANALYSIS_MAX_WORKERS: int = 8
    # 批量生成回复时后台并行 run 上限；默认 8，1 表示串行。
    REPLY_MAX_WORKERS: int = 8
    # 透传给 Chat Completions 的 extra_body（JSON 对象字符串）。
    # 例：关闭 Qwen 思考模式 → {"chat_template_kwargs":{"enable_thinking":false}}
    LLM_EXTRA_BODY: dict[str, Any] = Field(default_factory=dict)

    # ---- 产品运行时模型分工（可配置，切换只改环境变量）----
    # 拆意见 / 分类：要求结构化输出稳定
    MODEL_SPLIT: str = "gpt-5.6-sol"
    # 分析意见：先用 grok-4.5 占位，后续替换为正式分析模型
    MODEL_ANALYZE: str = "grok-4.5"
    # 论文语义卡片：未配置时回退到分析模型，但两者可独立覆盖。
    MODEL_PAPER_CARD: str = ""
    # 回复草稿：先用 grok-4.5 占位
    MODEL_DRAFT: str = "grok-4.5"

    # ---- 数据库（业务表、Checkpointer、Store 共用同一 PostgreSQL 实例）----
    # 原始连接串前缀可为 postgresql:// / postgresql+psycopg:// / postgres://，
    # sqlalchemy_url / libpq_url 会分别归一化为各自组件所需格式。
    DATABASE_URL: str = ""

    # ---- 论文本地存储（慢速模式）----
    # 默认指向 langgraph-agent/.storage/manuscripts；只返回路径，不创建目录。
    MANUSCRIPT_STORAGE_DIR: str = _DEFAULT_MANUSCRIPT_DIR
    MANUSCRIPT_MAX_BYTES: int = 20 * 1024 * 1024
    MANUSCRIPT_ALLOWED_SUFFIXES: tuple[str, ...] = (".pdf",)

    # ------------------------------------------------------------------
    # 字段校验（对齐 backend Config 的 _positive_* / _optional_json_object_env）
    # ------------------------------------------------------------------

    @field_validator("PAPER_CARD_LLM_TIMEOUT_SECONDS", mode="before")
    @classmethod
    def _positive_float_timeout(cls, value: Any) -> float:
        name = "PAPER_CARD_LLM_TIMEOUT_SECONDS"
        try:
            if isinstance(value, bool):
                raise TypeError
            parsed = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} 必须是大于 0 的数字，当前值：{value!r}") from error
        if not isfinite(parsed) or parsed <= 0:
            raise ValueError(f"{name} 必须是大于 0 的数字，当前值：{value!r}")
        return parsed

    @field_validator(
        "SPLIT_MAX_WORKERS",
        "PAPER_CARD_MAX_WORKERS",
        "ANALYSIS_MAX_WORKERS",
        "REPLY_MAX_WORKERS",
        mode="before",
    )
    @classmethod
    def _positive_int_workers(cls, value: Any, info: Any) -> int:
        name = str(info.field_name)
        try:
            if isinstance(value, bool):
                raise TypeError
            parsed = int(str(value).strip())
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} 必须是 >= 1 的整数，当前值：{value!r}") from error
        if parsed < 1:
            raise ValueError(f"{name} 必须是 >= 1 的整数，当前值：{value!r}")
        return parsed

    @field_validator("LLM_EXTRA_BODY", mode="before")
    @classmethod
    def _parse_extra_body(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"LLM_EXTRA_BODY 必须是合法 JSON 对象，当前值：{raw!r}"
                ) from error
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"LLM_EXTRA_BODY 必须是 JSON 对象，当前值类型：{type(parsed).__name__}"
                )
            return parsed
        raise ValueError(
            f"LLM_EXTRA_BODY 必须是 JSON 对象，当前值类型：{type(value).__name__}"
        )

    @model_validator(mode="after")
    def _default_paper_card_model(self) -> Self:
        """未配置 MODEL_PAPER_CARD 时回退到 MODEL_ANALYZE（对齐 backend）。"""
        if not (self.MODEL_PAPER_CARD or "").strip():
            self.MODEL_PAPER_CARD = self.MODEL_ANALYZE
        return self

    # ------------------------------------------------------------------
    # 派生方法（对齐 backend Config 类方法）
    # ------------------------------------------------------------------

    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 所需连接串：强制 postgresql+psycopg:// 前缀（psycopg 3）。"""
        self.require_database()
        raw = self.DATABASE_URL
        if raw.startswith("postgresql+psycopg://"):
            return raw
        if raw.startswith("postgresql://"):
            return raw.replace("postgresql://", "postgresql+psycopg://", 1)
        if raw.startswith("postgres://"):
            return raw.replace("postgres://", "postgresql+psycopg://", 1)
        return raw

    def libpq_url(self) -> str:
        """LangGraph（PostgresSaver/PostgresStore）所需连接串：原生 libpq 格式。"""
        self.require_database()
        raw = self.DATABASE_URL
        if raw.startswith("postgresql+psycopg://"):
            return raw.replace("postgresql+psycopg://", "postgresql://", 1)
        return raw

    def llm_extra_body(self) -> dict[str, Any]:
        """返回 Chat Completions 可选 extra_body；无配置时为空字典。"""
        value = self.LLM_EXTRA_BODY
        return dict(value) if isinstance(value, dict) else {}

    def require_llm(self) -> None:
        """校验 LLM 调用所需配置，缺失时给出清晰的中文报错。"""
        missing: list[str] = []
        if not self.LLM_BASE_URL:
            missing.append("LLM_BASE_URL（中转站地址）")
        if not self.LLM_API_KEY:
            missing.append("LLM_API_KEY（中转站密钥）")
        if missing:
            raise ConfigError(
                "缺少 LLM 调用所需的环境变量："
                + "、".join(missing)
                + "。请在 .env 中配置后重试。"
            )

    def require_database(self) -> None:
        """校验数据库连接配置，缺失时给出清晰的中文报错。"""
        if not self.DATABASE_URL:
            raise ConfigError(
                "缺少数据库连接串环境变量 DATABASE_URL。"
                "请在 .env 中配置为 PostgreSQL 连接串"
                "（形如 postgresql://user:pass@host:5432/dbname）后重试。"
            )

    def model_for(self, purpose: str) -> str:
        """按用途返回配置的模型名。

        purpose ∈ {"split", "analyze", "draft", "paper_card"}。
        """
        mapping = {
            "split": self.MODEL_SPLIT,
            "analyze": self.MODEL_ANALYZE,
            "draft": self.MODEL_DRAFT,
            "paper_card": self.MODEL_PAPER_CARD,
        }
        if purpose not in mapping:
            raise ConfigError(
                f"未知的模型用途 '{purpose}'，仅支持："
                "split、analyze、draft、paper_card。"
            )
        return mapping[purpose]

    def validate_runtime(self) -> None:
        """在应用启动时验证需要有明确语义的运行时配置。

        对应 backend ``Config.validate()``；方法名避开 pydantic ``BaseModel.validate``。
        """
        value = self.PAPER_CARD_LLM_TIMEOUT_SECONDS
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
        ):
            raise ConfigError(
                "PAPER_CARD_LLM_TIMEOUT_SECONDS 必须是大于 0 的数字，"
                f"当前值：{value!r}"
            )

    def manuscript_storage_path(self) -> Path:
        """返回论文存储目录 Path；只返回路径，不创建目录。"""
        return Path(self.MANUSCRIPT_STORAGE_DIR)


def clear_settings_cache() -> None:
    """清空 get_settings 进程内缓存（测试或热更新配置时使用）。"""
    get_settings.cache_clear()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程内缓存的 Settings 单例。

    构造失败时将 pydantic ValidationError 转为 ConfigError，消息保留中文说明。
    """
    try:
        return Settings()
    except ValidationError as error:
        parts: list[str] = []
        for item in error.errors():
            msg = str(item.get("msg", ""))
            # pydantic 会给自定义 ValueError 加上 "Value error, " 前缀
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
            loc = ".".join(str(x) for x in item.get("loc", ()) if x != "body")
            parts.append(f"{loc}: {msg}" if loc and msg else (msg or str(item)))
        raise ConfigError("；".join(parts) or str(error)) from error
