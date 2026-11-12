from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_translation_model: str = "translategemma:12b"
    ollama_temperature: float = 0
    ollama_num_ctx: int = 8192
    translation_llm_backend: str = "api"
    translation_api_base_url: str = ""
    translation_api_key: str = ""
    translation_api_model: str = ""
    translation_api_timeout_seconds: float = 240
    translation_api_max_tokens: int = 2048
    translation_api_retries: int = 2
    translation_api_retry_backoff_seconds: float = 2
    translation_api_disable_thinking: bool = True
    translation_api_max_parallel: int = 1
    max_parallel_segments: int = 1
    pdf2zh_command: str = ""
    default_output_dir: str = "outputs"


settings = Settings()
