from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from math import isclose
from pathlib import Path
from typing import Literal

from llm.scientific_elements import (
    PageVisualAnalysis,
    PageVisionGateway,
    ScientificElement,
    ScientificElementAnalysis,
    ScientificElementGateway,
    ScientificElementTarget,
    TableCellFact,
    TableCellFactVerification,
    TableCheckVerification,
    TableNumericCheck,
    VisualElementUpdate,
)
from pydantic import BaseModel, ConfigDict, Field
from schemas.models import DocumentIR, EvidenceReference, KnowledgeChunk
from paper_context.ports import TableExtractorPort
from paper_context.table_extraction import TableExtractionError

from .concurrency import run_concurrently
from .context import PreparedReadingContext
from .numeric_relations import NumericRelationGuard
from .page_renderer import PageRenderer
from .planning import ReadingTaskType, scientific_object_importance
from .reliability import ClaimEvidenceReliabilityGuard, ReliabilityRecord
from .table_labels import table_label_matches, table_tokens


ScientificCoverageMode = Literal["KEY", "COMPREHENSIVE", "SELECTED"]
ScientificCoverageStatus = Literal[
    "ANALYZED_TEXT",
    "VISION_VERIFIED",
    "VISION_NOT_CONFIRMED",
    "NOT_ANALYZED",
    "NOT_REQUESTED",
]


class ScientificCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    element_type: Literal["EQUATION", "FIGURE", "TABLE"]
    label: str
    page: int
    rank_within_type: int
    importance_score: int
    ranking_reasons: list[str]


class ScientificObjectCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    element_type: Literal["EQUATION", "FIGURE", "TABLE"]
    label: str
    page: int
    section_path: list[str]
    requested: bool
    status: ScientificCoverageStatus
    analyzed_element_id: str | None = None


TableEvidenceRejectionCode = Literal[
    "VERIFIER_UNAVAILABLE",
    "VERIFICATION_MISSING",
    "VERIFIER_REJECTED",
    "CELL_PROOF_UNCERTAIN",
    "SEMANTIC_CONTRADICTION",
    "INCOMPLETE_CELL_PROOF",
    "CELL_VALUE_MISMATCH",
    "LABEL_SCOPE_AXIS_MISMATCH",
    "VISUAL_NOT_CONFIRMED",
]


class TableEvidenceRejection(BaseModel):
    """One bounded audit record for proposed table evidence that was not accepted."""

    model_config = ConfigDict(extra="forbid")

    element_id: str
    evidence_type: Literal["NUMERIC_COMPARISON", "CELL_FACT"]
    item_index: int = Field(ge=0)
    reason_code: TableEvidenceRejectionCode
    safe_message: str = Field(min_length=1, max_length=500)
    # Optional only for historical deep_reading_report_v1 rejection records.
    # New rejections always populate both fields and also emit an audit record.
    proposal_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    proposal: TableNumericCheck | TableCellFact | None = None
    proof: TableCheckVerification | TableCellFactVerification | None = None


