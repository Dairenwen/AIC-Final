from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import BAILIAN_DEFAULT_MODEL, BAILIAN_DEFAULT_URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    bailian_base_url: str = Field(default=BAILIAN_DEFAULT_URL, alias="BAILIAN_BASE_URL")
    bailian_model: str = Field(default=BAILIAN_DEFAULT_MODEL, alias="BAILIAN_MODEL")
    bailian_timeout_seconds: float = Field(default=90, alias="BAILIAN_TIMEOUT_SECONDS", gt=0)
    bailian_max_retries: int = Field(default=2, alias="BAILIAN_MAX_RETRIES", ge=0, le=10)
    bailian_allow_offline_fallback: bool = Field(
        default=True, alias="BAILIAN_ALLOW_OFFLINE_FALLBACK"
    )
    output_dir: Path = Field(default=PROJECT_ROOT / "output", alias="OUTPUT_DIR")
    code_execution_timeout_seconds: int = Field(default=60, alias="CODE_EXECUTION_TIMEOUT_SECONDS", ge=5)
    max_input_file_mb: int = Field(default=50, alias="MAX_INPUT_FILE_MB", ge=1)
    mermaid_cli: str = Field(default="npx", alias="MERMAID_CLI")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
