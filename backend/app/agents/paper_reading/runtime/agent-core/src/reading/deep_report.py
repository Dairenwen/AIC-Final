from __future__ import annotations

from dataclasses import dataclass

from llm.experiments import ExperimentAnalysis
from llm.gateway import DeepReadingNarrative
from llm.scientific_elements import ScientificElementAnalysis
from paper_context.models import MetadataProvenance
from pydantic import BaseModel, ConfigDict, Field
from schemas.models import EvidenceReference, PaperRecord, QAResponse, ReadingRequest, ReadingResult

from .experiments import (
    ExperimentAnalysisOutput,
    ExperimentReproducibilityAgent,
    ReproducibilityReadinessSummary,
)
from .execution import ExecutionMode, FlowDegradation, FlowExecutionSummary, StageStatus
from .numeric_relations import NumericRelationGuard
from .renderer import render_reading_markdown
from .scientific_elements import (
    FormulaFigureAnalysisAgent,
    ScientificCoverageReport,
    ScientificElementsOutput,
)
from .context import PreparedReadingContext
from .planning import render_routing_summary
from .qa import PaperQaOutput
from .reliability import CoreReliabilityResult, render_reliability_markdown


class DeepReadingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "deep_reading_report_v1"
    paper: PaperRecord
    request: ReadingRequest
    narrative: DeepReadingNarrative | None
    reading_result: ReadingResult
    scientific_elements: ScientificElementAnalysis | None = None
    scientific_coverage: ScientificCoverageReport | None = None
    experiments: ExperimentAnalysis | None = None
    reproducibility_summary: ReproducibilityReadinessSummary | None = None
    qa_response: QAResponse | None = None
    qa_evidence: list[EvidenceReference] = Field(default_factory=list)
    explanation_response: QAResponse | None = None
    explanation_evidence: list[EvidenceReference] = Field(default_factory=list)
    # New runs always populate this field. It remains optional so historical
    # deep_reading_report_v1 artifacts can still be replayed unchanged.
    flow_execution: FlowExecutionSummary | None = None
    core_reliability: CoreReliabilityResult
    metadata_provenance: list[MetadataProvenance] = Field(default_factory=list)


@dataclass(frozen=True)
class UnifiedDeepReadingOutput:
    report: DeepReadingReport
    markdown: str
    json_text: str


