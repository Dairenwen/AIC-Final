"""FINALIZE 导出：复用 A6 tools.export_files，并提供 summary 侧纯函数。

来源：backend/app/graphs/finalize_export.py + finalize_graph 中 summary 辅助。
文件生成逻辑只读复用 tools；本模块不重复实现。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langgraph_agent.tools.export_files import (
    export_download_meta,
    generate_export_files,
    group_external_replies_by_party,
    normalize_export_format,
    render_export_markdown,
    resolve_registered_export_path,
)

# 兼容 backend 测试中的 _markdown 调用名
_markdown = render_export_markdown


def enrich_output_files_for_summary(
    workspace_id: UUID | str, snapshot: dict[str, Any] | None
) -> dict[str, Any] | None:
    """为 summary 附带 file_id / download_path，不暴露可写路径给前端改写。"""
    if snapshot is None:
        return None
    enriched = dict(snapshot)
    raw_files = snapshot.get("output_files")
    if not isinstance(raw_files, list):
        return enriched
    files: list[dict[str, Any]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        format_name = str(entry.get("format") or "").strip().upper()
        if format_name:
            entry["file_id"] = format_name
            entry["download_path"] = (
                f"/api/workspaces/{workspace_id}/exports/latest/files/"
                f"{format_name.lower()}"
            )
        files.append(entry)
    enriched["output_files"] = files
    return enriched


__all__ = [
    "enrich_output_files_for_summary",
    "export_download_meta",
    "generate_export_files",
    "group_external_replies_by_party",
    "normalize_export_format",
    "render_export_markdown",
    "resolve_registered_export_path",
    "_markdown",
]
