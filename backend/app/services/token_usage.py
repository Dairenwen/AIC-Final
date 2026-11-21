from __future__ import annotations

import calendar
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import TokenUsageRecord


class TokenQuotaExceeded(RuntimeError):
    def __init__(self, snapshot: dict[str, Any]):
        super().__init__("本周期免费 Token 额度已用尽")
        self.snapshot = snapshot


def current_period_start(now: datetime | None = None) -> date:
    current = now or datetime.now(UTC)
    return date(current.year, current.month, 1)


def period_end(period_start: date) -> date:
    last_day = calendar.monthrange(period_start.year, period_start.month)[1]
    return date(period_start.year, period_start.month, last_day)


def quota_limit(role: str | None = None) -> int | None:
    if role and role != "normal_user":
        return None
    return int(current_app.config.get("NORMAL_USER_FREE_TOKEN_QUOTA", 1_000_000))


def estimate_tokens(value: object) -> int:
    """Conservative provider-independent estimate used before a request is sent."""
    text = str(value or "")
    return max(1, math.ceil(len(text) / 2)) if text else 0


def estimate_messages_tokens(messages: Iterable[Mapping[str, object]], max_output_tokens: int = 0) -> int:
    prompt_tokens = sum(estimate_tokens(item.get("role")) + estimate_tokens(item.get("content")) for item in messages)
    return prompt_tokens + max(0, int(max_output_tokens or 0))


def _aggregate(user_id: UUID | str, period: date) -> tuple[int, dict[str, int]]:
    # Keep aggregation at request time so the quota remains auditable per model/source.
    try:
        grouped = db.session.execute(
            select(
                TokenUsageRecord.source,
                func.coalesce(func.sum(TokenUsageRecord.total_tokens), 0),
            )
            .where(TokenUsageRecord.user_id == user_id, TokenUsageRecord.period_start == period)
            .group_by(TokenUsageRecord.source)
        ).all()
    except SQLAlchemyError:
        # Keep older installations usable until migrate_model_catalog.py is run.
        db.session.rollback()
        current_app.logger.warning("token usage table is unavailable; quota is temporarily untracked", exc_info=True)
        return 0, {}
    breakdown = {str(source): int(value or 0) for source, value in grouped}
    return int(sum(breakdown.values())), breakdown


def usage_snapshot(user_id: UUID | str, role: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    period = current_period_start(now)
    used, breakdown = _aggregate(user_id, period)
    limit = quota_limit(role)
    remaining = None if limit is None else max(0, limit - used)
    return {
        "period_start": period.isoformat(),
        "period_end": period_end(period).isoformat(),
        "quota": limit,
        "used": used,
        "remaining": remaining,
        "unlimited": limit is None,
        "usage_percent": 0 if not limit else min(100, round(used * 100 / limit, 2)),
        "breakdown": {"platform": breakdown.get("platform", 0), "api": breakdown.get("api", 0)},
    }


def ensure_token_quota(user_id: UUID | str, role: str | None, estimated_tokens: int) -> dict[str, Any]:
    snapshot = usage_snapshot(user_id, role)
    if snapshot["quota"] is not None and snapshot["used"] + max(0, int(estimated_tokens)) > snapshot["quota"]:
        raise TokenQuotaExceeded(snapshot)
    return snapshot


def normalize_usage(usage: Mapping[str, object] | None, *, fallback_input: int = 0, fallback_output: int = 0) -> dict[str, int]:
    data = usage or {}
    input_tokens = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
    output_tokens = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
    total_tokens = int(data.get("total_tokens") or 0)
    input_tokens = input_tokens or max(0, int(fallback_input))
    output_tokens = output_tokens or max(0, int(fallback_output))
    total_tokens = total_tokens or input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def record_token_usage(
    *,
    user_id: UUID | str,
    role: str | None,
    source: str,
    model_name: str | None,
    usage: Mapping[str, object] | None,
    request_kind: str = "chat",
    fallback_input: int = 0,
    fallback_output: int = 0,
) -> dict[str, Any]:
    normalized = normalize_usage(usage, fallback_input=fallback_input, fallback_output=fallback_output)
    if normalized["total_tokens"] <= 0:
        return usage_snapshot(user_id, role)
    item = TokenUsageRecord(
        user_id=user_id,
        period_start=current_period_start(),
        source="api" if source == "api" else "platform",
        model_name=model_name,
        input_tokens=normalized["input_tokens"],
        output_tokens=normalized["output_tokens"],
        total_tokens=normalized["total_tokens"],
        request_kind=request_kind,
        metadata_json={},
    )
    db.session.add(item)
    db.session.commit()
    return usage_snapshot(user_id, role)