class TableEvidenceAudit(BaseModel):
    """One accepted or rejected table-evidence decision with bounded proof."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["table_evidence_audit_v1"] = "table_evidence_audit_v1"
    element_id: str
    evidence_type: Literal["NUMERIC_COMPARISON", "CELL_FACT"]
    item_index: int = Field(ge=0)
    decision: Literal["ACCEPTED", "REJECTED"]
    reason_code: Literal["VERIFIED"] | TableEvidenceRejectionCode
    safe_message: str = Field(min_length=1, max_length=500)
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal: TableNumericCheck | TableCellFact
    proof: TableCheckVerification | TableCellFactVerification | None = None
    page_image_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    verifier_id: str | None = Field(default=None, min_length=1, max_length=500)


class ScientificCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ScientificCoverageMode
    detected_count: int
    requested_count: int
    analyzed_count: int
    requested_analyzed_count: int
    vision_verified_count: int
    rejected_table_check_count: int
    rejected_table_cell_fact_count: int = 0
    table_evidence_rejections: list[TableEvidenceRejection] = Field(default_factory=list)
    table_evidence_audits: list[TableEvidenceAudit] = Field(default_factory=list)
    candidates: list[ScientificCandidate]
    objects: list[ScientificObjectCoverage]


@dataclass(frozen=True)
class ScientificElementsOutput:
    analysis: ScientificElementAnalysis
    evidence: tuple[EvidenceReference, ...]
    markdown: str
    visual_page_count: int
    detected_object_count: int
    unanalyzed_labels: tuple[str, ...]
    coverage: ScientificCoverageReport
    rejected_table_check_count: int
    rejected_table_cell_fact_count: int = 0
    reliability_records: tuple[ReliabilityRecord, ...] = ()


class FormulaFigureAnalysisAgent:
    """Explain scientific elements from text first, then verify selected pages visually."""

    def __init__(
        self,
        text_gateway: ScientificElementGateway,
        *,
        vision_gateway: PageVisionGateway | None = None,
        page_renderer: PageRenderer | None = None,
        max_visual_pages: int = 4,
        max_concurrent_visual_calls: int = 8,
        target_batch_size: int = 4,
        max_concurrent_text_calls: int = 8,
        key_elements_per_type: int = 2,
        table_extractor: TableExtractorPort | None = None,
    ) -> None:
        if min(
            max_visual_pages,
            max_concurrent_visual_calls,
            target_batch_size,
            max_concurrent_text_calls,
            key_elements_per_type,
        ) < 1:
            raise ValueError("scientific-element limits must be positive")
        if vision_gateway is not None and page_renderer is None:
            raise ValueError("a page renderer is required when a vision gateway is configured")
        self.text_gateway = text_gateway
        self.vision_gateway = vision_gateway
        self.page_renderer = page_renderer
        self.numeric_relation_guard = NumericRelationGuard()
        self.reliability_guard = ClaimEvidenceReliabilityGuard()
        self.max_visual_pages = max_visual_pages
        self.max_concurrent_visual_calls = max_concurrent_visual_calls
        self.target_batch_size = target_batch_size
        self.max_concurrent_text_calls = max_concurrent_text_calls
        self.key_elements_per_type = key_elements_per_type
        self.table_extractor = table_extractor

    def analyze(
        self,
        reading: PreparedReadingContext,
        pdf_path: str | Path | None = None,
        *,
        coverage_mode: ScientificCoverageMode = "KEY",
        max_scientific_elements: int | None = None,
        selected_object_ids: list[str] | None = None,
    ) -> ScientificElementsOutput:
        if coverage_mode not in {"KEY", "COMPREHENSIVE", "SELECTED"}:
            raise ValueError("coverage_mode must be KEY, COMPREHENSIVE, or SELECTED")
        if max_scientific_elements is not None and max_scientific_elements < 1:
            raise ValueError("max_scientific_elements must be positive")
        if coverage_mode == "SELECTED" and not selected_object_ids:
            raise ValueError("SELECTED coverage requires selected_object_ids")
        if coverage_mode != "SELECTED" and selected_object_ids:
            raise ValueError("selected_object_ids can only be used with SELECTED coverage")
        routed_chunks = list(reading.chunks_for_task(ReadingTaskType.SCIENTIFIC_ELEMENTS))
        if not routed_chunks:
            routed_chunks = list(
                reading.context_router.route(
                    ReadingTaskType.SCIENTIFIC_ELEMENTS,
                    reading.request,
                    reading.chunks,
                    reading.document_ir,
                ).chunks
            )
        context = [chunk.model_dump(mode="json") for chunk in routed_chunks]
        initial = (
            ScientificElementAnalysis(elements=[])
            if coverage_mode == "SELECTED"
            else self.text_gateway.analyze_scientific_elements(
                reading.paper,
                context,
                reading.request.language,
            )
        )
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        elements = [
            self._align_caption_location(item, reading.chunks, reading.document_ir)
            for item in initial.elements
        ]
        self._validate_elements(elements, reading)

        targets = self._located_targets(reading.document_ir)
        candidates = self._rank_targets(reading, targets)
        target_by_id = {target.object_id: target for target in targets}
        if coverage_mode == "KEY":
            planned_object_ids = set(
                reading.reading_plan.task(
                    ReadingTaskType.SCIENTIFIC_ELEMENTS
                ).selected_object_ids
            )
            requested_targets = [
                target_by_id[candidate.object_id]
                for candidate in candidates
                if (
                    candidate.object_id in planned_object_ids
                    if planned_object_ids
                    else candidate.rank_within_type <= self.key_elements_per_type
                )
            ]
        elif coverage_mode == "COMPREHENSIVE":
            requested_targets = [target_by_id[item.object_id] for item in candidates]
            if max_scientific_elements is not None:
                requested_targets = requested_targets[:max_scientific_elements]
        else:
            unknown_ids = set(selected_object_ids or []) - set(target_by_id)
            if unknown_ids:
                raise ValueError(
                    "selected scientific object was not found: " + ", ".join(sorted(unknown_ids))
                )
            requested_targets = [target_by_id[object_id] for object_id in selected_object_ids or []]

        covered_object_ids = self._covered_object_ids(elements, targets)
        missing_targets = [
            target for target in requested_targets if target.object_id not in covered_object_ids
        ]
        targeted_analyzer = getattr(
            self.text_gateway,
            "analyze_targeted_scientific_elements",
            None,
        )
        if missing_targets and targeted_analyzer is None:
            raise ValueError(
                f"{coverage_mode} coverage requires a targeted scientific-element gateway"
            )
        batches = [
            missing_targets[index : index + self.target_batch_size]
            for index in range(0, len(missing_targets), self.target_batch_size)
        ]

        def analyze_batch(batch: list[ScientificElementTarget]) -> ScientificElementAnalysis:
            batch_chunks = self._target_chunks(reading, batch)
            return targeted_analyzer(
                reading.paper,
                [chunk.model_dump(mode="json") for chunk in batch_chunks],
                batch,
                reading.request.language,
            )

        targeted_results = run_concurrently(
            {
                f"batch_{index:04d}": lambda batch=batch: analyze_batch(batch)
                for index, batch in enumerate(batches, start=1)
            },
            max_workers=self.max_concurrent_text_calls,
        )
        for result in targeted_results.values():
            elements.extend(
                self._align_caption_location(item, reading.chunks, reading.document_ir)
                for item in result.elements
            )
        elements = self._deduplicate_elements(elements, targets)
        self._validate_elements(elements, reading)

        elements = self._ensure_unique_element_ids(elements)
        text_only_by_id = {item.element_id: item for item in elements}
        proven_table_check_ids: set[str] = set()
        proven_table_cell_fact_ids: set[str] = set()
        rejected_table_checks_by_id: dict[str, list[TableNumericCheck]] = {}
        rejected_table_cell_facts_by_id: dict[str, list[TableCellFact]] = {}
        table_evidence_rejections: list[TableEvidenceRejection] = []
        table_evidence_audits: list[TableEvidenceAudit] = []
        page_image_sha256_by_page: dict[int, str] = {}
        discarded_visual_proposals: list[
            tuple[
                str,
                int,
                Literal["NUMERIC_COMPARISON", "CELL_FACT"],
                int,
                TableNumericCheck | TableCellFact,
            ]
        ] = []
        if self.vision_gateway is not None and pdf_path is not None:
            elements = [
                item.model_copy(update={"needs_visual": True})
                if item.element_type in {"FIGURE", "TABLE"}
                else item
                for item in elements
            ]

        visual_pages: list[int] = []
        rejected_table_check_count = 0
        rejected_table_cell_fact_count = 0
        if self.vision_gateway is not None and pdf_path is not None:
            visual_pages = sorted({item.page for item in elements if item.needs_visual})[
                : self.max_visual_pages
            ]
            if visual_pages:
                assert self.page_renderer is not None
                targets_by_page = {
                    page: [
                        item.label
                        for item in elements
                        if item.page == page and item.needs_visual
                    ]
                    for page in visual_pages
                }
                selected_table_ids = {
                    item.document_object_id
                    for item in elements
                    if item.page in visual_pages
                    and item.element_type == "TABLE"
                    and item.document_object_id is not None
                }
                table_context_by_page = self._table_context_by_page(
                    reading,
                    pdf_path,
                    visual_pages,
                    selected_table_ids=selected_table_ids,
                )
                table_regions_by_page = {
                    page: [
                        (
                            grid["table_bbox"]["x0"],
                            grid["table_bbox"]["y0"],
                            grid["table_bbox"]["x1"],
                            grid["table_bbox"]["y1"],
                        )
                        for item in table_context_by_page.get(page, [])
                        if isinstance((grid := item.get("table_grid")), dict)
                    ]
                    for page in visual_pages
                    if all(
                        item.element_type == "TABLE"
                        for item in elements
                        if item.page == page and item.needs_visual
                    )
                }
                table_regions_by_page = {
                    page: regions
                    for page, regions in table_regions_by_page.items()
                    if regions
                }
                region_renderer = getattr(
                    self.page_renderer,
                    "render_target_regions",
                    None,
                )
                target_renderer = getattr(self.page_renderer, "render_target_pages", None)
                images = (
                    region_renderer(
                        pdf_path,
                        targets_by_page,
                        table_regions_by_page,
                    )
                    if region_renderer is not None and table_regions_by_page
                    else target_renderer(pdf_path, targets_by_page)
                    if target_renderer is not None
                    else self.page_renderer.render_pages(pdf_path, visual_pages)
                )
                page_image_sha256_by_page = {
                    page: sha256(image).hexdigest()
                    for page, image in images.items()
                }
                page_elements_by_page = {
                    page: [
                        item for item in elements if item.page == page and item.needs_visual
                    ]
                    for page in visual_pages
                }
                page_context_by_page = {
                    page: [
                        {
                            "object_id": block.object_id,
                            "page": block.page_number,
                            "section": block.section_path,
                            "text": block.text,
                        }
                        for block in reading.document_ir.text_blocks
                        if block.page_number == page
                    ] + table_context_by_page.get(page, [])
                    for page in visual_pages
                }

                def analyze_visual_page(page: int):
                    assert self.vision_gateway is not None
                    try:
                        return self.vision_gateway.analyze_page(
                            page_number=page,
                            image_png=images[page],
                            elements=page_elements_by_page[page],
                            page_context=page_context_by_page[page],
                            language=reading.request.language,
                        )
                    except Exception:
                        return PageVisualAnalysis(
                            elements=[
                                VisualElementUpdate(
                                    element_id=item.element_id,
                                    verification_status="UNCERTAIN",
                                    explanation="Visual verification did not return a valid result.",
                                    variables=[],
                                    findings=[],
                                    table_checks=[],
                                )
                                for item in page_elements_by_page[page]
                            ]
                        )

                visual_results = run_concurrently(
                    {
                        str(page): lambda page=page: analyze_visual_page(page)
                        for page in visual_pages
                    },
                    max_workers=self.max_concurrent_visual_calls,
                )
                by_id = {item.element_id: item for item in elements}
                for page in visual_pages:
                    visual = visual_results[str(page)]
                    returned_ids: set[str] = set()
                    for update in visual.elements:
                        current = by_id[update.element_id]
                        returned_ids.add(update.element_id)
                        if (
                            update.verification_status == "VERIFIED"
                            and not self._table_visual_uses_inference(current, update)
                        ):
                            by_id[update.element_id] = current.model_copy(
                                update={
                                    "explanation": update.explanation,
                                    "variables": update.variables or current.variables,
                                    "findings": self._safe_visual_findings(current, update),
                                    "table_checks": update.table_checks or current.table_checks,
                                    "table_cell_facts": (
                                        update.table_cell_facts
                                        or current.table_cell_facts
                                    ),
                                    "visual_status": "VISION_VERIFIED",
                                }
                            )
                        else:
                            if current.element_type == "TABLE":
                                discarded_visual_proposals.extend(
                                    (
                                        current.element_id,
                                        current.page,
                                        "NUMERIC_COMPARISON",
                                        index,
                                        proposal,
                                    )
                                    for index, proposal in enumerate(update.table_checks)
                                )
                                discarded_visual_proposals.extend(
                                    (
                                        current.element_id,
                                        current.page,
                                        "CELL_FACT",
                                        index,
                                        proposal,
                                    )
                                    for index, proposal in enumerate(
                                        update.table_cell_facts
                                    )
                                )
                            by_id[update.element_id] = current.model_copy(
                                update={"visual_status": "VISION_NOT_CONFIRMED"}
                            )
                    for current in page_elements_by_page[page]:
                        if current.element_id not in returned_ids:
                            by_id[current.element_id] = current.model_copy(
                                update={"visual_status": "VISION_NOT_CONFIRMED"}
                            )
                table_check_verifier = getattr(
                    self.vision_gateway,
                    "verify_table_checks",
                    None,
                )
                verification_pages = [
                    page
                    for page in visual_pages
                    if any(
                        item.element_type == "TABLE"
                        and item.visual_status
                        in {"VISION_VERIFIED", "VISION_NOT_CONFIRMED"}
                        and (item.table_checks or item.table_cell_facts)
                        for item in by_id.values()
                        if item.page == page
                    )
                ]
                if verification_pages:
                    def verify_table_page(page: int):
                        page_tables = [
                            item
                            for item in by_id.values()
                            if item.page == page
                            and item.element_type == "TABLE"
                            and item.visual_status
                            in {"VISION_VERIFIED", "VISION_NOT_CONFIRMED"}
                            and (item.table_checks or item.table_cell_facts)
                        ]
                        if table_check_verifier is None:
                            return None
                        try:
                            return table_check_verifier(
                                page_number=page,
                                image_png=images[page],
                                elements=page_tables,
                                page_context=page_context_by_page[page],
                                language=reading.request.language,
                            )
                        except Exception:
                            return None

                    verification_results = run_concurrently(
                        {
                            str(page): lambda page=page: verify_table_page(page)
                            for page in verification_pages
                        },
                        max_workers=self.max_concurrent_visual_calls,
                    )
                    for page in verification_pages:
                        verification = verification_results[str(page)]
                        page_image_sha256 = page_image_sha256_by_page[page]
                        verifier_id = self._table_verifier_id()
                        verified_axes: dict[tuple[str, int], Literal["ROW", "COLUMN"]] = {}
                        verified_cell_facts: set[tuple[str, int]] = set()
                        verification_checks = {
                            (item.element_id, item.check_index): item
                            for item in verification.checks
                        } if verification is not None else {}
                        verification_facts = {
                            (item.element_id, item.fact_index): item
                            for item in verification.cell_facts
                        } if verification is not None else {}
                        if verification is not None:
                            for item in verification.checks:
                                if item.verification_status != "VERIFIED":
                                    continue
                                axis = self._table_check_verification_axis(
                                    by_id[item.element_id].table_checks[item.check_index],
                                    item,
                                )
                                if axis is not None:
                                    verified_axes[(item.element_id, item.check_index)] = axis
                            for item in verification.cell_facts:
                                if item.verification_status != "VERIFIED":
                                    continue
                                fact = by_id[item.element_id].table_cell_facts[
                                    item.fact_index
                                ]
                                if self._table_cell_fact_verification_matches(fact, item):
                                    verified_cell_facts.add(
                                        (item.element_id, item.fact_index)
                                    )
                        for current in list(by_id.values()):
                            if (
                                current.page != page
                                or current.element_type != "TABLE"
                                or current.visual_status
                                not in {"VISION_VERIFIED", "VISION_NOT_CONFIRMED"}
                                or not (
                                    current.table_checks
                                    or current.table_cell_facts
                                )
                            ):
                                continue
                            accepted = [
                                check.model_copy(
                                    update={
                                        "label_axis": verified_axes[
                                            (current.element_id, index)
                                        ]
                                    }
                                )
                                for index, check in enumerate(current.table_checks)
                                if (current.element_id, index) in verified_axes
                            ]
                            rejected_table_checks_by_id[current.element_id] = [
                                check
                                for index, check in enumerate(current.table_checks)
                                if (current.element_id, index) not in verified_axes
                            ]
                            rejected_table_check_count += len(current.table_checks) - len(accepted)
                            for index, check in enumerate(current.table_checks):
                                proof = verification_checks.get(
                                    (current.element_id, index)
                                )
                                if (current.element_id, index) in verified_axes:
                                    table_evidence_audits.append(
                                        self._accepted_table_evidence_audit(
                                            current.element_id,
                                            index,
                                            check,
                                            proof,
                                            page_image_sha256=page_image_sha256,
                                            verifier_id=verifier_id,
                                        )
                                    )
                                else:
                                    rejection = self._table_check_rejection(
                                        current.element_id,
                                        index,
                                        proof,
                                        verification_available=verification is not None,
                                        check=check,
                                    )
                                    table_evidence_rejections.append(rejection)
                                    table_evidence_audits.append(
                                        self._rejected_table_evidence_audit(
                                            rejection,
                                            page_image_sha256=page_image_sha256,
                                            verifier_id=verifier_id,
                                        )
                                    )
                            accepted_cell_facts = [
                                fact
                                for index, fact in enumerate(current.table_cell_facts)
                                if (current.element_id, index) in verified_cell_facts
                            ]
                            rejected_table_cell_facts_by_id[current.element_id] = [
                                fact
                                for index, fact in enumerate(current.table_cell_facts)
                                if (current.element_id, index) not in verified_cell_facts
                            ]
                            rejected_table_cell_fact_count += (
                                len(current.table_cell_facts)
                                - len(accepted_cell_facts)
                            )
                            for index, fact in enumerate(current.table_cell_facts):
                                proof = verification_facts.get(
                                    (current.element_id, index)
                                )
                                if (current.element_id, index) in verified_cell_facts:
                                    table_evidence_audits.append(
                                        self._accepted_table_evidence_audit(
                                            current.element_id,
                                            index,
                                            fact,
                                            proof,
                                            page_image_sha256=page_image_sha256,
                                            verifier_id=verifier_id,
                                        )
                                    )
                                else:
                                    rejection = self._table_cell_fact_rejection(
                                        current.element_id,
                                        index,
                                        proof,
                                        verification_available=verification is not None,
                                        fact=fact,
                                    )
                                    table_evidence_rejections.append(rejection)
                                    table_evidence_audits.append(
                                        self._rejected_table_evidence_audit(
                                            rejection,
                                            page_image_sha256=page_image_sha256,
                                            verifier_id=verifier_id,
                                        )
                                    )
                            update = {
                                "table_checks": accepted,
                                "table_cell_facts": accepted_cell_facts,
                                # Independent same-page cell proof can recover a
                                # table whose first qualitative visual pass was
                                # uncertain. No proof means the original status
                                # remains fail-closed.
                                "visual_status": (
                                    "VISION_VERIFIED"
                                    if accepted or accepted_cell_facts
                                    else current.visual_status
                                ),
                            }
                            if accepted:
                                proven_table_check_ids.add(current.element_id)
                            if accepted_cell_facts:
                                proven_table_cell_fact_ids.add(current.element_id)
                            by_id[current.element_id] = current.model_copy(update=update)
                elements = [by_id[item.element_id] for item in elements]

        audited_proposals = {
            (item.element_id, item.evidence_type, item.proposal_sha256)
            for item in table_evidence_audits
        }
        final_elements_by_id = {item.element_id: item for item in elements}
        for (
            element_id,
            page,
            evidence_type,
            index,
            proposal,
        ) in discarded_visual_proposals:
            proposal_sha256 = self._table_proposal_sha256(proposal)
            audit_key = (element_id, evidence_type, proposal_sha256)
            if audit_key in audited_proposals:
                continue
            final_element = final_elements_by_id[element_id]
            item_index = (
                len(final_element.table_checks)
                if evidence_type == "NUMERIC_COMPARISON"
                else len(final_element.table_cell_facts)
            ) + index
            rejection = TableEvidenceRejection(
                element_id=element_id,
                evidence_type=evidence_type,
                item_index=item_index,
                reason_code="VISUAL_NOT_CONFIRMED",
                safe_message=(
                    "The visual proposal was rejected because the table was not "
                    "confirmed without inference."
                ),
                proposal_sha256=proposal_sha256,
                proposal=proposal,
                proof=None,
            )
            table_evidence_rejections.append(rejection)
            table_evidence_audits.append(
                self._rejected_table_evidence_audit(
                    rejection,
                    page_image_sha256=page_image_sha256_by_page.get(page),
                    verifier_id=self._table_verifier_id(),
                )
            )
            audited_proposals.add(audit_key)
            if evidence_type == "NUMERIC_COMPARISON":
                rejected_table_check_count += 1
            else:
                rejected_table_cell_fact_count += 1
        for item in elements:
            if item.element_type != "TABLE":
                continue
            for evidence_type, proposals in (
                ("NUMERIC_COMPARISON", item.table_checks),
                ("CELL_FACT", item.table_cell_facts),
            ):
                for index, proposal in enumerate(proposals):
                    proposal_sha256 = self._table_proposal_sha256(proposal)
                    audit_key = (item.element_id, evidence_type, proposal_sha256)
                    if audit_key in audited_proposals:
                        continue
                    rejection = TableEvidenceRejection(
                        element_id=item.element_id,
                        evidence_type=evidence_type,
                        item_index=index,
                        reason_code="VISUAL_NOT_CONFIRMED",
                        safe_message=(
                            "The table was not visually confirmed, so the proposed "
                            "evidence was rejected before cell verification."
                        ),
                        proposal_sha256=proposal_sha256,
                        proposal=proposal,
                        proof=None,
                    )
                    table_evidence_rejections.append(rejection)
                    table_evidence_audits.append(
                        self._rejected_table_evidence_audit(
                            rejection,
                            page_image_sha256=page_image_sha256_by_page.get(item.page),
                            verifier_id=self._table_verifier_id(),
                        )
                    )
                    audited_proposals.add(audit_key)
                    if evidence_type == "NUMERIC_COMPARISON":
                        rejected_table_checks_by_id.setdefault(
                            item.element_id,
                            [],
                        ).append(proposal)
                        rejected_table_check_count += 1
                    else:
                        rejected_table_cell_facts_by_id.setdefault(
                            item.element_id,
                            [],
                        ).append(proposal)
                        rejected_table_cell_fact_count += 1

        elements = [
            self._finalize_table_visual_result(
                item,
                text_only=text_only_by_id[item.element_id],
                checks_proven=item.element_id in proven_table_check_ids,
                rejected_checks=rejected_table_checks_by_id.get(item.element_id, []),
                cell_facts_proven=(
                    item.element_id in proven_table_cell_fact_ids
                ),
                rejected_cell_facts=rejected_table_cell_facts_by_id.get(
                    item.element_id,
                    [],
                ),
            )
            for item in elements
        ]
        analysis = self.numeric_relation_guard.sanitize_scientific_analysis(
            ScientificElementAnalysis(elements=elements)
        )
        analysis = ScientificElementAnalysis(
            elements=[self._sanitize_configuration_element(item) for item in analysis.elements]
        )
        analysis, reliability_records = self.reliability_guard.consolidate_scientific(
            analysis,
            {chunk_id: chunk.text for chunk_id, chunk in chunk_by_id.items()},
        )
        evidence, evidence_by_chunk = self._evidence(reading, analysis)
        coverage = self._coverage(
            coverage_mode,
            targets,
            {target.object_id for target in requested_targets},
            candidates,
            analysis,
            rejected_table_check_count,
            rejected_table_cell_fact_count,
            table_evidence_rejections,
            table_evidence_audits,
        )
        unanalyzed = tuple(
            item.label for item in coverage.objects if item.status == "NOT_ANALYZED"
        )
        return ScientificElementsOutput(
            analysis=analysis,
            evidence=tuple(evidence),
            markdown=self._render(
                analysis,
                chunk_by_id,
                evidence_by_chunk,
                coverage,
                unanalyzed,
            ),
            visual_page_count=len(visual_pages),
            detected_object_count=coverage.detected_count,
            unanalyzed_labels=unanalyzed,
            coverage=coverage,
            rejected_table_check_count=rejected_table_check_count,
            rejected_table_cell_fact_count=rejected_table_cell_fact_count,
            reliability_records=reliability_records,
        )

    def _table_context_by_page(
        self,
        reading: PreparedReadingContext,
        pdf_path: str | Path,
        visual_pages: list[int],
        *,
        selected_table_ids: set[str] | None = None,
    ) -> dict[int, list[dict]]:
        if self.table_extractor is None:
            return {}
        try:
            pdf_bytes = Path(pdf_path).read_bytes()
            report = self.table_extractor.extract(
                paper_id=reading.paper.paper_id,
                pdf_bytes=pdf_bytes,
                document_ir=reading.document_ir,
                source_pdf_sha256=reading.paper.content_sha256,
            )
        except (OSError, TableExtractionError):
            return {}
        selected_pages = set(visual_pages)
        context: dict[int, list[dict]] = {}
        for item in report.results:
            grid = item.grid
            if grid is None or grid.page_number not in selected_pages:
                continue
            if selected_table_ids is not None and grid.table_id not in selected_table_ids:
                continue
            context.setdefault(grid.page_number, []).append(
                {
                    "object_id": grid.table_id,
                    "page": grid.page_number,
                    "section": ["STRUCTURED_TABLE_CANDIDATE"],
                    "text": (
                        f"{grid.label}: Docling/PyMuPDF candidate grid "
                        f"{grid.row_count}x{grid.column_count}"
                    ),
                    "table_grid": grid.model_dump(mode="json"),
                }
            )
        return context

    @staticmethod
    def _table_visual_uses_inference(
        element: ScientificElement,
        update: VisualElementUpdate,
    ) -> bool:
        if element.element_type != "TABLE":
            return False
        value = " ".join([update.explanation, *update.findings]).casefold()
        inference_markers = (
            "推断",
            "推测",
            "估算",
            "未列出",
            "缺失",
            "不可见",
            "无法确认",
            "实际对比应为",
            "更正",
            "自我修正",
            "infer",
            "assum",
            "estimate",
            "not listed",
            "missing cell",
            "not visible",
            "unreadable",
            "actual comparison",
            "correction",
        )
        if any(marker in value for marker in inference_markers):
            return True
        return any(
            FormulaFigureAnalysisAgent._is_numeric_comparison(finding)
            for finding in update.findings
        ) and not update.table_checks

    @staticmethod
    def _is_numeric_comparison(value: str) -> bool:
        lowered = value.casefold()
        comparison_markers = (
            "高于",
            "低于",
            "比",
            "提升",
            "下降",
            "差值",
            "超过",
            "超越",
            "优于",
            "更高",
            "更低",
            "目标",
            "基线",
            " vs ",
            "higher",
            "lower",
            "improv",
            "gain",
            "difference",
            "outperform",
            "surpass",
            "target",
            "baseline",
        )
        values = re.findall(r"(?<![A-Za-z_])\d+(?:\.\d+)?%?", value)
        decimal_or_percent = any("." in item or "%" in item for item in values)
        return (
            len(values) >= 2
            and decimal_or_percent
            and any(marker in lowered for marker in comparison_markers)
        )

    @classmethod
    def _safe_visual_findings(
        cls,
        current: ScientificElement,
        update: VisualElementUpdate,
    ) -> list[str]:
        findings = update.findings or current.findings
        if current.element_type != "TABLE":
            return findings
        return [
            finding
            for finding in findings
            if not (update.table_checks and cls._is_numeric_comparison(finding))
            and not (
                update.table_cell_facts
                and cls._unconfirmed_table_fact_text_is_unsafe(
                    finding,
                    update.table_cell_facts,
                )
            )
            and not cls._configuration_table_claim_is_causal(
                current,
                finding,
                extra_context=update.explanation,
            )
        ]

    @classmethod
    def _finalize_table_visual_result(
        cls,
        element: ScientificElement,
        *,
        text_only: ScientificElement | None = None,
        checks_proven: bool = False,
        rejected_checks: list[TableNumericCheck] | None = None,
        cell_facts_proven: bool = False,
        rejected_cell_facts: list[TableCellFact] | None = None,
    ) -> ScientificElement:
        """Apply the table status invariant once, immediately before final assembly."""

        if element.element_type != "TABLE":
            return element

        fallback = text_only or element
        rejected = list(rejected_checks or [])
        rejected_facts = list(rejected_cell_facts or [])
        checks_resolved = (
            not element.table_checks
            or checks_proven
            or rejected
        )
        cell_facts_resolved = (
            not element.table_cell_facts
            or cell_facts_proven
            or rejected_facts
        )
        if (
            element.visual_status == "VISION_VERIFIED"
            and checks_resolved
            and cell_facts_resolved
        ):
            if not rejected and not rejected_facts:
                return element
            explanation = element.explanation
            if (
                cls._unconfirmed_table_text_is_unsafe(
                    explanation,
                    rejected,
                    allow_visual_confirmation=True,
                )
                or cls._unconfirmed_table_fact_text_is_unsafe(
                    explanation,
                    rejected_facts,
                )
            ):
                explanation = fallback.explanation
                if (
                    cls._unconfirmed_table_text_is_unsafe(
                        explanation,
                        rejected,
                        allow_visual_confirmation=True,
                    )
                    or cls._unconfirmed_table_fact_text_is_unsafe(
                        explanation,
                        rejected_facts,
                    )
                ):
                    explanation = cls._neutral_unconfirmed_table_explanation()
            findings = [
                finding
                for finding in element.findings
                if not (
                    cls._unconfirmed_table_text_is_unsafe(
                        finding,
                        rejected,
                        allow_visual_confirmation=True,
                    )
                    or cls._unconfirmed_table_fact_text_is_unsafe(
                        finding,
                        rejected_facts,
                    )
                )
            ]
            if not findings:
                findings = [
                    finding
                    for finding in fallback.findings
                    if not (
                        cls._unconfirmed_table_text_is_unsafe(
                            finding,
                            rejected,
                            allow_visual_confirmation=True,
                        )
                        or cls._unconfirmed_table_fact_text_is_unsafe(
                            finding,
                            rejected_facts,
                        )
                    )
                ]
            return element.model_copy(
                update={
                    "explanation": explanation,
                    "findings": findings,
                    "table_checks": element.table_checks if checks_proven else [],
                    "table_cell_facts": (
                        element.table_cell_facts if cell_facts_proven else []
                    ),
                }
            )

        unconfirmed_checks = [
            *element.table_checks,
            *fallback.table_checks,
            *rejected,
        ]
        explanation = fallback.explanation
        if cls._unconfirmed_table_text_is_unsafe(explanation, unconfirmed_checks):
            explanation = cls._neutral_unconfirmed_table_explanation()
        findings = [
            finding
            for finding in fallback.findings
            if not cls._unconfirmed_table_text_is_unsafe(finding, unconfirmed_checks)
        ]
        return element.model_copy(
            update={
                "explanation": explanation,
                "variables": fallback.variables,
                "findings": findings,
                "table_checks": [],
                "table_cell_facts": [],
                "visual_status": element.visual_status,
            }
        )

    @classmethod
    def _unconfirmed_table_text_is_unsafe(
        cls,
        value: str,
        checks: list[TableNumericCheck],
        *,
        allow_visual_confirmation: bool = False,
    ) -> bool:
        lowered = value.casefold()
        visual_confirmation_markers = (
            "视觉确认",
            "视觉核验确认",
            "经视觉核验",
            "单元格确认",
            "visually confirmed",
            "vision verified",
            "cell-verified",
        )
        if not allow_visual_confirmation and any(
            marker in lowered for marker in visual_confirmation_markers
        ):
            return True
        comparison_markers = (
            "高于",
            "低于",
            "比",
            "提升",
            "下降",
            "差值",
            "超过",
            "超越",
            "优于",
            "更高",
            "更低",
            "最佳",
            "最优",
            " vs ",
            "higher",
            "lower",
            "improv",
            "gain",
            "difference",
            "outperform",
            "better",
            "best",
        )
        has_comparison = any(marker in lowered for marker in comparison_markers)
        has_number = bool(
            re.search(
                r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:\s*(?:e|[×x]\s*10\^?)\s*[-+]?\d+)?%?",
                value,
                re.IGNORECASE,
            )
        )
        return has_comparison and (has_number or bool(checks))

    @classmethod
    def _unconfirmed_table_fact_text_is_unsafe(
        cls,
        value: str,
        facts: list[TableCellFact],
    ) -> bool:
        if not facts:
            return False
        parsed_values = cls._table_numeric_values(value)
        lowered = value.casefold()
        for fact in facts:
            value_matches = any(
                isclose(item, fact.value, rel_tol=1e-6, abs_tol=1e-6)
                for item in parsed_values
            )
            label_matches = (
                fact.row_label.casefold() in lowered
                or fact.column_header.casefold() in lowered
                or fact.metric.casefold() in lowered
            )
            if value_matches and label_matches:
                return True
        return False

    @staticmethod
    def _table_numeric_values(value: str) -> list[float]:
        superscript_translation = str.maketrans(
            "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻",
            "0123456789+-",
        )
        parsed_values: list[float] = []
        for match in re.finditer(
            r"(?<![A-Za-z_])"
            r"([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
            r"(?:\s*(?:"
            r"e\s*([-+]?\d+)"
            r"|[×x]\s*10(?:"
            r"(?:\s*\^\s*|\s+)([-+]?\d+)"
            r"|\s*([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)"
            r")"
            r"))?",
            value,
            re.IGNORECASE,
        ):
            base = float(match.group(1).replace(",", ""))
            exponent_text = match.group(2) or match.group(3)
            if match.group(4) is not None:
                exponent_text = match.group(4).translate(superscript_translation)
            exponent = int(exponent_text) if exponent_text is not None else 0
            parsed_values.append(base * (10**exponent))
        return parsed_values

    @staticmethod
    def _neutral_unconfirmed_table_explanation() -> str:
        return "该表保留文本证据支持的定性说明；未保留缺少独立单元格证明的数值证据。"

    @staticmethod
    def _format_table_fact_value(value: float) -> str:
        if value == 0 or (1e-4 <= abs(value) < 1e6):
            return f"{value:g}"
        mantissa, exponent_text = f"{value:.12e}".split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        exponent = int(exponent_text)
        return f"{mantissa}×10^{exponent}"

    @staticmethod
    def _configuration_table_claim_is_causal(
        element: ScientificElement,
        finding: str,
        *,
        extra_context: str = "",
    ) -> bool:
        context = " ".join([element.label, element.explanation, extra_context]).casefold()
        configuration_markers = (
            "hyperparameter",
            "configuration",
            "config.",
            "超参数",
            "配置",
        )
        if not any(marker in context for marker in configuration_markers):
            return False
        value = finding.casefold()
        causal_markers = (
            "require",
            "necessary",
            "confirm",
            "prove",
            "indicat",
            "reflect",
            "because",
            "consistent with",
            "convergence",
            "show",
            "showing",
            "adjust",
            "scale",
            "choice",
            "dependent",
            "suggest",
            "demonstrat",
            "需要",
            "必须",
            "证明",
            "证实",
            "表明",
            "显示",
            "说明",
            "反映",
            "一致",
            "收敛",
            "调整",
        )
        return any(marker in value for marker in causal_markers)

    @classmethod
    def _sanitize_configuration_element(
        cls, element: ScientificElement
    ) -> ScientificElement:
        if element.element_type != "TABLE":
            return element
        findings = [
            finding
            for finding in element.findings
            if not cls._configuration_table_claim_is_causal(element, finding)
        ]
        explanation = element.explanation
        if cls._configuration_table_claim_is_causal(element, explanation):
            explanation = "该表列出论文报告的配置事实；未保留缺少受控比较的因果判断。"
        if not element.table_checks and any(
            marker in " ".join([element.label, element.explanation]).casefold()
            for marker in ("hyperparameter", "configuration", "config.", "超参数", "配置")
        ):
            explanation = "该表列出论文报告的配置事实；未保留由配置推导的因果或性能结论。"
            findings = [
                fact
                for finding in findings
                if (fact := cls._configuration_fact_only(finding, fallback=""))
            ]
        return element.model_copy(
            update={"explanation": explanation, "findings": findings}
        )

    @staticmethod
    def _configuration_fact_only(
        value: str,
        *,
        fallback: str = "该表仅保留论文明确报告的配置事实。",
    ) -> str:
        """Keep direct setup clauses; discard interpretation of configuration facts."""

        factual_markers = (
            "列出",
            "使用",
            "设置",
            "采用",
            "固定",
            "used",
            "uses",
            "set to",
            "fixed",
            "lists",
            "contains",
        )
        clauses = [
            clause.strip(" \t\n，,；;。.")
            for clause in re.split(r"[，；;。]|,(?!\d)|\.(?!\d)", value)
            if clause.strip(" \t\n，,；;。.")
        ]
        facts = [
            clause
            for clause in clauses
            if any(marker in clause.casefold() for marker in factual_markers)
        ]
        if not facts:
            return fallback
        joined = "，".join(facts)
        return joined + ("。" if re.search(r"[\u4e00-\u9fff]", joined) else ".")

    @staticmethod
    def _table_check_verification_matches(
        check: TableNumericCheck,
        verification: TableCheckVerification,
    ) -> bool:
        return (
            FormulaFigureAnalysisAgent._table_check_verification_axis(
                check,
                verification,
            )
            is not None
        )

    @staticmethod
    def _safe_table_rejection_message(value: str | None, fallback: str) -> str:
        cleaned = re.sub(r"\s+", " ", value or "").strip()
        return (cleaned or fallback)[:500]

    @staticmethod
    def _public_table_rejection_message(
        reason_code: TableEvidenceRejectionCode,
    ) -> str:
        return {
            "VERIFIER_UNAVAILABLE": "独立表格核验不可用，候选未被接受。",
            "VERIFICATION_MISSING": "独立核验未返回对应结果，候选未被接受。",
            "VERIFIER_REJECTED": "独立核验拒绝了该候选。",
            "CELL_PROOF_UNCERTAIN": "单元格证明不够清晰，候选未被接受。",
            "SEMANTIC_CONTRADICTION": "候选的方向、数值或最佳值语义存在矛盾。",
            "INCOMPLETE_CELL_PROOF": "候选缺少完整的行、列或数值证明。",
            "CELL_VALUE_MISMATCH": "独立转录值与候选值不一致。",
            "LABEL_SCOPE_AXIS_MISMATCH": "标签、指标、范围或行列方向未能独立绑定。",
            "VISUAL_NOT_CONFIRMED": "表格未获得充分视觉确认，候选未被接受。",
        }[reason_code]

    @staticmethod
    def _table_proposal_sha256(
        proposal: TableNumericCheck | TableCellFact,
    ) -> str:
        return sha256(proposal.model_dump_json().encode("utf-8")).hexdigest()

    def _table_verifier_id(self) -> str:
        if self.vision_gateway is None:
            return "unavailable"
        gateway_type = type(self.vision_gateway)
        gateway_name = f"{gateway_type.__module__}.{gateway_type.__qualname__}"
        model = getattr(self.vision_gateway, "model", None)
        return f"{gateway_name}:{model}" if model else gateway_name

    @classmethod
    def _accepted_table_evidence_audit(
        cls,
        element_id: str,
        item_index: int,
        proposal: TableNumericCheck | TableCellFact,
        proof: TableCheckVerification | TableCellFactVerification | None,
        *,
        page_image_sha256: str,
        verifier_id: str,
    ) -> TableEvidenceAudit:
        if proof is None:
            raise ValueError("accepted table evidence requires a verifier proof")
        evidence_type: Literal["NUMERIC_COMPARISON", "CELL_FACT"] = (
            "NUMERIC_COMPARISON"
            if isinstance(proposal, TableNumericCheck)
            else "CELL_FACT"
        )
        return TableEvidenceAudit(
            element_id=element_id,
            evidence_type=evidence_type,
            item_index=item_index,
            decision="ACCEPTED",
            reason_code="VERIFIED",
            safe_message=cls._safe_table_rejection_message(
                proof.reason,
                "Independent table verification accepted this evidence.",
            ),
            proposal_sha256=cls._table_proposal_sha256(proposal),
            proposal=proposal,
            proof=proof,
            page_image_sha256=page_image_sha256,
            verifier_id=verifier_id,
        )

    @staticmethod
    def _rejected_table_evidence_audit(
        rejection: TableEvidenceRejection,
        *,
        page_image_sha256: str | None,
        verifier_id: str,
    ) -> TableEvidenceAudit:
        if rejection.proposal_sha256 is None or rejection.proposal is None:
            raise ValueError("new table rejection audit requires its proposal payload")
        return TableEvidenceAudit(
            element_id=rejection.element_id,
            evidence_type=rejection.evidence_type,
            item_index=rejection.item_index,
            decision="REJECTED",
            reason_code=rejection.reason_code,
            safe_message=rejection.safe_message,
            proposal_sha256=rejection.proposal_sha256,
            proposal=rejection.proposal,
            proof=rejection.proof,
            page_image_sha256=page_image_sha256,
            verifier_id=verifier_id,
        )

    @classmethod
    def _table_check_rejection(
        cls,
        element_id: str,
        check_index: int,
        verification: TableCheckVerification | None,
        *,
        verification_available: bool,
        check: TableNumericCheck,
    ) -> TableEvidenceRejection:
        code: TableEvidenceRejectionCode
        message: str
        if not verification_available:
            code = "VERIFIER_UNAVAILABLE"
            message = "Independent table verification was unavailable."
        elif verification is None:
            code = "VERIFICATION_MISSING"
            message = "The verifier returned no result for this proposed comparison."
        elif verification.verification_status == "REJECTED":
            code = "VERIFIER_REJECTED"
            message = cls._safe_table_rejection_message(
                verification.reason,
                "The independent verifier rejected this proposed comparison.",
            )
        elif verification.verification_status == "UNCERTAIN":
            code = "CELL_PROOF_UNCERTAIN"
            message = cls._safe_table_rejection_message(
                verification.reason,
                "The required table cells were not readable with sufficient confidence.",
            )
        elif not NumericRelationGuard.table_check_semantics_are_consistent(check):
            code = "SEMANTIC_CONTRADICTION"
            message = "The proposed best-value role contradicts its values or direction."
        elif (
            verification.target_cell_value is None
            or verification.baseline_cell_value is None
            or not verification.target_row_label
            or not verification.baseline_row_label
            or not verification.target_column_header
            or not verification.baseline_column_header
        ):
            code = "INCOMPLETE_CELL_PROOF"
            message = "The verifier did not bind every required row, column, and value."
        elif not isclose(
            verification.target_cell_value,
            check.target_value,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ) or not isclose(
            verification.baseline_cell_value,
            check.baseline_value,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            code = "CELL_VALUE_MISMATCH"
            message = "The independently transcribed cell values do not match the proposal."
        else:
            code = "LABEL_SCOPE_AXIS_MISMATCH"
            message = (
                "The proposed labels, metric, scope, or row/column axis were not "
                "independently bound."
            )
        return TableEvidenceRejection(
            element_id=element_id,
            evidence_type="NUMERIC_COMPARISON",
            item_index=check_index,
            reason_code=code,
            safe_message=message,
            proposal_sha256=cls._table_proposal_sha256(check),
            proposal=check,
            proof=verification,
        )

    @classmethod
    def _table_cell_fact_rejection(
        cls,
        element_id: str,
        fact_index: int,
        verification: TableCellFactVerification | None,
        *,
        verification_available: bool,
        fact: TableCellFact,
    ) -> TableEvidenceRejection:
        code: TableEvidenceRejectionCode
        message: str
        if not verification_available:
            code = "VERIFIER_UNAVAILABLE"
            message = "Independent table verification was unavailable."
        elif verification is None:
            code = "VERIFICATION_MISSING"
            message = "The verifier returned no result for this proposed cell fact."
        elif verification.verification_status == "REJECTED":
            code = "VERIFIER_REJECTED"
            message = cls._safe_table_rejection_message(
                verification.reason,
                "The independent verifier rejected this proposed cell fact.",
            )
        elif verification.verification_status == "UNCERTAIN":
            code = "CELL_PROOF_UNCERTAIN"
            message = cls._safe_table_rejection_message(
                verification.reason,
                "The proposed cell was not readable with sufficient confidence.",
            )
        elif (
            verification.cell_value is None
            or not verification.row_label
            or not verification.column_header
        ):
            code = "INCOMPLETE_CELL_PROOF"
            message = "The verifier did not bind the required row, column, and value."
        elif not isclose(
            verification.cell_value,
            fact.value,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            code = "CELL_VALUE_MISMATCH"
            message = "The independently transcribed cell value does not match the proposal."
        else:
            code = "LABEL_SCOPE_AXIS_MISMATCH"
            message = (
                "The proposed row, column, metric, or scope was not independently bound."
            )
        return TableEvidenceRejection(
            element_id=element_id,
            evidence_type="CELL_FACT",
            item_index=fact_index,
            reason_code=code,
            safe_message=message,
            proposal_sha256=cls._table_proposal_sha256(fact),
            proposal=fact,
            proof=verification,
        )

    @staticmethod
    def _table_tokens(value: str) -> set[str]:
        return table_tokens(value)

    @classmethod
    def _table_label_matches(cls, expected: str, actual: str) -> bool:
        return table_label_matches(expected, actual)

    @classmethod
    def _table_cell_fact_verification_matches(
        cls,
        fact: TableCellFact,
        verification: TableCellFactVerification,
    ) -> bool:
        if (
            verification.cell_value is None
            or not verification.row_label
            or not verification.column_header
            or not isclose(
                verification.cell_value,
                fact.value,
                rel_tol=1e-6,
                abs_tol=1e-6,
            )
            or not cls._table_label_matches(fact.row_label, verification.row_label)
        ):
            return False
        metric_tokens = cls._table_tokens(fact.metric)
        scope_tokens = cls._table_tokens(fact.scope)
        row_tokens = cls._table_tokens(verification.row_label)
        column_tokens = cls._table_tokens(verification.column_header)
        table_scope_tokens = cls._table_tokens(verification.table_scope_text or "")
        expected_column_tokens = cls._table_tokens(fact.column_header)
        column_label_matches = cls._table_label_matches(
            fact.column_header,
            verification.column_header,
        ) or (
            bool(metric_tokens)
            and expected_column_tokens == metric_tokens
            and expected_column_tokens <= column_tokens
        )
        if not column_label_matches:
            return False
        metric_matches = bool(metric_tokens) and (
            metric_tokens <= row_tokens
            or metric_tokens <= column_tokens
            or metric_tokens <= table_scope_tokens
        )
        scope_matches = bool(scope_tokens) and (
            scope_tokens <= row_tokens
            or scope_tokens <= column_tokens
            or scope_tokens <= table_scope_tokens
        )
        return metric_matches and scope_matches

    @staticmethod
    def _table_check_verification_axis(
        check: TableNumericCheck,
        verification: TableCheckVerification,
    ) -> Literal["ROW", "COLUMN"] | None:
        if not NumericRelationGuard.table_check_semantics_are_consistent(check):
            return None
        if (
            verification.target_cell_value is None
            or verification.baseline_cell_value is None
            or not verification.target_row_label
            or not verification.baseline_row_label
            or not verification.target_column_header
            or not verification.baseline_column_header
        ):
            return None
        if not isclose(
            verification.target_cell_value,
            check.target_value,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ) or not isclose(
            verification.baseline_cell_value,
            check.baseline_value,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            return None

        metric_tokens = FormulaFigureAnalysisAgent._table_tokens(check.metric)
        scope_tokens = FormulaFigureAnalysisAgent._table_tokens(check.scope)
        table_scope_tokens = FormulaFigureAnalysisAgent._table_tokens(
            verification.table_scope_text or ""
        )
        scope_matches_table = bool(scope_tokens) and scope_tokens <= table_scope_tokens
        metric_matches_table = bool(metric_tokens) and metric_tokens <= table_scope_tokens
        column_labels_match = (
            FormulaFigureAnalysisAgent._table_label_matches(
                check.target_label,
                verification.target_column_header,
            )
            or FormulaFigureAnalysisAgent._table_label_matches(
                check.target_label,
                " ".join(
                    (
                        verification.target_column_header,
                        verification.target_row_label,
                    )
                ),
            )
        ) and (
            FormulaFigureAnalysisAgent._table_label_matches(
                check.baseline_label,
                verification.baseline_column_header,
            )
            or FormulaFigureAnalysisAgent._table_label_matches(
                check.baseline_label,
                " ".join(
                    (
                        verification.baseline_column_header,
                        verification.baseline_row_label,
                    )
                ),
            )
        )
        shared_row_matches = FormulaFigureAnalysisAgent._table_label_matches(
            verification.target_row_label,
            verification.baseline_row_label,
        )
        shared_row_tokens = FormulaFigureAnalysisAgent._table_tokens(
            verification.target_row_label
        )
        metric_matches_row = bool(metric_tokens) and metric_tokens <= shared_row_tokens
        scope_matches_row = bool(scope_tokens) and scope_tokens <= shared_row_tokens
        column_proof_matches = (
            column_labels_match
            and shared_row_matches
            and (metric_matches_table or metric_matches_row)
            and (scope_matches_table or scope_matches_row)
        )
        if check.label_axis == "COLUMN" and column_proof_matches:
            return "COLUMN"

        row_labels_match = FormulaFigureAnalysisAgent._table_label_matches(
            check.target_label,
            verification.target_row_label,
        ) and FormulaFigureAnalysisAgent._table_label_matches(
            check.baseline_label,
            verification.baseline_row_label,
        )
        row_headers_match = True
        if row_labels_match:
            for header in (
                verification.target_column_header,
                verification.baseline_column_header,
            ):
                header_tokens = FormulaFigureAnalysisAgent._table_tokens(header)
                scope_matches = bool(scope_tokens) and scope_tokens <= header_tokens
                metric_matches = bool(metric_tokens) and metric_tokens <= header_tokens
                if not metric_matches or (not scope_matches and not scope_matches_table):
                    row_headers_match = False
                    break
        if row_labels_match and row_headers_match:
            return "ROW"

        if check.label_axis == "ROW" and column_proof_matches:
            return "COLUMN"
        return None

    @staticmethod
    def _located_targets(document_ir: DocumentIR) -> list[ScientificElementTarget]:
        targets: list[ScientificElementTarget] = []
        for element_type, objects in (
            ("EQUATION", document_ir.equations),
            ("FIGURE", document_ir.figures),
            ("TABLE", document_ir.tables),
        ):
            for item in objects:
                targets.append(
                    ScientificElementTarget(
                        object_id=item.object_id,
                        element_type=element_type,
                        label=item.label,
                        page=item.page_number,
                        section_path=item.section_path,
                        content=item.content[:20_000],
                    )
                )
        return targets

    @staticmethod
    def _rank_targets(
        reading: PreparedReadingContext,
        targets: list[ScientificElementTarget],
    ) -> list[ScientificCandidate]:
        ranked: list[ScientificCandidate] = []
        for element_type in ("EQUATION", "FIGURE", "TABLE"):
            scored: list[tuple[int, ScientificElementTarget, list[str]]] = []
            for target in (item for item in targets if item.element_type == element_type):
                score, reasons = scientific_object_importance(
                    element_type,
                    target.label,
                    target.section_path,
                    target.content,
                    reading.chunks,
                )
                scored.append((score, target, reasons))
            scored.sort(key=lambda item: (-item[0], item[1].page, item[1].label, item[1].object_id))
            for rank, (score, target, reasons) in enumerate(scored, start=1):
                ranked.append(
                    ScientificCandidate(
                        object_id=target.object_id,
                        element_type=target.element_type,
                        label=target.label,
                        page=target.page,
                        rank_within_type=rank,
                        importance_score=score,
                        ranking_reasons=reasons,
                    )
                )
        return ranked

    @staticmethod
    def _covered_object_ids(
        elements: list[ScientificElement],
        targets: list[ScientificElementTarget],
    ) -> set[str]:
        target_identities = {
            (target.element_type, target.label.casefold()): target.object_id for target in targets
        }
        covered: set[str] = set()
        for element in elements:
            object_id = element.document_object_id or target_identities.get(
                (element.element_type, element.label.casefold())
            )
            if object_id:
                covered.add(object_id)
        return covered

    @staticmethod
    def _target_chunks(
        reading: PreparedReadingContext,
        targets: list[ScientificElementTarget],
    ) -> list[KnowledgeChunk]:
        return list(
            reading.context_router.route(
                ReadingTaskType.SCIENTIFIC_ELEMENTS,
                reading.request,
                reading.chunks,
                reading.document_ir,
                object_ids={target.object_id for target in targets},
            ).chunks
        )

    @staticmethod
    def _deduplicate_elements(
        elements: list[ScientificElement],
        targets: list[ScientificElementTarget],
    ) -> list[ScientificElement]:
        target_identities = {
            (target.element_type, target.label.casefold()): target.object_id for target in targets
        }
        seen_objects: set[str] = set()
        deduplicated: list[ScientificElement] = []
        for element in elements:
            object_id = element.document_object_id or target_identities.get(
                (element.element_type, element.label.casefold())
            )
            if object_id and object_id in seen_objects:
                continue
            if object_id:
                seen_objects.add(object_id)
                if element.document_object_id is None:
                    element = element.model_copy(update={"document_object_id": object_id})
            deduplicated.append(element)
        return deduplicated

    @staticmethod
    def _ensure_unique_element_ids(
        elements: list[ScientificElement],
    ) -> list[ScientificElement]:
        seen: set[str] = set()
        normalized: list[ScientificElement] = []
        for index, element in enumerate(elements, start=1):
            element_id = element.element_id
            if element_id in seen:
                identity = element.document_object_id or f"{element.element_type}|{element.label}|{index}"
                element_id = f"element_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
                element = element.model_copy(update={"element_id": element_id})
            seen.add(element_id)
            normalized.append(element)
        return normalized

    @staticmethod
    def _validate_elements(
        elements: list[ScientificElement],
        reading: PreparedReadingContext,
    ) -> None:
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        for element in elements:
            cited_chunks = []
            for chunk_id in dict.fromkeys(element.chunk_ids):
                try:
                    cited_chunks.append(chunk_by_id[chunk_id])
                except KeyError as exc:
                    raise ValueError(
                        "scientific-element analysis referenced an unknown Chunk"
                    ) from exc
            page_object_ids = {
                block.object_id
                for block in reading.document_ir.text_blocks
                if block.page_number == element.page
            }
            cited_object_ids = {
                object_id for chunk in cited_chunks for object_id in chunk.document_object_ids
            }
            if element.page not in {chunk.page for chunk in cited_chunks} and not (
                page_object_ids & cited_object_ids
            ):
                raise ValueError("scientific-element page must resolve through a cited Chunk")

    @staticmethod
    def _coverage(
        mode: ScientificCoverageMode,
        targets: list[ScientificElementTarget],
        requested_object_ids: set[str],
        candidates: list[ScientificCandidate],
        analysis: ScientificElementAnalysis,
        rejected_table_check_count: int,
        rejected_table_cell_fact_count: int,
        table_evidence_rejections: list[TableEvidenceRejection],
        table_evidence_audits: list[TableEvidenceAudit],
    ) -> ScientificCoverageReport:
        by_object_id = {
            element.document_object_id: element
            for element in analysis.elements
            if element.document_object_id is not None
        }
        by_identity = {
            (element.element_type, element.label.casefold()): element
            for element in analysis.elements
        }
        objects: list[ScientificObjectCoverage] = []
        for target in targets:
            requested = target.object_id in requested_object_ids
            element = by_object_id.get(target.object_id) or by_identity.get(
                (target.element_type, target.label.casefold())
            )
            status: ScientificCoverageStatus = (
                "NOT_ANALYZED" if requested else "NOT_REQUESTED"
            )
            if element is not None:
                status = {
                    "TEXT_ONLY": "ANALYZED_TEXT",
                    "VISION_VERIFIED": "VISION_VERIFIED",
                    "VISION_NOT_CONFIRMED": "VISION_NOT_CONFIRMED",
                }[element.visual_status]
            objects.append(
                ScientificObjectCoverage(
                    object_id=target.object_id,
                    element_type=target.element_type,
                    label=target.label,
                    page=target.page,
                    section_path=target.section_path,
                    requested=requested,
                    status=status,
                    analyzed_element_id=element.element_id if element is not None else None,
                )
            )
        analyzed_statuses = {
            "ANALYZED_TEXT",
            "VISION_VERIFIED",
            "VISION_NOT_CONFIRMED",
        }
        analyzed_count = sum(item.status in analyzed_statuses for item in objects)
        return ScientificCoverageReport(
            mode=mode,
            detected_count=len(objects),
            requested_count=len(requested_object_ids),
            analyzed_count=analyzed_count,
            requested_analyzed_count=sum(
                item.requested and item.status in analyzed_statuses for item in objects
            ),
            vision_verified_count=sum(
                item.status == "VISION_VERIFIED" for item in objects
            ),
            rejected_table_check_count=rejected_table_check_count,
            rejected_table_cell_fact_count=rejected_table_cell_fact_count,
            table_evidence_rejections=table_evidence_rejections,
            table_evidence_audits=table_evidence_audits,
            candidates=candidates,
            objects=objects,
        )

    @staticmethod
    def _table_evidence_proposal_summary(
        proposal: TableNumericCheck | TableCellFact,
    ) -> str:
        if isinstance(proposal, TableNumericCheck):
            return (
                f"{proposal.metric} / {proposal.scope}: "
                f"{proposal.target_label}={proposal.target_value:g}, "
                f"{proposal.baseline_label}={proposal.baseline_value:g}"
            )
        return (
            f"{proposal.metric} / {proposal.scope}: "
            f"{proposal.row_label} × {proposal.column_header}={proposal.value:g}"
        )

    @staticmethod
    def _align_caption_location(
        element: ScientificElement,
        chunks: tuple[KnowledgeChunk, ...],
        document_ir: DocumentIR,
    ) -> ScientificElement:
        official_label = re.search(
            r"\b(?:Equation|Figure|Table)\s+\d+[A-Za-z]?\b",
            element.label,
            re.IGNORECASE,
        )
        if official_label is None:
            return element
        label = official_label.group(0)
        object_groups = {
            "EQUATION": document_ir.equations,
            "FIGURE": document_ir.figures,
            "TABLE": document_ir.tables,
        }
        located_object = next(
            (
                item
                for item in object_groups[element.element_type]
                if item.label.lower() == label.lower()
            ),
            None,
        )
        caption_pattern = re.compile(rf"\b{re.escape(label)}\s*[.:]", re.IGNORECASE)
        if label.lower().startswith("figure "):
            figure_number = label.split(maxsplit=1)[1]
            caption_pattern = re.compile(
                rf"\b(?:Figure|Fig\.?)\s+{re.escape(figure_number)}\s*[.:]",
                re.IGNORECASE,
            )
        label_pattern = re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE)
        caption_block = next(
            (block for block in document_ir.text_blocks if caption_pattern.search(block.text)),
            None,
        )
        if caption_block is None:
            caption_block = next(
                (block for block in document_ir.text_blocks if label_pattern.search(block.text)),
                None,
            )
        if caption_block is None and located_object is None:
            return element
        caption_chunk = next(
            (
                chunk
                for chunk in chunks
                if caption_block is not None and caption_block.object_id in chunk.document_object_ids
            ),
            None,
        )
        if caption_chunk is None and located_object is not None:
            caption_chunk = next(
                (chunk for chunk in chunks if chunk.page == located_object.page_number),
                None,
            )
        chunk_ids = list(element.chunk_ids)
        if caption_chunk is not None:
            chunk_ids = list(dict.fromkeys([*chunk_ids, caption_chunk.chunk_id]))
        page_number = located_object.page_number if located_object is not None else caption_block.page_number
        return element.model_copy(
            update={
                "page": page_number,
                "chunk_ids": chunk_ids,
                "document_object_id": located_object.object_id if located_object is not None else None,
            }
        )

    @staticmethod
    def _evidence(
        reading: PdfReadingOutput,
        analysis: ScientificElementAnalysis,
    ) -> tuple[list[EvidenceReference], dict[str, str]]:
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        evidence: list[EvidenceReference] = []
        evidence_by_chunk: dict[str, str] = {}
        objects = {
            item.object_id: ("EQUATION", item)
            for item in reading.document_ir.equations
        }
        objects.update({item.object_id: ("FIGURE", item) for item in reading.document_ir.figures})
        objects.update({item.object_id: ("TABLE", item) for item in reading.document_ir.tables})
        for index, element in enumerate(
            (item for item in analysis.elements if item.document_object_id), start=1
        ):
            evidence_type, located = objects[element.document_object_id]
            evidence_text = located.content[:2000]
            evidence.append(
                EvidenceReference(
                    evidence_id=f"evidence_object_{index:03d}",
                    paper_id=reading.paper.paper_id,
                    evidence_type=evidence_type,
                    page_number=located.page_number,
                    section_path=located.section_path,
                    object_id=located.object_id,
                    evidence_text=evidence_text,
                    content_sha256=sha256(evidence_text.encode("utf-8")).hexdigest(),
                )
            )
        cited_ids = dict.fromkeys(
            chunk_id for element in analysis.elements for chunk_id in element.chunk_ids
        )
        for index, chunk_id in enumerate(cited_ids, start=1):
            chunk = chunk_by_id[chunk_id]
            if chunk.page is None or chunk.section is None or chunk.content_type is None:
                raise ValueError("scientific-element analysis requires located Chunks")
            evidence_text = chunk.text[:2000]
            evidence_id = f"evidence_element_{index:03d}"
            evidence_by_chunk[chunk_id] = evidence_id
            evidence.append(
                EvidenceReference(
                    evidence_id=evidence_id,
                    paper_id=reading.paper.paper_id,
                    evidence_type=chunk.content_type,
                    page_number=chunk.page,
                    section_path=chunk.section,
                    object_id=chunk.chunk_id,
                    evidence_text=evidence_text,
                    content_sha256=sha256(evidence_text.encode("utf-8")).hexdigest(),
                )
            )
        return evidence, evidence_by_chunk

    @staticmethod
    def _render(
        analysis: ScientificElementAnalysis,
        chunk_by_id: dict,
        evidence_by_chunk: dict[str, str],
        coverage: ScientificCoverageReport,
        unanalyzed_labels: tuple[str, ...],
    ) -> str:
        mode_label = {
            "KEY": "关键对象",
            "COMPREHENSIVE": "全面对象",
            "SELECTED": "指定对象",
        }[coverage.mode]
        lines = [
            "# 公式与图表精读",
            "",
            f"- 分析模式：{mode_label}",
            f"- 上游识别对象：{coverage.detected_count}",
            f"- 本次请求对象：{coverage.requested_count}",
            f"- 已完成请求对象：{coverage.requested_analyzed_count}/{coverage.requested_count}",
            f"- 被二次复核拒绝的表格数值项：{coverage.rejected_table_check_count}",
            (
                "- 被二次复核拒绝的单元格事实："
                f"{coverage.rejected_table_cell_fact_count}"
            ),
            f"- 额外关键对象：{max(0, len(analysis.elements) - coverage.analyzed_count)}",
        ]
        if unanalyzed_labels:
            lines.append(f"- 尚未分析：{', '.join(unanalyzed_labels)}")
        lines.append("")
        if not analysis.elements:
            lines.extend(("未从论文文本中识别出可可靠分析的关键公式、图或表。", ""))
            return "\n".join(lines).rstrip() + "\n"
        type_labels = {"EQUATION": "公式", "FIGURE": "图", "TABLE": "表"}
        accepted_audits = [
            item
            for item in coverage.table_evidence_audits
            if item.decision == "ACCEPTED"
        ]
        if accepted_audits:
            lines.extend(("## 表格证据接受审计", ""))
            for audit in accepted_audits:
                evidence_label = (
                    "数值比较"
                    if audit.evidence_type == "NUMERIC_COMPARISON"
                    else "单元格事实"
                )
                lines.append(
                    f"- `{audit.element_id}` {evidence_label}"
                    f"[{audit.item_index}] `{audit.reason_code}`："
                    f"{FormulaFigureAnalysisAgent._table_evidence_proposal_summary(audit.proposal)}；"
                    f"proposal `{audit.proposal_sha256[:12]}`，"
                    f"page `{(audit.page_image_sha256 or 'unavailable')[:12]}`。"
                )
            lines.append("")
        if coverage.table_evidence_rejections:
            lines.extend(("## 表格证据拒绝审计", ""))
            for rejection in coverage.table_evidence_rejections:
                evidence_label = (
                    "数值比较"
                    if rejection.evidence_type == "NUMERIC_COMPARISON"
                    else "单元格事实"
                )
                lines.append(
                    f"- `{rejection.element_id}` {evidence_label}"
                    f"[{rejection.item_index}] `{rejection.reason_code}`："
                    f"{FormulaFigureAnalysisAgent._public_table_rejection_message(rejection.reason_code)} "
                    f"proposal `{(rejection.proposal_sha256 or 'legacy-unavailable')[:12]}`。"
                )
            lines.append("")
        lines.extend(("## 候选排序", ""))
        for element_type in ("EQUATION", "FIGURE", "TABLE"):
            top_candidates = [
                candidate
                for candidate in coverage.candidates
                if candidate.element_type == element_type
            ][:5]
            if top_candidates:
                values = ", ".join(
                    f"{item.rank_within_type}. {item.label} (score={item.importance_score})"
                    for item in top_candidates
                )
                lines.append(f"- {type_labels[element_type]}：{values}")
        lines.append("")
        for element in analysis.elements:
            status = {
                "VISION_VERIFIED": "已用页面视觉核对",
                "VISION_NOT_CONFIRMED": "视觉页面未确认，保留文本分析",
                "TEXT_ONLY": "仅依据抽取文本",
            }[element.visual_status]
            lines.extend(
                (
                    f"## {type_labels[element.element_type]}：{element.label}",
                    "",
                    f"- 页码：p.{element.page}",
                    f"- 分析方式：{status}",
                    f"- 解释：{element.explanation}",
                )
            )
            if element.document_object_id:
                lines.append(f"- 页面对象：`{element.document_object_id}`")
            if element.variables:
                lines.append("- 变量：")
                for variable in element.variables:
                    lines.append(f"  - `{variable.symbol}`：{variable.meaning}")
            if element.table_checks:
                lines.append("- 数值核验：")
                for check in element.table_checks:
                    relative = (
                        ""
                        if check.relative_difference_percent is None
                        else f"；相对差异 {check.relative_difference_percent:g}%"
                    )
                    lines.append(
                        "  - "
                        f"`{check.check_type}` {check.metric} / {check.scope}："
                        f"{check.target_label}={check.target_value:g}，"
                        f"{check.baseline_label}={check.baseline_value:g}，"
                        f"目标值-基线值={check.absolute_difference:g}{relative}；"
                        f"方向={check.direction}"
                    )
            if element.table_cell_facts:
                lines.append("- 单元格事实核验：")
                for fact in element.table_cell_facts:
                    lines.append(
                        "  - "
                        f"{fact.metric} / {fact.scope}："
                        f"{fact.row_label} × {fact.column_header} = "
                        f"{FormulaFigureAnalysisAgent._format_table_fact_value(fact.value)}"
                    )
            rendered_findings = (
                [
                    finding
                    for finding in element.findings
                    if not (
                        element.table_checks
                        and FormulaFigureAnalysisAgent._is_numeric_comparison(finding)
                    )
                    and not FormulaFigureAnalysisAgent._configuration_table_claim_is_causal(
                        element,
                        finding,
                    )
                ]
                if element.element_type == "TABLE"
                else element.findings
            )
            if rendered_findings:
                finding_label = "关键结论" if element.element_type == "EQUATION" else "读图/读表结论"
                lines.append(f"- {finding_label}：")
                for finding in rendered_findings:
                    lines.append(f"  - {finding}")
            references = []
            for chunk_id in dict.fromkeys(element.chunk_ids):
                chunk = chunk_by_id[chunk_id]
                section = " / ".join(chunk.section or ["Document"])
                references.append(
                    f"p.{chunk.page} {section} ({chunk_id}; {evidence_by_chunk[chunk_id]})"
                )
            lines.extend((f"- 依据：{'; '.join(references)}", ""))
        return "\n".join(lines).rstrip() + "\n"
