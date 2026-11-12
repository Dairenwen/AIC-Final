from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from patent_agent.config import QwenConfig
from patent_agent.errors import ModelError


@dataclass(frozen=True)
class ModelResult:
    data: dict[str, Any]
    model: str
    request_id: str | None


class QwenAdapter:
    """Minimal OpenAI-compatible chat-completions adapter.

    It never logs prompts, response bodies, authorization headers, or API keys.
    """

    def __init__(self, config: QwenConfig):
        self.config = config

    @property
    def endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelResult:
        if not self.config.api_key:
            raise ModelError(f"API key variable {self.config.api_key_env} is empty")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
            "response_format": {"type": self.config.response_format},
            # Every model call in this adapter requires a strict JSON object.
            # Qwen hybrid models default to thinking mode, which is slower and
            # is not the production JSON-mode contract used by this workflow.
            "enable_thinking": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                    content = raw["choices"][0]["message"]["content"]
                    if isinstance(content, dict):
                        parsed = content
                    else:
                        parsed = json.loads(str(content).strip())
                    if not isinstance(parsed, dict):
                        raise ValueError("structured response was not a JSON object")
                    request_id = response.headers.get("x-request-id") or raw.get("request_id") or raw.get("id")
                    return ModelResult(parsed, str(raw.get("model") or self.config.model), request_id)
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.config.retries:
                    raise ModelError(f"Qwen HTTP error status={exc.code}; response body suppressed") from None
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt >= self.config.retries:
                    raise ModelError(f"Qwen transport error: {type(exc).__name__}") from None
            except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
                raise ModelError(f"Qwen returned invalid structured output: {type(exc).__name__}") from None
            time.sleep(min(2**attempt, 4))
        raise ModelError(f"Qwen request failed: {type(last_error).__name__ if last_error else 'unknown'}")

    def smoke_test(self) -> dict[str, Any]:
        result = self.complete_json(
            system_prompt="Return only a JSON object. Do not include markdown.",
            user_prompt='Return exactly this semantic object: {"status":"ok","purpose":"patent_agent_smoke"}.',
            temperature=0,
            max_tokens=128,
        )
        if result.data.get("status") != "ok" or result.data.get("purpose") != "patent_agent_smoke":
            raise ModelError("Qwen smoke response did not match the required structure")
        return {"status": "ok", "model": result.model, "request_id": result.request_id}
