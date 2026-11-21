from __future__ import annotations

import json
from pathlib import Path

from academic_translation.schemas.models import TermEntry


class TerminologyStore:
    """Small local personal/domain terminology library; no cloud service required."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[3] / "data" / "terminology.json"

    def _read(self) -> dict[str, list[dict]]:
        if not self.path.exists():
            return {"academic": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def load(self, domain: str) -> list[TermEntry]:
        return [TermEntry(**item, origin="library") for item in self._read().get(domain, [])]

    def save(self, domain: str, terms: list[TermEntry]) -> None:
        data = self._read()
        existing = {item["source"].lower(): item for item in data.setdefault(domain, [])}
        for term in terms:
            if term.origin == "fallback":
                continue
            existing[term.source.lower()] = {"source": term.source, "target": term.target, "confidence": term.confidence}
        data[domain] = list(existing.values())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
