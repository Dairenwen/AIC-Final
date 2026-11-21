from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from llm.experiments import ExperimentAnalysis, ExperimentAnalysisGateway
from pydantic import BaseModel, ConfigDict
from schemas.models import EvidenceReference

from .context import PreparedReadingContext
from .numeric_relations import NumericRelationGuard
from .planning import ReadingTaskType
from .reliability import ClaimEvidenceReliabilityGuard, ReliabilityRecord


class ReproducibilityReadinessSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    conclusion: str
    available_information: list[str]
    missing_information: list[str]


@dataclass(frozen=True)
class ExperimentAnalysisOutput:
    analysis: ExperimentAnalysis
    reproducibility_summary: ReproducibilityReadinessSummary
    evidence: tuple[EvidenceReference, ...]
    markdown: str
    reliability_records: tuple[ReliabilityRecord, ...] = ()


class ExperimentReproducibilityAgent:
    def __init__(self, gateway: ExperimentAnalysisGateway) -> None:
        self.gateway = gateway
        self.numeric_relation_guard = NumericRelationGuard()
        self.reliability_guard = ClaimEvidenceReliabilityGuard(gateway)

    def analyze(self, reading: PreparedReadingContext) -> ExperimentAnalysisOutput:
        selected_ids: set[str] = set()
        for task_type in (ReadingTaskType.EXPERIMENT, ReadingTaskType.REPRODUCIBILITY):
            task = reading.reading_plan.task(task_type)
            if task.enabled:
                selected_ids.update(task.selected_chunk_ids)
        selected_chunks = [
            chunk for chunk in reading.chunks if chunk.chunk_id in selected_ids
        ]
        if not selected_chunks:
            selected_chunks = list(reading.chunks_for_task(ReadingTaskType.EXPERIMENT))
        context = [chunk.model_dump(mode="json") for chunk in selected_chunks]
        analysis = self.gateway.analyze_experiments(
            reading.paper,
            context,
            reading.request.language,
        )
        analysis = self.numeric_relation_guard.sanitize_experiment_analysis(analysis)
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        analysis, reliability_records = self.reliability_guard.consolidate_experiments(
            analysis,
            {chunk_id: chunk.text for chunk_id, chunk in chunk_by_id.items()},
        )
        evidence: list[EvidenceReference] = []
        for index, chunk_id in enumerate(dict.fromkeys(analysis.all_chunk_ids()), start=1):
            try:
                chunk = chunk_by_id[chunk_id]
            except KeyError as exc:
                raise ValueError("experiment analysis referenced an unknown Chunk") from exc
            if chunk.page is None or chunk.section is None or chunk.content_type is None:
                raise ValueError("experiment analysis requires located Chunks")
            evidence_text = chunk.text[:2000]
            evidence.append(
                EvidenceReference(
                    evidence_id=f"evidence_experiment_{index:03d}",
                    paper_id=reading.paper.paper_id,
                    evidence_type=chunk.content_type,
                    page_number=chunk.page,
                    section_path=chunk.section,
                    object_id=chunk.chunk_id,
                    evidence_text=evidence_text,
                    content_sha256=sha256(evidence_text.encode("utf-8")).hexdigest(),
                )
            )
        reproducibility_summary = self._reproducibility_summary(analysis)
        return ExperimentAnalysisOutput(
            analysis=analysis,
            reproducibility_summary=reproducibility_summary,
            evidence=tuple(evidence),
            markdown=self._render(analysis, reproducibility_summary, chunk_by_id),
            reliability_records=reliability_records,
        )

    @staticmethod
    def _reproducibility_summary(
        analysis: ExperimentAnalysis,
    ) -> ReproducibilityReadinessSummary:
        reproducibility = analysis.reproducibility
        available: list[str] = []
        if reproducibility.code_availability == "AVAILABLE":
            available.append("代码可获得")
        if reproducibility.data_availability == "AVAILABLE":
            available.append("数据可获得")
        if reproducibility.hyperparameters:
            available.append("提供关键超参数")
        if reproducibility.training_details:
            available.append("提供训练流程")
        if reproducibility.hardware_and_cost:
            available.append("提供硬件或成本信息")
        missing = list(reproducibility.missing_information)
        if reproducibility.code_availability != "AVAILABLE":
            missing.append("可执行代码未确认可获得")
        if reproducibility.data_availability != "AVAILABLE":
            missing.append("实验数据未确认可获得")
        if not reproducibility.hyperparameters:
            missing.append("关键超参数不足")
        if not reproducibility.training_details:
            missing.append("训练流程不足")
        missing = list(dict.fromkeys(missing))
        if not missing and len(available) >= 4:
            status = "DIRECTLY_REPRODUCIBLE"
            conclusion = "论文给出的资源与配置已足以直接进入复现实验。"
        elif available:
            status = "PARTIALLY_REPRODUCIBLE"
            conclusion = "可以开始搭建复现，但仍需补齐下列关键信息或资源。"
        else:
            status = "INSUFFICIENT_INFORMATION"
            conclusion = "当前证据不足以直接启动可靠复现。"
        return ReproducibilityReadinessSummary(
            status=status,
            conclusion=conclusion,
            available_information=available,
            missing_information=missing,
        )

    @staticmethod
    def _render(
        analysis: ExperimentAnalysis,
        reproducibility_summary: ReproducibilityReadinessSummary,
        chunk_by_id: dict,
    ) -> str:
        lines = ["# 实验与复现精读", ""]

        def references(chunk_ids: list[str]) -> str:
            values = []
            for chunk_id in dict.fromkeys(chunk_ids):
                chunk = chunk_by_id[chunk_id]
                values.append(f"p.{chunk.page} {' / '.join(chunk.section or ['Document'])} ({chunk_id})")
            return "; ".join(values)

        for title, items in (
            ("数据集", analysis.datasets),
            ("基线", analysis.baselines),
            ("评价指标", analysis.metrics),
        ):
            lines.extend((f"## {title}", ""))
            if not items:
                lines.extend(("论文未提供可可靠提取的信息。", ""))
            for item in items:
                lines.extend(
                    (
                        f"- **{item.name}**：{item.detail}",
                        f"  - 依据：{references(item.chunk_ids)}",
                    )
                )
            lines.append("")

        lines.extend(("## 实验发现（论文报告）", ""))
        for item in analysis.findings:
            lines.extend(
                (
                    f"- `{item.finding_type}`：{item.content}",
                    f"  - 依据：{references(item.chunk_ids)}",
                )
            )
        if not analysis.findings:
            lines.append("论文未提供可可靠提取的实验发现。")
        lines.append("")

        lines.extend(("## 结论支持度（Agent 判断）", ""))
        for item in analysis.conclusion_assessments:
            lines.extend(
                (
                    f"- `{item.support_status}`：{item.conclusion}",
                    f"  - 判断：{item.reason}",
                    f"  - 依据：{references(item.chunk_ids)}",
                )
            )
        if not analysis.conclusion_assessments:
            lines.append("暂无结论支持度判断。")
        lines.append("")

        reproducibility = analysis.reproducibility
        lines.extend(
            (
                "## 复现信息",
                "",
                f"- 复现判断：`{reproducibility_summary.status}`",
                f"- 总结：{reproducibility_summary.conclusion}",
                f"- 代码：`{reproducibility.code_availability}`",
                f"- 数据：`{reproducibility.data_availability}`",
            )
        )
        if reproducibility_summary.available_information:
            lines.append(
                "- 已具备：" + "、".join(reproducibility_summary.available_information)
            )
        for title, items in (
            ("超参数", reproducibility.hyperparameters),
            ("硬件与成本", reproducibility.hardware_and_cost),
            ("训练细节", reproducibility.training_details),
        ):
            if items:
                lines.append(f"- {title}：")
                for item in items:
                    lines.append(f"  - {item.name}：{item.detail}；依据：{references(item.chunk_ids)}")
        if reproducibility_summary.missing_information:
            lines.append("- 缺失信息：")
            lines.extend(
                f"  - {item}" for item in reproducibility_summary.missing_information
            )
        return "\n".join(lines).rstrip() + "\n"
