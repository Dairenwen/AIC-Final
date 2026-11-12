"""Workspace / Review 读与任务初始化落库端口。"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from langgraph_agent.ports.types import (
    PersistTaskInitResult,
    ReviewInputRecord,
    ReviewPartyRecord,
    WorkspaceRecord,
)


class WorkspaceStore(Protocol):
    """工作区与审稿输入访问端口。

    图节点通过本接口读写，禁止直接依赖 ORM 会话对象。
    """

    def get_workspace(self, workspace_id: UUID) -> WorkspaceRecord | None:
        """按主键读取工作区。

        对应 backend：`WorkspaceRepository.get_by_id`。
        """
        ...

    def list_current_review_inputs(
        self, workspace_id: UUID
    ) -> list[ReviewInputRecord]:
        """列出工作区当前生效的审稿输入，并附带 party 展示字段。

        对应 backend：
        - `ReviewRepository.list_inputs(..., current_only=True)`
        - `ReviewRepository.list_parties`（拼装 role/display_name/raw_label）
        调用方语义对齐 `workspace_task_graph.load_inputs`。
        """
        ...

    def list_parties(self, workspace_id: UUID) -> list[ReviewPartyRecord]:
        """列出工作区下全部审稿方。

        对应 backend：`ReviewRepository.list_parties`。
        """
        ...

    def persist_task_init_result(
        self,
        *,
        workspace_id: UUID,
        input_version: str,
        confirmed_suggestions: list[dict[str, Any]],
    ) -> PersistTaskInitResult:
        """任务初始化确认后的建议/来源落库，并将 Workspace 置为 ACTIVE。

        对应 backend：
        - 组合逻辑：`graphs/persist.py::persist_and_ready`
        - 底层写入：
          - `WorkspaceRepository.update_status`（ACTIVE）
          - `SuggestionRepository.list_suggestions` / `create_suggestion`
          - `SuggestionRepository.find_source` / `create_source`
        """
        ...
