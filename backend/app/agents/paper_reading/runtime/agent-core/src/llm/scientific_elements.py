from __future__ import annotations

import re
from math import isclose
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas.models import PaperRecord


ElementType = Literal["EQUATION", "FIGURE", "TABLE"]


class VariableExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=200)
    meaning: str = Field(min_length=1, max_length=2000)


class TableNumericCheck(BaseModel):
    """One auditable comparison reconstructed from explicit table cells."""

    model_config = ConfigDict(extra="forbid")

    check_type: Literal[
        "BEST_VALUE", "BASELINE_COMPARISON", "ABLATION_COMPARISON"
    ] = "BASELINE_COMPARISON"
    label_axis: Literal["ROW", "COLUMN"] = "ROW"
    metric: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=500)
    direction: Literal[
        "HIGHER_IS_BETTER", "LOWER_IS_BETTER", "NEUTRAL"
    ] = "NEUTRAL"
    baseline_label: str = Field(default="baseline", min_length=1, max_length=500)
    baseline_value: float
    target_label: str = Field(default="target", min_length=1, max_length=500)
    target_value: float
    absolute_difference: float
    relative_difference_percent: float | None = None

    @model_validator(mode="after")
    def calculations_are_consistent(self) -> "TableNumericCheck":
        if (
            self.target_label.casefold() == self.baseline_label.casefold()
            and isclose(self.target_value, self.baseline_value, rel_tol=1e-9, abs_tol=1e-9)
        ):
            raise ValueError("table check must compare distinct cells or values")
        expected_absolute = self.target_value - self.baseline_value
        if not isclose(self.absolute_difference, expected_absolute, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError("table absolute_difference must equal target_value - baseline_value")
        if self.relative_difference_percent is not None:
            if self.baseline_value == 0:
                raise ValueError("relative table difference is undefined for a zero baseline")
            expected_relative = expected_absolute / abs(self.baseline_value) * 100
            if not isclose(
                self.relative_difference_percent,
                expected_relative,
                rel_tol=1e-3,
                abs_tol=5e-2,
            ):
                raise ValueError("table relative_difference_percent is inconsistent")
        return self


class TableCellFact(BaseModel):
    """One factual numeric value bound to a single visible table cell."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=500)
    row_label: str = Field(min_length=1, max_length=500)
    column_header: str = Field(min_length=1, max_length=500)
    value: float = Field(allow_inf_nan=False)


class ScientificElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(min_length=3, max_length=128)
    element_type: ElementType
    label: str = Field(min_length=1, max_length=500)
    page: int = Field(ge=1)
    explanation: str = Field(min_length=1, max_length=8000)
    variables: list[VariableExplanation] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    table_checks: list[TableNumericCheck] = Field(default_factory=list)
    table_cell_facts: list[TableCellFact] = Field(default_factory=list)
    chunk_ids: list[str] = Field(min_length=1)
    needs_visual: bool
    visual_status: Literal["TEXT_ONLY", "VISION_VERIFIED", "VISION_NOT_CONFIRMED"] = "TEXT_ONLY"
    document_object_id: str | None = None


class ScientificElementAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[ScientificElement]

    @model_validator(mode="after")
    def element_ids_are_unique(self) -> "ScientificElementAnalysis":
        identifiers = [item.element_id for item in self.elements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("scientific element IDs must be unique")
        return self


class ScientificElementTarget(BaseModel):
    """One upstream-located paper object that the reading model must explain."""

    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1, max_length=256)
    element_type: ElementType
    label: str = Field(min_length=1, max_length=500)
    page: int = Field(ge=1)
    section_path: list[str]
    content: str = Field(min_length=1, max_length=20_000)


class VisualElementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str
    verification_status: Literal["VERIFIED", "NOT_VISIBLE", "UNCERTAIN"]
    explanation: str = Field(min_length=1, max_length=8000)
    variables: list[VariableExplanation] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    table_checks: list[TableNumericCheck] = Field(default_factory=list)
    table_cell_facts: list[TableCellFact] = Field(default_factory=list)


class PageVisualAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[VisualElementUpdate]


class TableCheckVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str
    check_index: int = Field(ge=0)
    verification_status: Literal["VERIFIED", "REJECTED", "UNCERTAIN"]
    reason: str = Field(min_length=1, max_length=2000)
    table_scope_text: str | None = Field(default=None, max_length=2000)
    target_row_label: str | None = None
    target_column_header: str | None = None
    target_cell_value: float | None = None
    baseline_row_label: str | None = None
    baseline_column_header: str | None = None
    baseline_cell_value: float | None = None


class TableCellFactVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str
    fact_index: int = Field(ge=0)
    verification_status: Literal["VERIFIED", "REJECTED", "UNCERTAIN"]
    reason: str = Field(min_length=1, max_length=2000)
    table_scope_text: str | None = Field(default=None, max_length=2000)
    row_label: str | None = None
    column_header: str | None = None
    cell_value: float | None = Field(default=None, allow_inf_nan=False)


class PageTableCheckVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[TableCheckVerification] = Field(default_factory=list)
    cell_facts: list[TableCellFactVerification] = Field(default_factory=list)


def normalize_table_checks_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep valid numeric checks while tolerating provider omissions in label fields."""

    normalized = dict(payload)
    normalized_elements: list[Any] = []
    for raw_element in payload.get("elements", []):
        if not isinstance(raw_element, dict):
            normalized_elements.append(raw_element)
            continue
        element = dict(raw_element)
        valid_checks: list[dict[str, Any]] = []
        valid_cell_facts: list[dict[str, Any]] = []
        for raw_check in element.get("table_checks", []):
            if not isinstance(raw_check, dict):
                continue
            candidate = dict(raw_check)
            scope = str(candidate.get("scope", ""))
            if " vs " in scope.casefold():
                labels = re.split(r"\s+vs\.?\s+", scope, maxsplit=1, flags=re.IGNORECASE)
                if len(labels) == 2:
                    candidate.setdefault("target_label", labels[0].strip())
                    candidate.setdefault("baseline_label", labels[1].strip())
            scope_lower = scope.casefold()
            if "ablation" in scope_lower or "消融" in scope:
                candidate.setdefault("check_type", "ABLATION_COMPARISON")
            candidate.setdefault("check_type", "BASELINE_COMPARISON")
            candidate.setdefault("label_axis", "ROW")
            candidate.setdefault("direction", "NEUTRAL")
            candidate.setdefault("target_label", "target")
            candidate.setdefault("baseline_label", "baseline")
            try:
                valid_checks.append(
                    TableNumericCheck.model_validate(candidate).model_dump(mode="json")
                )
            except (TypeError, ValueError):
                continue
        element["table_checks"] = valid_checks
        for raw_fact in element.get("table_cell_facts", []):
            if not isinstance(raw_fact, dict):
                continue
            try:
                valid_cell_facts.append(
                    TableCellFact.model_validate(raw_fact).model_dump(mode="json")
                )
            except (TypeError, ValueError):
                continue
        element["table_cell_facts"] = valid_cell_facts
        normalized_elements.append(element)
    normalized["elements"] = normalized_elements
    return normalized


def discard_invalid_scientific_evidence_items(
    payload: dict[str, Any],
    valid_chunk_ids: set[str],
) -> tuple[dict[str, Any], int]:
    """Discard optional scientific items whose Evidence lineage is empty or invalid."""
    normalized = dict(payload)
    retained: list[Any] = []
    discarded = 0
    for raw_element in payload.get("elements", []):
        if not isinstance(raw_element, dict):
            retained.append(raw_element)
            continue
        chunk_ids = raw_element.get("chunk_ids")
        if (
            not isinstance(chunk_ids, list)
            or not chunk_ids
            or any(
                not isinstance(chunk_id, str)
                or not chunk_id.strip()
                or chunk_id not in valid_chunk_ids
                for chunk_id in chunk_ids
            )
        ):
            discarded += 1
            continue
        retained.append(raw_element)
    normalized["elements"] = retained
    return normalized, discarded


class ScientificElementGateway(Protocol):
    def analyze_scientific_elements(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> ScientificElementAnalysis: ...


class TargetedScientificElementGateway(Protocol):
    def analyze_targeted_scientific_elements(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        targets: list[ScientificElementTarget],
        language: str,
    ) -> ScientificElementAnalysis: ...


class PageVisionGateway(Protocol):
    def analyze_page(
        self,
        *,
        page_number: int,
        image_png: bytes,
        elements: list[ScientificElement],
        page_context: list[dict[str, Any]],
        language: str,
    ) -> PageVisualAnalysis: ...

    def verify_table_checks(
        self,
        *,
        page_number: int,
        image_png: bytes,
        elements: list[ScientificElement],
        page_context: list[dict[str, Any]],
        language: str,
    ) -> PageTableCheckVerification: ...
