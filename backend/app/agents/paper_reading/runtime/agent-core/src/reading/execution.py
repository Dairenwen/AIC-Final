from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ExecutionMode = Literal["flow_first", "strict"]
StageStatus = Literal[
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "FAILED_CONTINUED",
    "NOT_REQUESTED",
]


class DegradationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int | None = None
    object_id: str
    snippet: str = Field(min_length=1, max_length=240)


class FlowDegradation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    code: str
    category: Literal[
        "USER_SCOPE",
        "EXTERNAL_TOOL",
        "MODEL",
        "RELIABILITY_GATE",
        "INTERNAL",
    ] = "INTERNAL"
    message: str
    action: str = "Review the stage code before retrying."
    candidates: list[DegradationCandidate] = Field(default_factory=list)


class FlowExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ExecutionMode
    completion_status: Literal["COMPLETED", "COMPLETED_WITH_WARNINGS"]
    stages: dict[str, StageStatus]
    degradations: list[FlowDegradation] = Field(default_factory=list)


def optional_analysis_enabled(
    explicit_value: bool | None,
    *,
    mode: ExecutionMode,
    depth: str,
) -> bool:
    if explicit_value is not None:
        return explicit_value
    return mode == "flow_first" and depth == "DEEP"
