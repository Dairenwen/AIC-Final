"""FINALIZE 与来源回复图共用的跨来源一致性检查。

来源：backend/app/graphs/finalize_consistency.py
纯函数，无 DB / 无 ports 依赖。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Sequence
from uuid import UUID


def _normalized(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _structured_assertions(
    response_facts: object,
) -> list[tuple[str, str, str | None]]:
    """提取可确定比较的 ``key/value`` 事实，不猜测自由文本语义。"""
    if isinstance(response_facts, dict):
        candidates = response_facts.get("fact_items", [])
        if not isinstance(candidates, list):
            candidates = []
    elif isinstance(response_facts, list):
        candidates = response_facts
    else:
        candidates = []
    assertions: list[tuple[str, str, str | None]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = next(
            (
                item[field]
                for field in ("fact_key", "key", "name", "subject")
                if isinstance(item.get(field), str) and item[field].strip()
            ),
            None,
        )
        value = next(
            (
                item[field]
                for field in ("value", "status", "claim", "answer")
                if item.get(field) is not None
            ),
            None,
        )
        if key is None or value is None:
            continue
        fact_id = item.get("fact_id")
        assertions.append(
            (
                _normalized(key),
                _normalized(value),
                str(fact_id) if fact_id is not None else None,
            )
        )
    return assertions


def _explicit_conflicts(reply: dict[str, Any]) -> list[dict[str, Any]]:
    draft = reply.get("current_draft")
    if not isinstance(draft, dict):
        return []
    report = draft.get("consistency_report")
    if not isinstance(report, dict):
        return []
    conflicts = report.get("cross_source_conflicts", [])
    return [item for item in conflicts if isinstance(item, dict)]


def check_cross_source_consistency(
    suggestion_id: UUID | str,
    replies: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """返回同一 suggestion 下的跨来源矛盾项。

    这是单一检查入口。SourceReplyGraph 后续接入时应直接复用本函数，
    不再实现第二份规则。
    """
    suggestion_id_text = str(suggestion_id)
    conflicts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for reply in replies:
        source_id = str(reply.get("source_id"))
        for item in _explicit_conflicts(reply):
            description = str(item.get("description", "跨来源回复存在矛盾")).strip()
            related_fact_ids = [
                str(value) for value in item.get("related_fact_ids", [])
            ]
            signature = _normalized(
                {
                    "source_id": source_id,
                    "description": description,
                    "related_fact_ids": sorted(related_fact_ids),
                }
            )
            if signature in seen:
                continue
            seen.add(signature)
            conflicts.append(
                {
                    "code": "CROSS_SOURCE_CONFLICT",
                    "suggestion_id": suggestion_id_text,
                    "source_ids": [source_id],
                    "description": description,
                    "related_fact_ids": related_fact_ids,
                }
            )

    assertions: dict[str, dict[str, list[tuple[str, str | None]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for reply in replies:
        source_id = str(reply.get("source_id"))
        for key, value, fact_id in _structured_assertions(
            reply.get("response_facts")
        ):
            assertions[key][value].append((source_id, fact_id))

    for key, values in assertions.items():
        if len(values) < 2:
            continue
        source_ids = sorted(
            {
                source_id
                for entries in values.values()
                for source_id, _fact_id in entries
            }
        )
        related_fact_ids = sorted(
            {
                fact_id
                for entries in values.values()
                for _source_id, fact_id in entries
                if fact_id is not None
            }
        )
        description = f"同一事实 {key!r} 在不同来源回复中取值不一致"
        signature = _normalized(
            {
                "source_ids": source_ids,
                "description": description,
                "related_fact_ids": related_fact_ids,
            }
        )
        if signature in seen:
            continue
        seen.add(signature)
        conflicts.append(
            {
                "code": "CROSS_SOURCE_CONFLICT",
                "suggestion_id": suggestion_id_text,
                "source_ids": source_ids,
                "description": description,
                "related_fact_ids": related_fact_ids,
            }
        )

    return conflicts


__all__ = ["check_cross_source_consistency"]
