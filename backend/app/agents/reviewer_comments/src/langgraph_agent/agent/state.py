"""WorkspaceTaskGraph 的极薄状态结构。"""

from __future__ import annotations

from typing import Literal, TypedDict
from uuid import UUID

from langgraph_agent.schemas import GraphRunStatus, JsonObject, WorkspaceMode


class WorkspaceTaskState(TypedDict):
    """WorkspaceTaskGraph 执行上下文（对齐技术手册第 19.2 节）。"""

    workspace_id: UUID
    user_id: str
    mode: WorkspaceMode
    manuscript_version_id: UUID | None
    thread_id: str
    run_id: UUID
    run_scope: Literal["TASK_INIT", "FINALIZE"]
    input_version: str
    phase: str
    pending_interaction_id: UUID | None
    draft_refs: JsonObject
    # 只存纯 dict，避免 checkpoint msgpack 反序列化未注册的 ResultReference。
    result_refs: list[dict[str, str]]
    status: GraphRunStatus
    error_code: str | None
