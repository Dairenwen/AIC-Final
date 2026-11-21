from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_MARKERS = ("请填写", "your_", "xxx", "YOUR_")


def load_local_env(env_path: Optional[Path] = None) -> None:
    env_path = env_path or (PACKAGE_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def compact_text(value: str, limit: int = 4000) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


class OpenAICompatibleLLMClient:
    """Tiny OpenAI-compatible chat client using only the Python standard library."""

    def __init__(self) -> None:
        load_local_env()
        self.enabled = os.getenv("COMPLIANCE_AGENT_USE_LLM", "true").lower() in {"1", "true", "yes", "on"}
        self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
        self.base_url = (os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "").rstrip("/")
        self.model = os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "qwen-plus"
        self.temperature = float(os.getenv("COMPLIANCE_AGENT_LLM_TEMPERATURE", "0.1"))
        self.timeout = int(os.getenv("COMPLIANCE_AGENT_LLM_TIMEOUT", "60"))
        self.last_error = ""

    def is_available(self) -> bool:
        if not self.enabled or not self.api_key or not self.base_url:
            return False
        return not any(marker in self.api_key for marker in PLACEHOLDER_MARKERS)

    def chat_text(self, system_prompt: str, user_payload: Dict[str, Any]) -> str:
        if not self.is_available():
            return ""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": self.temperature,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return ""
        choices = body.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    def chat_json(self, system_prompt: str, user_payload: Dict[str, Any]) -> Dict[str, Any]:
        return extract_json_object(self.chat_text(system_prompt, user_payload))


def llm_document_view(parsed_document: Dict[str, Any]) -> Dict[str, Any]:
    sections = []
    for section in parsed_document.get("sections", [])[:8]:
        sections.append(
            {
                "title": section.get("title", ""),
                "content": compact_text(section.get("content", ""), 900),
            }
        )
    return {
        "title": parsed_document.get("title", ""),
        "abstract": compact_text(parsed_document.get("abstract", ""), 1200),
        "keywords": parsed_document.get("keywords", []),
        "sections": sections,
        "figures": parsed_document.get("figures", [])[:20],
        "tables": parsed_document.get("tables", [])[:20],
        "references": parsed_document.get("references", [])[:30],
        "citations": parsed_document.get("citations", [])[:50],
        "statements": parsed_document.get("statements", {}),
    }

