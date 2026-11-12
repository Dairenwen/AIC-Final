from __future__ import annotations

from academic_translation.llm.api import OpenAICompatibleAcademicLLM
from academic_translation.llm.ollama import OllamaAcademicLLM, TextGenerator
from academic_translation.settings import settings


def build_academic_llm() -> TextGenerator:
    backend = settings.translation_llm_backend.strip().lower()
    if backend in {"api", "openai", "openai-compatible"}:
        return OpenAICompatibleAcademicLLM()
    if backend == "ollama":
        return OllamaAcademicLLM(settings.ollama_translation_model)
    raise ValueError(
        "Unsupported TRANSLATION_LLM_BACKEND. Use api or ollama."
    )

