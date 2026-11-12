"""SDK 对外输入/结果类型（与 graph state 字段对齐，不含 Flask 壳）。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from .common import ApiSchema, JsonObject
from .interaction import PendingInteraction
from .run import ResultReference
from .workspace import WorkspaceMode


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AgentStatus(str, Enum):
    """SDK 对外统一运行状态。"""

    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentResult(ApiSchema):
    """一次 invoke/resume 后的对外结果信封。"""

    status: AgentStatus
    thread_id: NonBlankStr
    run_id: UUID
    pending: PendingInteraction | None = None
    result_refs: list[ResultReference] = Field(default_factory=list)
    phase: NonBlankStr | None = None
    error_code: NonBlankStr | None = None
    artifacts: JsonObject = Field(default_factory=dict)


class TaskInitInput(ApiSchema):
    """WorkspaceTaskGraph TASK_INIT 启动输入（对齐 WorkspaceTaskState）。"""

    workspace_id: UUID
    user_id: NonBlankStr
    mode: WorkspaceMode = WorkspaceMode.FAST
    manuscript_version_id: UUID | None = None
    input_version: NonBlankStr | None = None


class AnalysisInput(ApiSchema):
    """SuggestionAnalysisGraph 启动输入（对齐 SuggestionAnalysisState）。"""

    workspace_id: UUID
    suggestion_id: UUID
    user_id: NonBlankStr
    mode: WorkspaceMode = WorkspaceMode.FAST
    manuscript_version_id: UUID | None = None
    input_version: NonBlankStr | None = None


class ReplyInput(ApiSchema):
    """SourceReplyGraph 启动输入（对齐 SourceReplyState）。"""

    workspace_id: UUID
    suggestion_id: UUID
    source_id: UUID
    user_id: NonBlankStr
    input_version: NonBlankStr | None = None


class FinalizeInput(ApiSchema):
    """WorkspaceTaskGraph FINALIZE 启动输入（对齐 FinalizeState）。"""

    workspace_id: UUID
    user_id: NonBlankStr
    input_version: NonBlankStr | None = None
