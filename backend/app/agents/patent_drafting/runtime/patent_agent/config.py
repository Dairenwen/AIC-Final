from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from patent_agent.errors import ConfigurationError


ENV_ALIASES = {
    "base_url": ("QWEN_BASE_URL", "CHUNK_QA_MODEL_BASE_URL"),
    "model": ("QWEN_MODEL_NAME", "CHUNK_QA_MODEL_NAME"),
    "api_key": ("QWEN_API_KEY", "CHUNK_QA_MODEL_API_KEY", "DASHSCOPE_API_KEY"),
    "timeout": ("QWEN_TIMEOUT", "CHUNK_QA_MODEL_TIMEOUT"),
    "temperature": ("QWEN_TEMPERATURE", "CHUNK_QA_MODEL_TEMPERATURE"),
    "response_format": ("QWEN_RESPONSE_FORMAT", "CHUNK_QA_MODEL_RESPONSE_FORMAT"),
    "max_tokens": ("QWEN_MAX_TOKENS",),
    "retries": ("QWEN_RETRIES",),
}
WORKFLOW_MODES = {"strict", "flow_first"}


@dataclass(frozen=True)
class QwenConfig:
    base_url: str
    model: str
    api_key: str
    api_key_env: str
    timeout_seconds: float = 90.0
    retries: int = 2
    temperature: float = 0.2
    max_tokens: int = 8192
    response_format: str = "json_object"


@dataclass(frozen=True)
class CnipaConfig:
    tool: Path
    timeout_seconds: float
    max_queries: int
    allow_fixture_fallback: bool
    fixture: Path


@dataclass(frozen=True)
class AppConfig:
    root: Path
    qwen: QwenConfig
    cnipa: CnipaConfig
    runs_dir: Path
    outputs_dir: Path
    log_level: str
    fake_mode: bool
    env_file: Path | None
    recognized_qwen_vars: tuple[str, ...]
    min_effective_characters: int
    docx_required: bool
    docx_font: str
    workflow_mode: str


def _first(env: dict[str, str], names: tuple[str, ...], default: str = "") -> tuple[str, str]:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value, name
    return default, ""


