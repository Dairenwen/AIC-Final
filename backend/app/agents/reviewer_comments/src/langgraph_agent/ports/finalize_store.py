"""FINALIZE 上下文加载与导出快照落库端口。

现有 Workspace/Reply/Analysis/Run 端口不足以表达「工作区级汇总 + EXPORT 审计」
组合语义，故单独定义 FinalizeStore。实现归 adapters（A5/后续补齐）；
本文件仅 Protocol + 薄 DTO。
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict
from uuid import UUID


class FinalizeContext(TypedDict, total=False):
    """`load_finalize_context` 返回形态（字段对齐 backend finalize_graph）。"""

    workspace_id: str
    workspace_title: str
    user_id: str
    global_settings: dict[str, Any]
    suggestions: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    internal_revision_items: list[dict[str, Any]]
    external_replies: list[dict[str, Any]]
    suggestion_by_id: dict[str, dict[str, Any]]


class ExportSnapshotRecord(TypedDict, total=False):
    """EXPORT CONFIRM 审计 payload 形态。"""

    export_snapshot_id: str
    workspace_id: str
    workspace_title: str
    created_at: str
    created_by: str
    content_hash: str
    output_files: list[dict[str, Any]]
    validation_result: dict[str, Any]
    internal_revision_items: list[dict[str, Any]]
    external_replies: list[dict[str, Any]]
    included_source_ids: list[str]
    accepted_draft_version_ids: list[str]
    revision_action_version_ids: list[str]
    global_expression_settings: dict[str, Any]
    acknowledged_warnings: list[Any]


class FinalizeStore(Protocol):
    """定稿/导出图的读写端口。

    对应 backend：
    - 读：`finalize_graph.load_finalize_context` /
      `load_latest_export_snapshot`
    - 写：`AuditRepository.create(target_type=\"EXPORT\", action=\"CONFIRM\")`
    - 幂等读：`AuditRepository.list_by_target`
    """

    def load_finalize_context(self, workspace_id: UUID) -> FinalizeContext:
        """只读加载 Workspace、ACTIVE source、回复当前版本与修改事实。

        对应 backend：`finalize_graph.load_finalize_context`。
        Workspace 不存在时应抛 ValueError。
        """
        ...

    def load_export_snapshot(self, snapshot_id: UUID) -> ExportSnapshotRecord | None:
        """按 export_snapshot_id 读取已落库的 EXPORT CONFIRM 快照。

        对应 backend：`AuditRepository.list_by_target(EXPORT, snapshot_id)`
        取最后一条 payload；无则 None。
        """
        ...

    def save_export_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        snapshot: dict[str, Any],
        actor_user_id: str,
    ) -> ExportSnapshotRecord:
        """幂等写入 EXPORT CONFIRM 审计事件，返回最终快照。

        若同 snapshot_id 已存在 CONFIRM 记录，应返回已有 payload，不重复写入。
        对应 backend：`generate_final` 中 AuditRepository 分支。
        """
        ...

    def load_latest_export_snapshot(
        self, workspace_id: UUID
    ) -> ExportSnapshotRecord | None:
        """读取工作区最近一次 EXPORT CONFIRM 快照；无则 None。

        对应 backend：`finalize_graph.load_latest_export_snapshot`。
        """
        ...
