from .api import OpenAICompatibleAcademicLLM
from .factory import build_academic_llm
from .ollama import OllamaAcademicLLM, TextGenerator

__all__ = [
    "OpenAICompatibleAcademicLLM",
    "OllamaAcademicLLM",
    "TextGenerator",
    "build_academic_llm",
]
