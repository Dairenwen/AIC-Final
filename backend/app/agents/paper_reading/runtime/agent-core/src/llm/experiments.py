from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from schemas.models import PaperRecord


class EvidenceBoundFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=500)
    detail: str = Field(min_length=1, max_length=4000)
    chunk_ids: list[str] = Field(min_length=1)


class ExperimentFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["MAIN_RESULT", "ABLATION", "EFFICIENCY", "ROBUSTNESS", "OTHER"]
    content: str = Field(min_length=1, max_length=6000)
    chunk_ids: list[str] = Field(min_length=1)


class ConclusionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(min_length=1, max_length=4000)
    support_status: Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "UNCERTAIN"]
    reason: str = Field(min_length=1, max_length=4000)
    chunk_ids: list[str] = Field(min_length=1)


class ReproducibilityAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code_availability: Literal["AVAILABLE", "UNAVAILABLE", "NOT_STATED"]
    data_availability: Literal["AVAILABLE", "UNAVAILABLE", "NOT_STATED"]
    hyperparameters: list[EvidenceBoundFact] = Field(default_factory=list)
    hardware_and_cost: list[EvidenceBoundFact] = Field(default_factory=list)
    training_details: list[EvidenceBoundFact] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class ExperimentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: list[EvidenceBoundFact] = Field(default_factory=list)
    baselines: list[EvidenceBoundFact] = Field(default_factory=list)
    metrics: list[EvidenceBoundFact] = Field(default_factory=list)
    findings: list[ExperimentFinding] = Field(default_factory=list)
    conclusion_assessments: list[ConclusionAssessment] = Field(default_factory=list)
    reproducibility: ReproducibilityAnalysis

    def all_chunk_ids(self) -> list[str]:
        values: list[str] = []
        for item in [*self.datasets, *self.baselines, *self.metrics]:
            values.extend(item.chunk_ids)
        for item in [*self.findings, *self.conclusion_assessments]:
            values.extend(item.chunk_ids)
        for item in [
            *self.reproducibility.hyperparameters,
            *self.reproducibility.hardware_and_cost,
            *self.reproducibility.training_details,
        ]:
            values.extend(item.chunk_ids)
        return values


OPTIONAL_REPRODUCIBILITY_EVIDENCE_FIELDS = (
    "hyperparameters",
    "hardware_and_cost",
    "training_details",
)


def discard_invalid_optional_evidence_items(
    payload: dict[str, Any],
    *,
    valid_chunk_ids: set[str] | None = None,
) -> tuple[dict[str, Any], int]:
    """Drop only optional reproducibility facts whose Evidence lineage is unusable.

    Core experiment facts retain their strict Pydantic constraints. This helper is
    intentionally limited to the three Evidence-bearing optional lists in the
    existing reproducibility model.
    """

    normalized = dict(payload)
    raw_reproducibility = normalized.get("reproducibility")
    if not isinstance(raw_reproducibility, dict):
        return normalized, 0

    reproducibility = dict(raw_reproducibility)
    discarded = 0
    for field in OPTIONAL_REPRODUCIBILITY_EVIDENCE_FIELDS:
        raw_items = reproducibility.get(field, [])
        if not isinstance(raw_items, list):
            continue
        kept: list[Any] = []
        for item in raw_items:
            chunk_ids = item.get("chunk_ids") if isinstance(item, dict) else None
            usable = (
                isinstance(chunk_ids, list)
                and bool(chunk_ids)
                and all(
                    isinstance(chunk_id, str) and chunk_id.strip()
                    for chunk_id in chunk_ids
                )
            )
            if usable and valid_chunk_ids is not None:
                usable = all(chunk_id in valid_chunk_ids for chunk_id in chunk_ids)
            if usable:
                kept.append(item)
            else:
                discarded += 1
        reproducibility[field] = kept
    normalized["reproducibility"] = reproducibility
    return normalized, discarded


class ExperimentAnalysisGateway(Protocol):
    def analyze_experiments(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> ExperimentAnalysis: ...
