from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas.models import PaperRecord


class QuestionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"]
    answer: str = Field(max_length=8000)
    chunk_ids: list[str]

    @model_validator(mode="after")
    def status_matches_evidence(self) -> "QuestionAnalysis":
        if self.answer_status == "ANSWERED":
            if not self.answer.strip() or not self.chunk_ids:
                raise ValueError("ANSWERED requires an answer and Chunk citations")
        elif self.chunk_ids:
            raise ValueError("non-answered status must not cite Chunks")
        return self


class QuestionAnsweringGateway(Protocol):
    def answer_question(
        self,
        question: str,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> QuestionAnalysis: ...
