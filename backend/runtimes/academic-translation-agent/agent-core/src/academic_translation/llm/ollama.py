from __future__ import annotations

from typing import Protocol

from langchain_ollama import ChatOllama

from academic_translation.settings import settings


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> str: ...


class OllamaAcademicLLM:
    """The only model adapter used by this project."""

    def __init__(self, model: str | None = None) -> None:
        self.client = ChatOllama(
            model=model or settings.ollama_translation_model,
            base_url=settings.ollama_base_url,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
        )

    def generate(self, prompt: str) -> str:
        response = self.client.invoke(prompt)
        content = response.content
        return content if isinstance(content, str) else str(content)
