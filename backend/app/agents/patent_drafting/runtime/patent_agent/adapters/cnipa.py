from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patent_agent.config import CnipaConfig
from patent_agent.errors import SearchError


@dataclass(frozen=True)
class PriorArtRecord:
    publication_number: str | None
    application_number: str | None
    title: str | None
    applicant: str | None
    filing_date: str | None
    publication_date: str | None
    abstract: str | None
    source_url: str | None
    source_name: str
    retrieved_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SEARCH_STATUSES = {
    "success",
    "zero_results",
    "transport_error",
    "timeout",
    "waf_or_verification_error",
    "tool_error",
    "parse_error",
}


@dataclass(frozen=True)
class SearchResult:
    query: str
    status: str
    result_count: int
    records: list[PriorArtRecord]
    error_type: str | None
    error_message: str | None
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "status": self.status,
            "result_count": self.result_count,
            "records": [record.to_dict() for record in self.records],
            "error_type": self.error_type,
            "error_message": self.error_message,
            "elapsed_seconds": self.elapsed_seconds,
        }


def normalize_hit(hit: dict[str, Any], *, retrieved_at: str | None = None) -> PriorArtRecord:
    return PriorArtRecord(
        publication_number=hit.get("publication_number") or hit.get("pub_number"),
        application_number=hit.get("application_number"),
        title=hit.get("title"),
        applicant=hit.get("applicant"),
        filing_date=hit.get("filing_date"),
        publication_date=hit.get("publication_date"),
        abstract=hit.get("abstract"),
        source_url=hit.get("source_url") or hit.get("link"),
        source_name=hit.get("source_name") or "CNIPA Patent Gazette",
        retrieved_at=retrieved_at or hit.get("retrieved_at") or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def dedupe_records(records: list[PriorArtRecord]) -> list[PriorArtRecord]:
    seen: set[str] = set()
    out: list[PriorArtRecord] = []
    for record in records:
        key = record.publication_number or record.source_url or (record.title or "")[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


class CnipaAdapter:
    def __init__(self, config: CnipaConfig):
        self.config = config

    def search(self, query: str) -> SearchResult:
        query = " ".join(query.split()).strip()
        if not query or len(query) > 80:
            raise SearchError("CNIPA query must be a non-empty abstract technical phrase of at most 80 characters")
        if not self.config.tool.is_file():
            raise SearchError(f"CNIPA tool not found: {self.config.tool}")
        started = time.monotonic()
        proc: subprocess.CompletedProcess[str] | None = None
        # Keep the crawler's WAF polling inside the adapter's process budget.
        # Without this, the vendored tool can wait 180s before the adapter's
        # shorter timeout has a chance to classify the failure.
        waf_wait = max(5, min(int(self.config.timeout_seconds) - 5, 45))
        child_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "EPUB_WAF_MAX_WAIT_SEC": str(waf_wait),
        }
        for attempt in range(2):
            try:
                proc = subprocess.run(
                    [sys.executable, str(self.config.tool), query],
                    cwd=str(self.config.tool.parent),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.timeout_seconds,
                    env=child_env,
                )
            except subprocess.TimeoutExpired:
                return self._error(query, "timeout", "TimeoutExpired", f"CNIPA query timed out after {self.config.timeout_seconds:g}s", started)
            except OSError:
                return self._error(query, "transport_error", "OSError", "CNIPA process failed to start", started)
            if proc.returncode == 0:
                break
            reason = next((line for line in proc.stderr.splitlines() if line.startswith(("CNIPA_EPUB_ERROR:", "ERROR:"))), "CNIPA tool returned a non-zero exit code")
            lower = (reason + "\n" + proc.stderr).lower()
            if (
                attempt == 0
                and re.search(
                    r"execution context was destroyed|most likely because of a navigation|"
                    r"target page.*closed",
                    lower,
                )
            ):
                continue
            if re.search(r"captcha|验证码|verification|waf|人机|访问验证", lower):
                status = "waf_or_verification_error"
            elif re.search(r"timeout|timed out|超时", lower):
                status = "timeout"
            elif re.search(r"network|connection|dns|transport|网络|连接", lower):
                status = "transport_error"
            else:
                status = "tool_error"
            return self._error(query, status, "CnipaToolError", reason[:500], started)
        if proc is None:
            return self._error(query, "tool_error", "CnipaToolError", "CNIPA tool did not run", started)
        line = next((line for line in proc.stdout.splitlines() if line.startswith("EPUB_HITS_JSON:")), "")
        if not line:
            return self._error(query, "parse_error", "MissingOutputContract", "CNIPA output contract missing EPUB_HITS_JSON", started)
        try:
            raw = json.loads(line.split(":", 1)[1].strip())
        except json.JSONDecodeError:
            return self._error(query, "parse_error", "JSONDecodeError", "CNIPA output contained invalid JSON", started)
        if not isinstance(raw, list):
            return self._error(query, "parse_error", "InvalidOutputType", "CNIPA output JSON must be a list", started)
        if not raw:
            return SearchResult(query, "zero_results", 0, [], None, None, round(time.monotonic() - started, 3))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        records = dedupe_records([normalize_hit(row, retrieved_at=now) for row in raw if isinstance(row, dict)])
        if not records and raw:
            return self._error(query, "parse_error", "NoValidRecords", "CNIPA output contained no valid record objects", started)
        return SearchResult(query, "success", len(records), records, None, None, round(time.monotonic() - started, 3))

    @staticmethod
    def _error(query: str, status: str, error_type: str, message: str, started: float) -> SearchResult:
        return SearchResult(query, status, 0, [], error_type, message, round(time.monotonic() - started, 3))


class FixtureCnipaAdapter:
    def __init__(self, fixture: Path):
        self.fixture = fixture

    def search(self, query: str) -> SearchResult:
        started = time.monotonic()
        raw = json.loads(self.fixture.read_text(encoding="utf-8"))
        records = []
        for row in raw:
            labeled = dict(row)
            labeled["source_name"] = "Fixture demo data (not real prior art)"
            records.append(normalize_hit(labeled, retrieved_at="2026-01-01T00:00:00Z"))
        records = dedupe_records(records)
        return SearchResult(query, "success", len(records), records, None, None, round(time.monotonic() - started, 3))
