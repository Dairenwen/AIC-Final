from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from schemas.models import PaperRecord, ReadingRequest, ReadingWarning


class AnalysisClaim(BaseModel):
    """Provider-neutral claim candidate returned by a reading model."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_type: Literal[
        "RESEARCH_QUESTION",
        "METHOD",
        "EQUATION_FIGURE",
        "EXPERIMENT",
        "INNOVATION",
        "LIMITATION",
    ]
    claim_source: Literal["AUTHOR_STATED", "EVIDENCE_DERIVED", "AGENT_INFERRED"]
    content: str
    chunk_ids: list[str] = Field(min_length=1)


class ClaimSupportCheck(BaseModel):
    """Bounded semantic fallback result for one Claim and its bound Evidence."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    unsupported_fragments: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)


class AnalysisBasicInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1000)
    authors: list[str] = Field(min_length=1)
    year: int | None = Field(default=None, ge=1600, le=3000)


class DeepReadingNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    one_sentence_summary: str = Field(min_length=1, max_length=2000)
    background_and_motivation: list[str] = Field(default_factory=list)
    problem_definition: list[str] = Field(default_factory=list)
    method_data_flow: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    further_reading_questions: list[str] = Field(default_factory=list)


class ReadingAnalysis(BaseModel):
    """Structured model output consumed by the deterministic workflow."""

    model_config = ConfigDict(extra="forbid")

    basic_information: AnalysisBasicInformation | None = None
    narrative: DeepReadingNarrative | None = None
    claims: list[AnalysisClaim] = Field(min_length=1)
    warnings: list[ReadingWarning] = Field(default_factory=list)


class ModelGateway(Protocol):
    def analyze_paper(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        context: list[dict[str, Any]],
    ) -> ReadingAnalysis: ...


class FakeModelGateway:
    """Deterministic test adapter; production composition injects a real gateway."""

    def analyze_paper(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        context: list[dict[str, Any]],
    ) -> ReadingAnalysis:
        if not context:
            raise ValueError("fixture analysis requires at least one chunk")
        chunk = context[0]
        return ReadingAnalysis(
            claims=[
                AnalysisClaim(
                    claim_id="claim_fixture_method_001",
                    claim_type="METHOD",
                    claim_source="AUTHOR_STATED",
                    content="The synthetic fixture describes a bounded two-stage reading workflow.",
                    chunk_ids=[chunk["chunk_id"]],
                )
            ],
            warnings=[
                ReadingWarning(
                    warning_code="FIXTURE_ONLY",
                    message="The injected deterministic gateway proves the Agent flow, not reading quality.",
                )
            ],
        )
