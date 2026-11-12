from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal

from llm.experiments import ExperimentAnalysis
from llm.scientific_elements import ScientificElementAnalysis

from .deep_report import DeepReadingReport
from .numeric_relations import NumericRelationGuard
from .reliability import (
    ClaimEvidenceReliabilityGuard,
    CoreReliabilityResult,
    ReliabilityRecord,
    ReliabilitySource,
    ReliabilityStatus,
)
from .scientific_elements import FormulaFigureAnalysisAgent


ReplayProfile = Literal["attention", "lora"]


def replay_core_reliability_report(report: DeepReadingReport) -> DeepReadingReport:
    """Replay only deterministic final reliability stages on an existing report.

    The report is already a model-produced artifact. This path never invokes the
    Splitter, a text model, or a vision model, and never mutates the input file.
    Final-report table checks are treated as the accepted checks produced by the
    independent cell verifier in the original run.
    """

    numeric_guard = NumericRelationGuard()
    scientific = _finalize_scientific(report.scientific_elements, numeric_guard)
    comparisons = tuple(
        numeric_guard.from_table_check(check)
        for element in (scientific.elements if scientific else [])
        for check in element.table_checks
    )
    reading = numeric_guard.sanitize_reading_result(report.reading_result, comparisons)
    reading, reading_records = ClaimEvidenceReliabilityGuard().consolidate_reading_result(
        reading
    )
    experiments = (
        numeric_guard.sanitize_experiment_analysis(report.experiments, comparisons)
        if report.experiments is not None
        else None
    )
    records = _reconcile_reliability_records(
        report.core_reliability.records,
        reading_records,
        experiments,
        scientific,
        numeric_guard,
    )
    return report.model_copy(
        update={
            "reading_result": reading,
            "scientific_elements": scientific,
            "experiments": experiments,
            "core_reliability": CoreReliabilityResult(records=records),
        }
    )


def _finalize_scientific(
    analysis: ScientificElementAnalysis | None,
    numeric_guard: NumericRelationGuard,
) -> ScientificElementAnalysis | None:
    if analysis is None:
        return None
    finalized = [
        FormulaFigureAnalysisAgent._finalize_table_visual_result(
            element,
            checks_proven=(
                element.element_type == "TABLE"
                and element.visual_status == "VISION_VERIFIED"
                and bool(element.table_checks)
            ),
            cell_facts_proven=(
                element.element_type == "TABLE"
                and element.visual_status == "VISION_VERIFIED"
                and bool(element.table_cell_facts)
            ),
        )
        for element in analysis.elements
    ]
    guarded = numeric_guard.sanitize_scientific_analysis(
        ScientificElementAnalysis(elements=finalized)
    )
    return ScientificElementAnalysis(
        elements=[
            FormulaFigureAnalysisAgent._sanitize_configuration_element(element)
            for element in guarded.elements
        ]
    )


def _reconcile_reliability_records(
    existing: Iterable[ReliabilityRecord],
    reading_records: Iterable[ReliabilityRecord],
    experiments: ExperimentAnalysis | None,
    scientific: ScientificElementAnalysis | None,
    numeric_guard: NumericRelationGuard,
) -> list[ReliabilityRecord]:
    """Keep the audit aligned with replayed output without reviving removed text."""

    by_final: dict[str, list[ReliabilityRecord]] = defaultdict(list)
    for record in existing:
        if record.final_content:
            by_final[record.final_content].append(record)

    records = list(reading_records)
    items: list[tuple[str, str, str, ReliabilitySource, bool]] = []
    if experiments is not None:
        items.extend(
            (
                f"experiment_finding_{index:03d}",
                "EXPERIMENT_FINDING",
                finding.content,
                ReliabilitySource.EVIDENCE_SUMMARY,
                False,
            )
            for index, finding in enumerate(experiments.findings, start=1)
        )
        items.extend(
            (
                f"conclusion_assessment_{index:03d}",
                "CONCLUSION",
                f"{assessment.conclusion}；{assessment.reason}",
                ReliabilitySource.AGENT_INFERENCE,
                False,
            )
            for index, assessment in enumerate(
                experiments.conclusion_assessments, start=1
            )
        )
    if scientific is not None:
        for element in scientific.elements:
            items.append(
                (
                    f"{element.element_id}:explanation",
                    "EQUATION_FIGURE",
                    element.explanation,
                    ReliabilitySource.EVIDENCE_SUMMARY,
                    False,
                )
            )
            generated = {
                numeric_guard.table_check_statement(check)
                for check in element.table_checks
            }
            items.extend(
                (
                    f"{element.element_id}:finding:{index}",
                    "EQUATION_FIGURE",
                    finding,
                    ReliabilitySource.EVIDENCE_SUMMARY,
                    finding in generated,
                )
                for index, finding in enumerate(element.findings, start=1)
            )

    for item_id, item_type, content, source, structured in items:
        prior = by_final.get(content, [])
        if prior:
            record = prior.pop(0)
            records.append(
                record.model_copy(
                    update={
                        "item_id": item_id,
                        "original_content": content,
                        "final_content": content,
                        "unsupported_fragments": [],
                    }
                )
            )
            continue
        records.append(
            ReliabilityRecord(
                item_id=item_id,
                item_type=item_type,
                status=(
                    ReliabilityStatus.SUPPORTED
                    if structured
                    else ReliabilityStatus.PARTIALLY_SUPPORTED
                ),
                source=source,
                original_content=content,
                final_content=content,
                unsupported_fragments=[],
                reason=(
                    "Generated deterministically from accepted structured table checks."
                    if structured
                    else "Retained from a previously reliability-filtered report after deterministic replay."
                ),
            )
        )
    return records


