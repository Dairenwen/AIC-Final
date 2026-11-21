"""A1 配置与通用工具验收测试。"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from config.constants import (
    MODEL_PURPOSE_ANALYZE,
    MODEL_PURPOSES,
    PHASE_EXTRACT_PARTIES_AND_ITEMS,
    RUN_SCOPE_FINALIZE,
    RUN_SCOPE_TASK_INIT,
    STATUS_PENDING,
)
from config.settings import Settings, clear_settings_cache, get_settings
from langgraph_agent.utils import (
    AgentError,
    ConfigError,
    ErrorCode,
    PortError,
    get_logger,
    stable_hash,
    stable_json,
)


def _settings(**env: str) -> Settings:
    """构造隔离于 .env 文件的 Settings（仅依赖传入环境变量）。"""
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Settings 导入与默认值
# ---------------------------------------------------------------------------


def test_settings_importable() -> None:
    assert Settings is not None
    s = _settings()
    assert s.MODEL_ANALYZE == "grok-4.5"
    assert s.MODEL_SPLIT == "gpt-5.6-sol"
    assert s.MODEL_DRAFT == "grok-4.5"
    assert s.LLM_TIMEOUT_SECONDS == 30.0
    assert s.PAPER_CARD_LLM_TIMEOUT_SECONDS == 240.0
    assert s.SPLIT_MAX_WORKERS == 3
    assert s.PAPER_CARD_MAX_WORKERS == 5
    assert s.ANALYSIS_MAX_WORKERS == 8
    assert s.REPLY_MAX_WORKERS == 8
    assert s.LLM_EXTRA_BODY == {}
    assert s.DATABASE_URL == ""


def test_model_paper_card_falls_back_to_analyze() -> None:
    s = _settings(MODEL_ANALYZE="custom-analyze")
    assert s.MODEL_PAPER_CARD == "custom-analyze"


def test_model_paper_card_explicit_override() -> None:
    s = _settings(MODEL_ANALYZE="analyze-x", MODEL_PAPER_CARD="paper-y")
    assert s.MODEL_PAPER_CARD == "paper-y"


# ---------------------------------------------------------------------------
# DATABASE_URL 归一化
# ---------------------------------------------------------------------------


def test_sqlalchemy_url_postgresql_prefix() -> None:
    s = _settings(DATABASE_URL="postgresql://u:p@localhost:5432/db")
    assert s.sqlalchemy_url() == "postgresql+psycopg://u:p@localhost:5432/db"


def test_sqlalchemy_url_already_psycopg() -> None:
    raw = "postgresql+psycopg://u:p@localhost:5432/db"
    s = _settings(DATABASE_URL=raw)
    assert s.sqlalchemy_url() == raw


def test_sqlalchemy_url_postgres_scheme() -> None:
    s = _settings(DATABASE_URL="postgres://u:p@localhost:5432/db")
    assert s.sqlalchemy_url() == "postgresql+psycopg://u:p@localhost:5432/db"


def test_libpq_url_strips_psycopg() -> None:
    s = _settings(DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db")
    assert s.libpq_url() == "postgresql://u:p@localhost:5432/db"


def test_libpq_url_keeps_postgresql() -> None:
    raw = "postgresql://u:p@localhost:5432/db"
    s = _settings(DATABASE_URL=raw)
    assert s.libpq_url() == raw


def test_sqlalchemy_url_unknown_scheme_passthrough() -> None:
    raw = "sqlite:///tmp.db"
    s = _settings(DATABASE_URL=raw)
    assert s.sqlalchemy_url() == raw


def test_require_database_missing() -> None:
    s = _settings()
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        s.require_database()
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        s.sqlalchemy_url()
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        s.libpq_url()


def test_require_llm_missing_both() -> None:
    s = _settings()
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        s.require_llm()


def test_require_llm_missing_key_only() -> None:
    s = _settings(LLM_BASE_URL="https://relay.example/v1")
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        s.require_llm()


def test_require_llm_ok() -> None:
    s = _settings(
        LLM_BASE_URL="https://relay.example/v1",
        LLM_API_KEY="sk-test",
    )
    s.require_llm()  # 不抛


# ---------------------------------------------------------------------------
# 正数校验
# ---------------------------------------------------------------------------


def test_positive_float_timeout_rejects_zero() -> None:
    with pytest.raises(ValidationError, match="大于 0"):
        _settings(PAPER_CARD_LLM_TIMEOUT_SECONDS="0")


def test_positive_float_timeout_rejects_negative() -> None:
    with pytest.raises(ValidationError, match="大于 0"):
        _settings(PAPER_CARD_LLM_TIMEOUT_SECONDS="-1")


def test_positive_float_timeout_rejects_nan_like() -> None:
    with pytest.raises(ValidationError, match="大于 0"):
        _settings(PAPER_CARD_LLM_TIMEOUT_SECONDS="not-a-number")


def test_positive_int_workers_rejects_zero() -> None:
    with pytest.raises(ValidationError, match=">= 1"):
        _settings(SPLIT_MAX_WORKERS="0")


def test_positive_int_workers_rejects_negative() -> None:
    with pytest.raises(ValidationError, match=">= 1"):
        _settings(ANALYSIS_MAX_WORKERS="-3")


def test_positive_int_workers_accepts_one() -> None:
    s = _settings(REPLY_MAX_WORKERS="1")
    assert s.REPLY_MAX_WORKERS == 1


# ---------------------------------------------------------------------------
# LLM_EXTRA_BODY
# ---------------------------------------------------------------------------


def test_extra_body_empty_string() -> None:
    s = _settings(LLM_EXTRA_BODY="")
    assert s.LLM_EXTRA_BODY == {}
    assert s.llm_extra_body() == {}


def test_extra_body_valid_json() -> None:
    raw = '{"chat_template_kwargs":{"enable_thinking":false}}'
    s = _settings(LLM_EXTRA_BODY=raw)
    assert s.LLM_EXTRA_BODY == {"chat_template_kwargs": {"enable_thinking": False}}
    body = s.llm_extra_body()
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    # 返回副本，修改不影响内部
    body["x"] = 1
    assert "x" not in s.LLM_EXTRA_BODY


def test_extra_body_invalid_json() -> None:
    with pytest.raises(ValidationError, match="合法 JSON"):
        _settings(LLM_EXTRA_BODY="{not-json")


def test_extra_body_json_array_rejected() -> None:
    with pytest.raises(ValidationError, match="JSON 对象"):
        _settings(LLM_EXTRA_BODY="[1,2,3]")


# ---------------------------------------------------------------------------
# model_for / validate / manuscript
# ---------------------------------------------------------------------------


def test_model_for_known_purposes() -> None:
    s = _settings(
        MODEL_SPLIT="m-split",
        MODEL_ANALYZE="m-analyze",
        MODEL_DRAFT="m-draft",
        MODEL_PAPER_CARD="m-card",
    )
    assert s.model_for("split") == "m-split"
    assert s.model_for("analyze") == "m-analyze"
    assert s.model_for("draft") == "m-draft"
    assert s.model_for("paper_card") == "m-card"


def test_model_for_unknown_purpose() -> None:
    s = _settings()
    with pytest.raises(ConfigError, match="未知的模型用途"):
        s.model_for("unknown")


def test_validate_runtime_ok() -> None:
    s = _settings()
    s.validate_runtime()  # 对应 backend Config.validate()


def test_manuscript_storage_path() -> None:
    s = _settings(MANUSCRIPT_STORAGE_DIR="/tmp/ms")
    assert str(s.manuscript_storage_path()) in {"/tmp/ms", "\\tmp\\ms"}
    # 默认目录指向包内 .storage/manuscripts
    default = _settings()
    assert default.manuscript_storage_path().name == "manuscripts"
    assert default.MANUSCRIPT_MAX_BYTES == 20 * 1024 * 1024
    assert default.MANUSCRIPT_ALLOWED_SUFFIXES == (".pdf",)


# ---------------------------------------------------------------------------
# get_settings 缓存
# ---------------------------------------------------------------------------


def test_get_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    monkeypatch.setenv("DATABASE_URL", "postgresql://cache-test/db")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    a = get_settings()
    b = get_settings()
    assert a is b
    clear_settings_cache()
    c = get_settings()
    assert c is not a
    clear_settings_cache()


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_constants_run_scope_and_phase() -> None:
    assert RUN_SCOPE_TASK_INIT == "TASK_INIT"
    assert RUN_SCOPE_FINALIZE == "FINALIZE"
    assert STATUS_PENDING == "PENDING"
    assert PHASE_EXTRACT_PARTIES_AND_ITEMS == "EXTRACT_PARTIES_AND_ITEMS"
    assert MODEL_PURPOSE_ANALYZE in MODEL_PURPOSES


# ---------------------------------------------------------------------------
# utils: hashing / logging / exceptions
# ---------------------------------------------------------------------------


def test_stable_hash_key_order_independent() -> None:
    a = stable_hash({"b": 2, "a": 1})
    b = stable_hash({"a": 1, "b": 2})
    assert a == b
    expected = hashlib.sha256(
        json.dumps(
            {"a": 1, "b": 2},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert a == expected


def test_stable_json_unicode() -> None:
    text = stable_json({"意见": "修改方法"})
    assert "意见" in text
    assert stable_hash({"意见": "修改方法"}) == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def test_get_logger_names() -> None:
    root = get_logger()
    assert root.name == "langgraph_agent"
    child = get_logger("utils.hashing")
    assert child.name == "langgraph_agent.utils.hashing"
    already = get_logger("langgraph_agent.agent")
    assert already.name == "langgraph_agent.agent"
    config_logger = get_logger("config.settings")
    assert config_logger.name == "config.settings"


def test_exception_hierarchy() -> None:
    err = ConfigError("配置坏了")
    assert isinstance(err, RuntimeError)
    assert str(err) == "配置坏了"

    port = PortError("db down", {"host": "localhost"})
    assert port.code == ErrorCode.PORT_ERROR
    assert port.http_status == 500
    assert port.details["host"] == "localhost"

    agent = AgentError("图失败")
    assert agent.code == ErrorCode.AGENT_ERROR
    assert isinstance(agent, Exception)