def _expand(value: Any, env: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        return env.get(value[2:-1], "")
    return value


def load_config(
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
    fake_mode: bool | None = None,
    require_model: bool = True,
    workflow_mode: str | None = None,
) -> AppConfig:
    root = Path.cwd().resolve()
    selected_env = Path(env_file).expanduser().resolve() if env_file else None
    if selected_env is None and os.environ.get("PATENT_AGENT_ENV_FILE"):
        selected_env = Path(os.environ["PATENT_AGENT_ENV_FILE"]).expanduser().resolve()
    if selected_env is None and (root / ".env").is_file():
        selected_env = root / ".env"
    file_env: dict[str, str] = {}
    if selected_env:
        if not selected_env.is_file():
            raise ConfigurationError(f"env file not found: {selected_env}")
        file_env = {k: str(v) for k, v in dotenv_values(selected_env).items() if v is not None}
    env = {**file_env, **os.environ}

    raw: dict[str, Any] = {}
    selected_config = Path(config_path).expanduser().resolve() if config_path else None
    if selected_config is None and env.get("PATENT_AGENT_CONFIG"):
        selected_config = Path(env["PATENT_AGENT_CONFIG"]).expanduser().resolve()
    if selected_config is None and (root / "config.yaml").is_file():
        selected_config = root / "config.yaml"
    if selected_config:
        if not selected_config.is_file():
            raise ConfigurationError(f"config file not found: {selected_config}")
        loaded = yaml.safe_load(selected_config.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigurationError("config root must be a mapping")
        raw = loaded

    qraw = raw.get("qwen", {}) or {}
    base_default = str(_expand(qraw.get("base_url", ""), env) or "")
    model_default = str(_expand(qraw.get("model", ""), env) or "")
    base_url, base_var = _first(env, ENV_ALIASES["base_url"], base_default)
    model, model_var = _first(env, ENV_ALIASES["model"], model_default)
    configured_key_env = str(qraw.get("api_key_env", "QWEN_API_KEY"))
    key_names = (configured_key_env,) + tuple(n for n in ENV_ALIASES["api_key"] if n != configured_key_env)
    api_key, key_var = _first(env, key_names)
    timeout_s, timeout_var = _first(env, ENV_ALIASES["timeout"], str(qraw.get("timeout_seconds", 90)))
    temp_s, temp_var = _first(env, ENV_ALIASES["temperature"], str(qraw.get("temperature", 0.2)))
    max_s, max_var = _first(env, ENV_ALIASES["max_tokens"], str(qraw.get("max_tokens", 8192)))
    retries_s, retries_var = _first(env, ENV_ALIASES["retries"], str(qraw.get("retries", 2)))
    format_s, format_var = _first(env, ENV_ALIASES["response_format"], str(qraw.get("response_format", "json_object")))
    if format_s != "json_object":
        raise ConfigurationError("Qwen response_format must be json_object in baseline v0.1")
    recognized = tuple(
        x for x in (base_var, model_var, key_var, timeout_var, temp_var, max_var, retries_var, format_var) if x
    )

    craw = raw.get("cnipa", {}) or {}
    workflow_raw = raw.get("workflow", {}) or {}
    parser_raw = raw.get("parser", {}) or {}
    export_raw = raw.get("export", {}) or {}
    praw = raw.get("paths", {}) or {}
    fake = bool(fake_mode) if fake_mode is not None else str(env.get("PATENT_AGENT_FAKE_MODE", "")).lower() in {"1", "true", "yes"}
    selected_workflow_mode = str(
        workflow_mode
        or env.get("PATENT_AGENT_WORKFLOW_MODE")
        or workflow_raw.get("mode", "flow_first")
    ).strip()
    if selected_workflow_mode not in WORKFLOW_MODES:
        raise ConfigurationError(
            "workflow mode must be one of: " + ", ".join(sorted(WORKFLOW_MODES))
        )
    if not fake and require_model:
        missing = [name for name, value in (("Qwen base URL", base_url), ("Qwen model", model), ("Qwen API key", api_key)) if not value]
        if missing:
            raise ConfigurationError("missing configuration: " + ", ".join(missing))
    qwen = QwenConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_env=key_var or configured_key_env,
        timeout_seconds=float(timeout_s),
        retries=int(retries_s),
        temperature=float(temp_s),
        max_tokens=int(max_s),
        response_format=format_s,
    )

    def root_path(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else root / p

    cnipa = CnipaConfig(
        tool=root_path(str(craw.get("tool", "vendor/patent-disclosure-skill/tools/cnipa_epub_search.py"))),
        timeout_seconds=float(craw.get("timeout_seconds", 240)),
        max_queries=int(craw.get("max_queries", 8)),
        allow_fixture_fallback=bool(craw.get("allow_fixture_fallback", False)),
        fixture=root_path(str(craw.get("fixture", "patent_agent/fixtures/cnipa_hits.json"))),
    )
    runs = root_path(str(env.get("PATENT_AGENT_RUNS_DIR") or praw.get("runs_dir", "runs")))
    outputs = root_path(str(env.get("PATENT_AGENT_OUTPUTS_DIR") or praw.get("outputs_dir", "outputs")))
    return AppConfig(
        root=root,
        qwen=qwen,
        cnipa=cnipa,
        runs_dir=runs,
        outputs_dir=outputs,
        log_level=str(env.get("PATENT_AGENT_LOG_LEVEL") or (raw.get("logging", {}) or {}).get("level", "INFO")),
        fake_mode=fake,
        env_file=selected_env,
        recognized_qwen_vars=recognized,
        min_effective_characters=int(parser_raw.get("min_effective_characters", 50)),
        docx_required=bool(export_raw.get("docx_required", False)),
        docx_font=str(env.get("PATENT_AGENT_DOCX_FONT") or export_raw.get("docx_font", "Noto Sans CJK SC")),
        workflow_mode=selected_workflow_mode,
    )