def validate_replayed_report(
    report: DeepReadingReport,
    *,
    profile: ReplayProfile | None = None,
) -> list[str]:
    """Return blocking invariant violations for an offline replay result."""

    errors: list[str] = []
    numeric_guard = NumericRelationGuard()
    evidence_ids = {item.evidence_id for item in report.reading_result.evidence}
    for claim in report.reading_result.claims:
        if not claim.evidence_ids:
            errors.append(f"{claim.claim_id}: Claim has no Evidence")
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                errors.append(f"{claim.claim_id}: unresolved Evidence {evidence_id}")

    for element in report.scientific_elements.elements if report.scientific_elements else []:
        if element.element_type != "TABLE":
            continue
        if element.visual_status != "VISION_VERIFIED" and element.table_checks:
            errors.append(f"{element.label}: non-verified table retained checks")
        if element.visual_status != "VISION_VERIFIED" and element.table_cell_facts:
            errors.append(f"{element.label}: non-verified table retained cell facts")
        generated = {
            numeric_guard.table_check_statement(check) for check in element.table_checks
        }
        configuration_table = numeric_guard.is_configuration_table(element)
        for check in element.table_checks:
            if check.target_label.casefold() == check.baseline_label.casefold():
                errors.append(f"{element.label}: table self-comparison")
        if (
            numeric_guard.is_table_numeric_statement(element.explanation)
            or (
                numeric_guard.has_table_numeric_value(element.explanation)
                and not configuration_table
            )
        ):
            errors.append(f"{element.label}: free-text numeric explanation survived")
        for finding in element.findings:
            if finding not in generated and (
                numeric_guard.is_table_numeric_statement(finding)
                or (
                    numeric_guard.has_table_numeric_value(finding)
                    and not configuration_table
                )
            ):
                errors.append(f"{element.label}: numeric finding lacks an accepted check")

        if profile == "attention":
            for check in element.table_checks:
                values = {check.target_value, check.baseline_value}
                labels = f"{check.target_label} {check.baseline_label}".casefold()
                if (
                    "en-fr" in check.scope.casefold()
                    and "transformer(big)" in labels
                    and 3.3e18 in values
                ):
                    errors.append(
                        f"{element.label}: Transformer(big) EN-FR is bound to 3.3e18"
                    )
        if profile == "lora":
            if element.label.casefold() == "table 3":
                for check in element.table_checks:
                    if "en-de" in check.scope.casefold():
                        errors.append("Table 3: retained incorrect EN-DE scope")
            if element.label.casefold() == "table 9":
                text = " ".join([element.explanation, *element.findings]).casefold()
                if ("α=16" in text or "alpha=16" in text) and any(
                    marker in text
                    for marker in (
                        "导致",
                        "证明",
                        "需要",
                        "causes",
                        "proves",
                        "requires",
                    )
                ):
                    errors.append("Table 9: alpha=16 was promoted beyond a configuration fact")

    try:
        CoreReliabilityResult.model_validate(report.core_reliability.model_dump())
    except Exception as exc:  # pragma: no cover - Pydantic supplies the details
        errors.append(f"core reliability is not parseable: {exc}")
    return errors
