"""合并建议下：已批准回复同步到兄弟来源的领域纯函数。

实际写库由 `ReplyStore.save_review_decision` 在 adapter 内完成；
本模块只提供可复用的报告/载荷构造，避免图层直接依赖 Session。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def build_propagated_consistency_report(
    approved_report: object,
    *,
    primary_source_id: UUID,
    primary_draft_id: UUID,
) -> dict[str, Any]:
    """为兄弟来源复制草稿时构造一致性报告，标注传播来源。"""
    if isinstance(approved_report, dict):
        return {
            **approved_report,
            "propagated_from_source_id": str(primary_source_id),
            "propagated_from_draft_id": str(primary_draft_id),
        }
    return {
        "is_consistent": True,
        "passed": True,
        "issues": [],
        "items": [],
        "cross_source_conflicts": [],
        "reminders": ["由同建议其他来源批准结果同步"],
        "propagated_from_source_id": str(primary_source_id),
        "propagated_from_draft_id": str(primary_draft_id),
    }


def build_sibling_sync_item(
    *,
    source_id: UUID,
    reply_id: UUID,
    draft_id: UUID,
    mode: str,
    superseded_run_id: str | None = None,
) -> dict[str, str]:
    """构造一次兄弟同步结果条目。"""
    item = {
        "source_id": str(source_id),
        "reply_id": str(reply_id),
        "draft_id": str(draft_id),
        "mode": mode,
    }
    if superseded_run_id:
        item["superseded_run_id"] = superseded_run_id
    return item
