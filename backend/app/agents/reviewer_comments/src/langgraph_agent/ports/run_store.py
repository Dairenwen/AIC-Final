"""GraphRun 状态端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from langgraph_agent.ports.types import GraphRunRecord


class RunStore(Protocol):
    """图运行记录查询与状态迁移端口。"""

    def get_graph_run(self, run_id: UUID) -> GraphRunRecord | None:
        """按主键读取 GraphRun。

        对应 backend：`GraphRunRepository.get_by_id`。
        """
        ...

    def mark_running(
        self,
        run_id: UUID,
        *,
        started_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        """标记运行为 RUNNING，并清理终态时间戳。

        对应 backend：`GraphRunRepository.update_status(status="RUNNING", ...)`。
        典型调用：resume 后从 WAITING_USER 回到 RUNNING（见 analysis/reply service）。
        """
        ...

    def mark_waiting(
        self,
        run_id: UUID,
        *,
        result_refs: list[dict[str, Any]] | list[Any] | None = None,
    ) -> GraphRunRecord | None:
        """标记运行为 WAITING_USER（interrupt 等待人工）。

        对应 backend：`GraphRunRepository.update_status(status="WAITING_USER", ...)`。
        """
        ...

    def mark_succeeded(
        self,
        run_id: UUID,
        *,
        result_refs: list[dict[str, Any]] | list[Any] | None = None,
        finished_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        """标记运行为 SUCCEEDED。

        对应 backend：`GraphRunRepository.update_status(status="SUCCEEDED", ...)`。
        """
        ...

    def mark_failed(
        self,
        run_id: UUID,
        *,
        error_code: str | None,
        error_message: str | None,
        final: bool = False,
        finished_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        """标记运行为失败。

        对应 backend：`GraphRunRepository.update_status`：
        - `final=False` → `FAILED_RETRYABLE`
        - `final=True` → `FAILED_FINAL`
        """
        ...
