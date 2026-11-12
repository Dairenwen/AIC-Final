from __future__ import annotations

from enum import Enum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from .common import ApiSchema
from .interaction import PendingInteraction


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GraphRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class ResultReference(ApiSchema):
    type: NonBlankStr
    id: UUID


class RunStatusData(ApiSchema):
    run_id: UUID
    workspace_id: UUID
    status: GraphRunStatus
    result_refs: list[ResultReference] = Field(default_factory=list)
    pending_interaction: PendingInteraction | None = None
    error_code: NonBlankStr | None = None
    error_message: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is GraphRunStatus.WAITING_USER:
            if self.pending_interaction is None:
                raise ValueError("WAITING_USER 状态必须包含 pending_interaction")
        elif self.pending_interaction is not None:
            raise ValueError("非 WAITING_USER 状态不得包含 pending_interaction")

        failed_statuses = {
            GraphRunStatus.FAILED_RETRYABLE,
            GraphRunStatus.FAILED_FINAL,
        }
        if self.status in failed_statuses:
            if self.error_code is None or self.error_message is None:
                raise ValueError("FAILED 状态必须包含 error_code 和 error_message")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("非 FAILED 状态不得包含错误字段")

        return self
