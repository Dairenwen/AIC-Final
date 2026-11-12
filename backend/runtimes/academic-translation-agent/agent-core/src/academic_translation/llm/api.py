from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from academic_translation.settings import settings


class OpenAICompatibleAcademicLLM:
    """Small dependency-free client for OpenAI-compatible chat APIs."""

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = (model or settings.translation_api_model).strip()
        self.base_url = (base_url or settings.translation_api_base_url).strip().rstrip("/")
        self.api_key = api_key if api_key is not None else settings.translation_api_key
        if not self.base_url:
            raise ValueError("TRANSLATION_API_BASE_URL is required when API translation is enabled.")
        if not self.model:
            raise ValueError("TRANSLATION_API_MODEL is required when API translation is enabled.")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": min(
                settings.translation_api_max_tokens,
                max(512, int(len(prompt) * 1.5)),
            ),
        }
        if settings.translation_api_disable_thinking:
            # SiliconFlow/Qwen-compatible endpoints use this OpenAI extension.
            payload["extra_body"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        attempts = max(1, settings.translation_api_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=settings.translation_api_timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = RuntimeError(f"Translation API returned HTTP {exc.code}: {detail}")
                if exc.code not in {408, 409, 425, 429} and exc.code < 500:
                    raise last_error from exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt + 1 < attempts:
                time.sleep(settings.translation_api_retry_backoff_seconds * (2**attempt))
        else:
            if isinstance(last_error, json.JSONDecodeError):
                raise RuntimeError("Translation API returned invalid JSON.") from last_error
            if last_error is not None:
                raise RuntimeError(f"Translation API request failed: {last_error}") from last_error
            raise RuntimeError("Translation API request failed after retries.")

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Translation API response has no choices[0].message.content.") from exc
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        result = re.sub(r"<think>.*?</think>", "", str(content), flags=re.DOTALL).strip()
        if not result:
            raise RuntimeError("Translation API returned empty content.")
        return result
