from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ModelRequestRecord:
    request_id: int
    request_kind: str
    status: str
    duration_seconds: float
    http_status_code: int | None


class ModelRequestTelemetry:
    """Thread-safe, content-free timing records for external model requests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_request_id = 1
        self._records: list[ModelRequestRecord] = []

    def record(self, request_kind: str, operation: Callable[[], T]) -> T:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        started_at = perf_counter()
        try:
            result = operation()
        except Exception as exc:
            response = getattr(exc, "response", None)
            self._append(
                ModelRequestRecord(
                    request_id=request_id,
                    request_kind=request_kind,
                    status="FAILED",
                    duration_seconds=max(0.0, perf_counter() - started_at),
                    http_status_code=getattr(response, "status_code", None),
                )
            )
            raise
        self._append(
            ModelRequestRecord(
                request_id=request_id,
                request_kind=request_kind,
                status="SUCCEEDED",
                duration_seconds=max(0.0, perf_counter() - started_at),
                http_status_code=getattr(result, "status_code", None),
            )
        )
        return result

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            records = sorted(self._records, key=lambda item: item.request_id)
        succeeded = sum(item.status == "SUCCEEDED" for item in records)
        return {
            "request_count": len(records),
            "succeeded_count": succeeded,
            "failed_count": len(records) - succeeded,
            "cumulative_request_seconds": round(
                sum(item.duration_seconds for item in records),
                3,
            ),
            "requests": [
                {
                    "request_id": item.request_id,
                    "request_kind": item.request_kind,
                    "status": item.status,
                    "duration_seconds": round(item.duration_seconds, 3),
                    "http_status_code": item.http_status_code,
                }
                for item in records
            ],
        }

    def _append(self, record: ModelRequestRecord) -> None:
        with self._lock:
            self._records.append(record)
