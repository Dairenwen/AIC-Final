"""论文解析与信息卡片使用的精简数据结构。

来源：backend/app/parsing/paper_schemas.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SectionType(StrEnum):
    ABSTRACT = "ABSTRACT"
    INTRODUCTION = "INTRODUCTION"
    RELATED_WORK = "RELATED_WORK"
    METHOD = "METHOD"
    DATASET = "DATASET"
    EXPERIMENTS = "EXPERIMENTS"
    RESULTS = "RESULTS"
    ABLATION = "ABLATION"
    DISCUSSION = "DISCUSSION"
    LIMITATIONS = "LIMITATIONS"
    CONCLUSION = "CONCLUSION"
    REFERENCES = "REFERENCES"
    OTHER = "OTHER"


class CardType(StrEnum):
    RESEARCH_QUESTION = "RESEARCH_QUESTION"
    RESEARCH_MOTIVATION = "RESEARCH_MOTIVATION"
    CORE_CONTRIBUTIONS = "CORE_CONTRIBUTIONS"
    MAIN_METHOD = "MAIN_METHOD"
    DATASET_OR_SAMPLE = "DATASET_OR_SAMPLE"
    EXPERIMENT_SETUP_BASELINES_METRICS = "EXPERIMENT_SETUP_BASELINES_METRICS"
    MAIN_RESULTS = "MAIN_RESULTS"
    ABLATION_OR_SUPPLEMENTARY_ANALYSIS = "ABLATION_OR_SUPPLEMENTARY_ANALYSIS"
    LIMITATIONS = "LIMITATIONS"
    RESEARCH_BOUNDARY = "RESEARCH_BOUNDARY"


class ConfirmationStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EDITED = "EDITED"
    DELETED = "DELETED"


class LlmPaperCardCandidate(BaseModel):
    """LLM 生成的内部候选；来源章节 ID 在落库前转换为公开字段。"""

    model_config = ConfigDict(extra="forbid")

    card_type: CardType
    content: str = Field(min_length=1, max_length=800)
    source_section_id: str = Field(min_length=1)
    source_quote: str = Field(min_length=1, max_length=1600)
    # 次要字段：json_object / 宽松模型常漏；缺省不阻断主路径。
    confidence: float = Field(
        default=0.75,
        ge=0,
        le=1,
        description="该卡片的置信度，范围 0 到 1；缺省 0.75",
    )

    @field_validator("content", "source_section_id", "source_quote")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def default_confidence(cls, value: object) -> object:
        if value is None or value == "":
            return 0.75
        return value


class LlmPaperCardBatch(BaseModel):
    """一次结构化调用返回的全部论文卡片候选。"""

    model_config = ConfigDict(extra="forbid")

    cards: list[LlmPaperCardCandidate] = Field(default_factory=list, max_length=10)


@dataclass(slots=True)
class PaperSection:
    original_heading: str
    normalized_type: SectionType
    text: str
    pages: list[int]
    confidence: float
    # 结构大纲元数据；默认值保留旧调用方按原字段构造的兼容性。
    section_id: str | None = None
    parent_id: str | None = None
    order_index: int | None = None
    level: int | None = None
    excerpt: str = ""


@dataclass(slots=True)
class ParsedPaper:
    title: str
    abstract: str
    full_text: str
    sections: list[PaperSection] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "abstract": self.abstract,
            "full_text": self.full_text,
            "sections": [
                {
                    "section_id": section.section_id,
                    "parent_id": section.parent_id,
                    "order_index": section.order_index,
                    "original_heading": section.original_heading,
                    "normalized_type": section.normalized_type.value,
                    "level": section.level,
                    "text": section.text,
                    "pages": section.pages,
                    "confidence": section.confidence,
                    "excerpt": section.excerpt,
                }
                for section in self.sections
            ],
            "parse_warnings": self.parse_warnings,
        }


@dataclass(slots=True)
class PaperCard:
    card_type: CardType
    content: str
    source_sections: list[str]
    source_quote: str
    confidence: float
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_type": self.card_type.value,
            "content": self.content,
            "source_sections": self.source_sections,
            "source_quote": self.source_quote,
            "confidence": self.confidence,
            "confirmation_status": self.confirmation_status.value,
        }


__all__ = [
    "CardType",
    "ConfirmationStatus",
    "LlmPaperCardBatch",
    "LlmPaperCardCandidate",
    "PaperCard",
    "PaperSection",
    "ParsedPaper",
    "SectionType",
]
