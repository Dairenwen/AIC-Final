"""审稿意见拆分节点的输入、模型输出与最终结果契约。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from .common import ApiSchema


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SplitReviewInput(ApiSchema):
    """单条原始审稿意见及可选语言提示。"""

    original_text: NonBlankStr
    language: NonBlankStr | None = None


class SplitCandidate(ApiSchema):
    """LLM 仅负责生成的第 2.5 节问题点字段。"""

    atomic_concern: NonBlankStr = Field(
        description="可独立处理的单一问题或诉求的归一化简述"
    )
    explicit_request: str | None = Field(
        description="审稿人明确提出的动作要求；没有明确要求时为 null"
    )
    implicit_concern: str | None = Field(
        description="审稿人实际担心的问题；没有隐含担忧时为 null"
    )
    source_quote: NonBlankStr = Field(
        description="能够支撑该问题点的原文连续片段，不得改写或补充"
    )
    # 模型常漏此字段；缺省不阻断拆分主路径。
    split_confidence: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="该问题点拆分正确的置信度，范围为 0 到 1；缺省 0.5",
    )

    @field_validator("explicit_request", "implicit_concern", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("split_confidence", mode="before")
    @classmethod
    def default_split_confidence(cls, value: object) -> object:
        if value is None or value == "":
            return 0.5
        return value


class LlmSplitResult(ApiSchema):
    """供结构化 LLM 调用使用的最小结果。"""

    review_points: list[SplitCandidate] = Field(
        description="从一条原始意见拆出的可独立处理问题点；纯正面评价返回空列表"
    )


class ReviewPoint(ApiSchema):
    """第 2.5 节定义的完整问题点结构。"""

    point_id: NonBlankStr
    reviewer_id: NonBlankStr | None
    original_item_id: NonBlankStr | None
    original_item_number: NonBlankStr | None
    original_text: NonBlankStr
    atomic_concern: NonBlankStr
    explicit_request: str | None
    implicit_concern: str | None
    source_order: int = Field(ge=1)
    source_quote: NonBlankStr
    split_confidence: float = Field(ge=0, le=1)
    split_status: NonBlankStr | None
    parent_point_id: NonBlankStr | None


class SplitReviewResult(ApiSchema):
    """拆分节点返回的完整问题点列表。"""

    review_points: list[ReviewPoint]