def build_unified_deep_reading_output(
    reading: PreparedReadingContext,
    *,
    scientific: ScientificElementsOutput | None = None,
    experiments: ExperimentAnalysisOutput | None = None,
    qa: PaperQaOutput | None = None,
    explanation: PaperQaOutput | None = None,
    execution_mode: ExecutionMode = "flow_first",
    stage_statuses: dict[str, StageStatus] | None = None,
    degradations: list[FlowDegradation] | None = None,
) -> UnifiedDeepReadingOutput:
    guard = NumericRelationGuard()
    comparisons = tuple(
        guard.from_table_check(check)
        for element in (scientific.analysis.elements if scientific else [])
        for check in element.table_checks
    )
    guarded_result = guard.sanitize_reading_result(reading.result, comparisons)
    guarded_scientific = (
        guard.sanitize_scientific_analysis(scientific.analysis) if scientific else None
    )
    guarded_experiments = (
        guard.sanitize_experiment_analysis(experiments.analysis, comparisons)
        if experiments
        else None
    )
    reliability = CoreReliabilityResult(
        records=[
            *reading.reliability_records,
            *(experiments.reliability_records if experiments else ()),
            *(scientific.reliability_records if scientific else ()),
        ]
    )
    review_candidate_count = sum(
        record.review_candidate_content is not None
        for record in reliability.records
    )
    recorded_degradations = list(degradations or [])
    base_analysis_repaired = any(
        warning.warning_code == "BASE_ANALYSIS_REPAIRED"
        for warning in reading.result.warnings
    )
    stages: dict[str, StageStatus] = {
        "pdf_preparation": (
            "COMPLETED_WITH_WARNINGS"
            if reading.document_ir.parse_quality.status == "REVIEW"
            else "COMPLETED"
        ),
        "base_reading": (
            "COMPLETED_WITH_WARNINGS"
            if base_analysis_repaired
            else "COMPLETED"
        ),
        "experiments": "COMPLETED" if experiments else "NOT_REQUESTED",
        "scientific_elements": "COMPLETED" if scientific else "NOT_REQUESTED",
        "question": "COMPLETED" if qa else "NOT_REQUESTED",
        "explanation": "COMPLETED" if explanation else "NOT_REQUESTED",
        "core_reliability": (
            "COMPLETED_WITH_WARNINGS"
            if review_candidate_count
            else "COMPLETED"
        ),
    }
    stages.update(stage_statuses or {})
    if (
        execution_mode == "flow_first"
        and reading.document_ir.parse_quality.status == "REVIEW"
        and not any(item.code == "PARSE_REVIEW_CONTINUED" for item in recorded_degradations)
    ):
        recorded_degradations.append(
            FlowDegradation(
                stage="pdf_preparation",
                code="PARSE_REVIEW_CONTINUED",
                message="Partially extractable PDF text was used in flow-first mode.",
            )
        )
    if (
        base_analysis_repaired
        and not any(item.code == "BASE_ANALYSIS_REPAIRED" for item in recorded_degradations)
    ):
        recorded_degradations.append(
            FlowDegradation(
                stage="base_reading",
                code="BASE_ANALYSIS_REPAIRED",
                message="One malformed base analysis response was repaired.",
            )
        )
    if (
        review_candidate_count
        and not any(
            item.code == "UNCERTAIN_CONTENT_RETAINED"
            for item in recorded_degradations
        )
    ):
        recorded_degradations.append(
            FlowDegradation(
                stage="core_reliability",
                code="UNCERTAIN_CONTENT_RETAINED",
                category="RELIABILITY_GATE",
                message=(
                    f"{review_candidate_count} low-risk unresolved item(s) were retained "
                    "only as review candidates, not as reliable core conclusions."
                ),
                action="Review the candidate against its bound Evidence before reuse.",
            )
        )
    flow_execution = FlowExecutionSummary(
        mode=execution_mode,
        completion_status=(
            "COMPLETED_WITH_WARNINGS" if recorded_degradations else "COMPLETED"
        ),
        stages=stages,
        degradations=recorded_degradations,
    )
    report = DeepReadingReport(
        paper=reading.paper,
        request=reading.request,
        narrative=reading.analysis.narrative,
        reading_result=guarded_result,
        scientific_elements=guarded_scientific,
        scientific_coverage=scientific.coverage if scientific else None,
        experiments=guarded_experiments,
        reproducibility_summary=(
            experiments.reproducibility_summary if experiments else None
        ),
        qa_response=qa.response if qa else None,
        qa_evidence=list(qa.evidence) if qa else [],
        explanation_response=explanation.response if explanation else None,
        explanation_evidence=list(explanation.evidence) if explanation else [],
        flow_execution=flow_execution,
        core_reliability=reliability,
        metadata_provenance=list(reading.metadata_provenance),
    )
    parts = [
        render_reading_markdown(guarded_result).rstrip(),
        render_routing_summary(reading.reading_plan).rstrip(),
    ]
    if reading.analysis.narrative is not None:
        narrative = reading.analysis.narrative
        lines = ["# 深度理解补充", "", f"- 一句话概括：{narrative.one_sentence_summary}"]
        for title, values in (
            ("背景与动机", narrative.background_and_motivation),
            ("问题定义", narrative.problem_definition),
            ("方法数据流", narrative.method_data_flow),
            ("关键假设", narrative.assumptions),
            ("进一步阅读问题", narrative.further_reading_questions),
        ):
            lines.extend(("", f"## {title}", ""))
            lines.extend(f"- {item}" for item in values)
            if not values:
                lines.append("暂无可靠内容。")
        parts.append("\n".join(lines))
    if experiments is not None:
        parts.append(
            ExperimentReproducibilityAgent._render(
                guarded_experiments,
                experiments.reproducibility_summary,
                {chunk.chunk_id: chunk for chunk in reading.chunks},
            ).rstrip()
        )
    if scientific is not None:
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        evidence_by_chunk = {
            item.object_id: item.evidence_id
            for item in scientific.evidence
            if item.object_id in chunk_by_id
        }
        parts.append(
            FormulaFigureAnalysisAgent._render(
                guarded_scientific,
                chunk_by_id,
                evidence_by_chunk,
                scientific.coverage,
                scientific.unanalyzed_labels,
            ).rstrip()
        )
    if qa is not None:
        parts.append(qa.markdown.rstrip())
    if explanation is not None:
        parts.append(explanation.markdown.rstrip())
    flow_lines = [
        "# 流程状态",
        "",
        f"- 执行模式：`{flow_execution.mode}`",
        f"- 完成状态：`{flow_execution.completion_status}`",
        "",
        "## 阶段",
        "",
        *(f"- `{name}`：`{status}`" for name, status in flow_execution.stages.items()),
    ]
    if flow_execution.degradations:
        flow_lines.extend(("", "## 降级记录", ""))
        flow_lines.extend(
            (
                f"- `{item.stage}` / `{item.code}` / `{item.category}`："
                f"{item.message} 建议：{item.action}"
                + (
                    " 候选："
                    + "；".join(
                        f"p.{candidate.page_number or '?'} {candidate.object_id} "
                        f"“{candidate.snippet}”"
                        for candidate in item.candidates
                    )
                    if item.candidates
                    else ""
                )
            )
            for item in flow_execution.degradations
        )
    parts.append("\n".join(flow_lines))
    parts.append(render_reliability_markdown(reliability).rstrip())
    return UnifiedDeepReadingOutput(
        report=report,
        markdown="\n\n".join(parts).rstrip() + "\n",
        json_text=report.model_dump_json(indent=2),
    )
