from __future__ import annotations

import re

import httpx


ARXIV_ID_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.IGNORECASE)


class PaperSourceError(RuntimeError):
    pass


class ArxivPdfDownloader:
    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        maximum_pdf_bytes: int = 50 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.maximum_pdf_bytes = maximum_pdf_bytes
        self.client = client

    def download(self, arxiv_id: str) -> bytes:
        identifier = arxiv_id.strip()
        if not ARXIV_ID_PATTERN.fullmatch(identifier):
            raise PaperSourceError("invalid arXiv identifier")
        url = f"https://arxiv.org/pdf/{identifier}"
        try:
            if self.client is not None:
                response = self.client.get(url, follow_redirects=True, timeout=self.timeout_seconds)
            else:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    response = client.get(url)
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            raise PaperSourceError("arXiv PDF download failed") from exc
        if not data.startswith(b"%PDF-"):
            raise PaperSourceError("arXiv response is not a PDF")
        if len(data) > self.maximum_pdf_bytes:
            raise PaperSourceError("arXiv PDF exceeds the input limit")
        return data
