from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    ARXIV_DEFAULT_URL,
    ARXIV_REQUEST_INTERVAL_SECONDS,
    BAILIAN_DEFAULT_MODEL,
    BAILIAN_DEFAULT_URL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_SEARCH_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    KNOWLEDGE_API_DEFAULT_URL,
    SERPAPI_DEFAULT_URL,
)


AGENT_CORE_DIR = Path(__file__).resolve().parents[1]
BACKEND_ENV_FILE = AGENT_CORE_DIR.parent / "agent-system" / "backend" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(AGENT_CORE_DIR / ".env", BACKEND_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    arxiv_api_url: str = Field(default=ARXIV_DEFAULT_URL, alias="ARXIV_API_URL")
    arxiv_timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, alias="ARXIV_TIMEOUT_SECONDS", gt=0)
    arxiv_request_interval_seconds: float = Field(
        default=ARXIV_REQUEST_INTERVAL_SECONDS,
        alias="ARXIV_REQUEST_INTERVAL_SECONDS",
        ge=0,
    )
    arxiv_max_retries: int = Field(default=DEFAULT_SEARCH_RETRIES, alias="ARXIV_MAX_RETRIES", ge=0, le=5)
    serpapi_api_key: str | None = Field(default=None, alias="SERPAPI_API_KEY")
    serpapi_url: str = Field(default=SERPAPI_DEFAULT_URL, alias="SERPAPI_URL")
    serpapi_timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, alias="SERPAPI_TIMEOUT_SECONDS", gt=0)
    knowledge_api_base_url: str = Field(default=KNOWLEDGE_API_DEFAULT_URL, alias="KNOWLEDGE_API_BASE_URL")
    knowledge_api_timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        alias="KNOWLEDGE_API_TIMEOUT_SECONDS",
        gt=0,
    )
    knowledge_api_user_id: str = Field(default="agent-user", alias="KNOWLEDGE_API_USER_ID")
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    bailian_base_url: str = Field(default=BAILIAN_DEFAULT_URL, alias="BAILIAN_BASE_URL")
    bailian_model: str = Field(default=BAILIAN_DEFAULT_MODEL, alias="BAILIAN_MODEL")
    bailian_timeout_seconds: float = Field(
        default=DEFAULT_LLM_TIMEOUT_SECONDS,
        alias="BAILIAN_TIMEOUT_SECONDS",
        gt=0,
    )
    bailian_max_retries: int = Field(default=2, alias="BAILIAN_MAX_RETRIES", ge=0, le=10)
    postgres_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URI"),
        serialization_alias="POSTGRES_URI",
    )
    postgres_test_uri: str | None = Field(default=None, alias="POSTGRES_TEST_URI")
    langgraph_aes_key: str | None = Field(default=None, alias="LANGGRAPH_AES_KEY")
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
