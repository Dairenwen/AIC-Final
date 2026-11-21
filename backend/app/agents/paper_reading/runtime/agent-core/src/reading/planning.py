from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field
from schemas.models import DocumentIR, KnowledgeChunk, ReadingRequest

from .errors import ReadingStageError
from .execution import DegradationCandidate


class ReadingTaskType(StrEnum):
    BASE_READING = "BASE_READING"
    RESEARCH_QUESTION = "RESEARCH_QUESTION"
    METHOD = "METHOD"
    EXPERIMENT = "EXPERIMENT"
    REPRODUCIBILITY = "REPRODUCIBILITY"
    LIMITATION = "LIMITATION"
    SCIENTIFIC_ELEMENTS = "SCIENTIFIC_ELEMENTS"
    PAPER_QA = "PAPER_QA"
    SELECTION_EXPLANATION = "SELECTION_EXPLANATION"


class SectionRole(StrEnum):
    ABSTRACT = "ABSTRACT"
    INTRODUCTION = "INTRODUCTION"
    RELATED_WORK = "RELATED_WORK"
    METHOD = "METHOD"
    EXPERIMENT = "EXPERIMENT"
    RESULT = "RESULT"
    DISCUSSION = "DISCUSSION"
    LIMITATION = "LIMITATION"
    CONCLUSION = "CONCLUSION"
    IMPLEMENTATION = "IMPLEMENTATION"
    APPENDIX = "APPENDIX"
    REFERENCES = "REFERENCES"
    OTHER = "OTHER"


class PlannedReadingTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: ReadingTaskType
    enabled: bool
    preferred_section_roles: tuple[SectionRole, ...]
    selected_chunk_ids: tuple[str, ...]
    selected_object_ids: tuple[str, ...]
    context_character_count: int = Field(ge=0)
    routing_strategy: str
    fallback_reason: str | None = None


class ReadingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str
    depth: str
    requested_aspects: tuple[str, ...]
    planned_tasks: tuple[PlannedReadingTask, ...]
    planning_summary: str

    def task(self, task_type: ReadingTaskType) -> PlannedReadingTask:
        return next(item for item in self.planned_tasks if item.task_type == task_type)


@dataclass(frozen=True)
class ContextBudget:
    max_chunks: int
    max_characters: int
    neighbor_radius: int


@dataclass(frozen=True)
class ContextSelection:
    chunks: tuple[KnowledgeChunk, ...]
    object_ids: tuple[str, ...]
    preferred_roles: tuple[SectionRole, ...]
    routing_strategy: str
    fallback_reason: str | None

    @property
    def character_count(self) -> int:
        return sum(len(chunk.text) for chunk in self.chunks)


TASK_ROLES: dict[ReadingTaskType, tuple[SectionRole, ...]] = {
    ReadingTaskType.RESEARCH_QUESTION: (
        SectionRole.ABSTRACT,
        SectionRole.INTRODUCTION,
        SectionRole.CONCLUSION,
    ),
    ReadingTaskType.METHOD: (SectionRole.METHOD, SectionRole.IMPLEMENTATION),
    ReadingTaskType.EXPERIMENT: (
        SectionRole.EXPERIMENT,
        SectionRole.RESULT,
        SectionRole.DISCUSSION,
    ),
    ReadingTaskType.REPRODUCIBILITY: (
        SectionRole.IMPLEMENTATION,
        SectionRole.EXPERIMENT,
        SectionRole.APPENDIX,
    ),
    ReadingTaskType.LIMITATION: (
        SectionRole.LIMITATION,
        SectionRole.DISCUSSION,
        SectionRole.CONCLUSION,
    ),
    ReadingTaskType.SCIENTIFIC_ELEMENTS: (
        SectionRole.METHOD,
        SectionRole.EXPERIMENT,
        SectionRole.RESULT,
        SectionRole.IMPLEMENTATION,
    ),
    ReadingTaskType.PAPER_QA: (),
    ReadingTaskType.SELECTION_EXPLANATION: (),
}


TASK_KEYWORDS: dict[ReadingTaskType, tuple[str, ...]] = {
    ReadingTaskType.RESEARCH_QUESTION: (
        "problem", "question", "objective", "motivation", "challenge",
        "研究问题", "目标", "动机",
    ),
    ReadingTaskType.METHOD: (
        "method", "approach", "architecture", "model", "algorithm", "framework",
        "方法", "模型", "架构",
    ),
    ReadingTaskType.EXPERIMENT: (
        "experiment", "evaluation", "result", "ablation", "baseline", "dataset", "metric",
        "实验", "结果", "消融",
    ),
    ReadingTaskType.REPRODUCIBILITY: (
        "implementation", "training", "hyperparameter", "optimizer", "hardware", "code",
        "appendix", "实现", "训练", "超参数",
    ),
    ReadingTaskType.LIMITATION: (
        "limitation", "failure", "future work", "discussion", "weakness",
        "局限", "失败", "未来工作",
    ),
    ReadingTaskType.SCIENTIFIC_ELEMENTS: (
        "equation", "figure", "table", "formula", "fig.", "公式", "图", "表",
    ),
    ReadingTaskType.PAPER_QA: (),
    ReadingTaskType.SELECTION_EXPLANATION: (),
}


ROLE_PATTERNS: tuple[tuple[SectionRole, tuple[str, ...]], ...] = (
    (SectionRole.REFERENCES, ("references", "bibliography", "参考文献")),
    (SectionRole.LIMITATION, ("limitation", "limitations", "局限", "局限性")),
    (
        SectionRole.RELATED_WORK,
        ("related work", "background", "prior work", "相关工作", "研究背景"),
    ),
    (
        SectionRole.IMPLEMENTATION,
        (
            "implementation", "training detail", "hyperparameter", "setup",
            "实现细节", "训练细节", "实验设置",
        ),
    ),
    (
        SectionRole.EXPERIMENT,
        ("experiment", "evaluation", "benchmark", "ablation", "实验", "评估", "消融"),
    ),
    (SectionRole.RESULT, ("result", "analysis", "finding", "结果", "分析")),
    (
        SectionRole.DISCUSSION,
        ("discussion", "error analysis", "failure case", "讨论", "失败案例"),
    ),
    (SectionRole.CONCLUSION, ("conclusion", "summary", "concluding", "结论", "总结")),
    (
        SectionRole.METHOD,
        (
            "method", "approach", "model", "architecture", "algorithm",
            "方法", "模型", "架构", "算法",
        ),
    ),
    (SectionRole.INTRODUCTION, ("introduction", "overview", "引言", "介绍")),
    (SectionRole.ABSTRACT, ("abstract", "摘要")),
    (SectionRole.APPENDIX, ("appendix", "supplement", "附录", "补充材料")),
)


HEADING_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*)|(?:[ivxlcdm]+))?[\s.\-:、]*",
    re.IGNORECASE,
)


def normalize_selected_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\u00ad", "")
    normalized = re.sub(
        r"(?<=[A-Za-z0-9])-\s*\n\s*(?=[A-Za-z0-9])",
        "",
        normalized,
    )
    normalized = normalized.translate(
        str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"})
    )
    normalized = re.sub(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _selection_candidates(
    needle: str,
    chunks: list[KnowledgeChunk],
    *,
    limit: int = 3,
) -> list[DegradationCandidate]:
    needle_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", needle))
    ranked: list[tuple[float, str, KnowledgeChunk]] = []
    for chunk in chunks:
        normalized = normalize_selected_text(chunk.text)
        if not normalized:
            continue
        windows = [
            normalized[index : index + 240]
            for index in range(0, max(1, len(normalized)), 160)
        ]
        best_window = max(
            windows,
            key=lambda value: SequenceMatcher(None, needle, value).ratio(),
        )
        ratio = SequenceMatcher(None, needle, best_window).ratio()
        window_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", best_window))
        overlap = len(needle_tokens & window_tokens) / max(1, len(needle_tokens))
        score = max(ratio, overlap)
        if score > 0:
            ranked.append((score, best_window, chunk))
    ranked.sort(key=lambda item: (-item[0], item[2].page or 0, item[2].chunk_id))
    return [
        DegradationCandidate(
            page_number=chunk.page,
            object_id=(
                chunk.document_object_ids[0]
                if chunk.document_object_ids
                else chunk.chunk_id
            ),
            snippet=window[:240],
        )
        for _, window, chunk in ranked[:limit]
    ]


MAIN_RESULT_TABLE_MARKERS = (
    "main result",
    "experimental result",
    "performance comparison",
    "benchmark result",
    "ablation",
    "accuracy",
    "precision",
    "recall",
    "f1",
    "bleu",
    "auc",
    "主要结果",
    "主结果",
    "实验结果",
    "性能对比",
    "消融",
    "准确率",
)

CONFIGURATION_TABLE_MARKERS = (
    "hyperparameter",
    "configuration",
    "parameter setting",
    "training setting",
    "implementation detail",
    "超参数",
    "参数设置",
    "训练设置",
    "配置表",
    "实现细节",
)


def scientific_object_importance(
    element_type: str,
    label: str,
    section_path: Iterable[str],
    content: str,
    chunks: Iterable[KnowledgeChunk],
) -> tuple[int, list[str]]:
    """Rank located objects by bounded paper evidence and scientific role."""
    label_variants = {label.casefold()}
    if element_type == "FIGURE":
        label_variants.add(label.casefold().replace("figure ", "fig. "))
    mention_count = sum(
        sum(chunk.text.casefold().count(variant) for variant in label_variants)
        for chunk in chunks
    )
    weighted_mentions = min(mention_count, 3) if element_type == "TABLE" else mention_count
    score = weighted_mentions * 10
    reasons = [f"paper_mentions={mention_count}"]
    section = " ".join(section_path).casefold()
    descriptor = f"{section} {content.casefold()}"
    if any(keyword in section for keyword in ("method", "approach", "model", "方法", "模型")):
        score += 4
        reasons.append("core_method_section")
    if any(
        keyword in section
        for keyword in (
            "experiment",
            "result",
            "evaluation",
            "ablation",
            "实验",
            "结果",
            "消融",
        )
    ):
        score += 6 if element_type in {"FIGURE", "TABLE"} else 3
        reasons.append("experiment_or_result_section")
    if element_type == "TABLE" and any(
        marker in descriptor for marker in MAIN_RESULT_TABLE_MARKERS
    ):
        score += 40
        reasons.append("main_result_table")
    if element_type == "TABLE" and any(
        marker in descriptor for marker in CONFIGURATION_TABLE_MARKERS
    ):
        score -= 20
        reasons.append("configuration_table_penalty")
    if len(content.strip()) >= 80:
        score += 2
        reasons.append("substantial_object_content")
    return score, reasons


DEPTH_BUDGETS: dict[str, dict[ReadingTaskType, ContextBudget]] = {
    "OVERVIEW": {
        ReadingTaskType.BASE_READING: ContextBudget(10, 28_000, 0),
        ReadingTaskType.RESEARCH_QUESTION: ContextBudget(5, 14_000, 0),
        ReadingTaskType.METHOD: ContextBudget(6, 16_000, 0),
        ReadingTaskType.EXPERIMENT: ContextBudget(6, 16_000, 0),
        ReadingTaskType.REPRODUCIBILITY: ContextBudget(5, 14_000, 0),
        ReadingTaskType.LIMITATION: ContextBudget(5, 14_000, 0),
        ReadingTaskType.SCIENTIFIC_ELEMENTS: ContextBudget(8, 20_000, 0),
        ReadingTaskType.PAPER_QA: ContextBudget(5, 14_000, 0),
        ReadingTaskType.SELECTION_EXPLANATION: ContextBudget(3, 10_000, 1),
    },
    "STANDARD": {
        ReadingTaskType.BASE_READING: ContextBudget(20, 56_000, 1),
        ReadingTaskType.RESEARCH_QUESTION: ContextBudget(8, 22_000, 1),
        ReadingTaskType.METHOD: ContextBudget(12, 34_000, 1),
        ReadingTaskType.EXPERIMENT: ContextBudget(14, 40_000, 1),
        ReadingTaskType.REPRODUCIBILITY: ContextBudget(12, 34_000, 1),
        ReadingTaskType.LIMITATION: ContextBudget(8, 22_000, 1),
        ReadingTaskType.SCIENTIFIC_ELEMENTS: ContextBudget(16, 44_000, 1),
        ReadingTaskType.PAPER_QA: ContextBudget(8, 22_000, 1),
        ReadingTaskType.SELECTION_EXPLANATION: ContextBudget(5, 14_000, 1),
    },
    "DEEP": {
        ReadingTaskType.BASE_READING: ContextBudget(32, 90_000, 1),
        ReadingTaskType.RESEARCH_QUESTION: ContextBudget(12, 32_000, 1),
        ReadingTaskType.METHOD: ContextBudget(20, 56_000, 1),
        ReadingTaskType.EXPERIMENT: ContextBudget(24, 68_000, 1),
        ReadingTaskType.REPRODUCIBILITY: ContextBudget(20, 56_000, 1),
        ReadingTaskType.LIMITATION: ContextBudget(12, 32_000, 1),
        ReadingTaskType.SCIENTIFIC_ELEMENTS: ContextBudget(28, 78_000, 2),
        ReadingTaskType.PAPER_QA: ContextBudget(12, 32_000, 1),
        ReadingTaskType.SELECTION_EXPLANATION: ContextBudget(7, 20_000, 2),
    },
}


class ContextRouter:
    """Pure, deterministic routing over already-located Chunks and DocumentIR."""

    def __init__(
        self,
        budgets: dict[str, dict[ReadingTaskType, ContextBudget]] | None = None,
    ) -> None:
        self.budgets = budgets or DEPTH_BUDGETS

    @staticmethod
    def section_role(section_path: list[str] | None) -> SectionRole:
        title = " ".join(section_path or []).casefold()
        for role, patterns in ROLE_PATTERNS:
            if any(pattern in title for pattern in patterns):
                return role
        return SectionRole.OTHER

    @classmethod
    def chunk_role(cls, chunk: KnowledgeChunk) -> SectionRole:
        """Use explicit in-text headings only when structured section lineage is unavailable."""
        structured = cls.section_role(chunk.section)
        if structured != SectionRole.OTHER:
            return structured
        for raw_line in chunk.text.splitlines():
            line = re.sub(
                r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])",
                "",
                raw_line.strip(),
            )
            line = re.sub(r"(?<=[A-Z])\s+(?=[A-Z](?:\s|[A-Z]))", "", line)
            heading = HEADING_PREFIX_PATTERN.sub("", line).casefold()
            for role, patterns in ROLE_PATTERNS:
                if any(
                    heading == pattern
                    or heading.startswith(pattern + " ")
                    or heading.startswith(pattern + ":")
                    for pattern in patterns
                ):
                    return role
        return SectionRole.OTHER

    def build_plan(
        self,
        request: ReadingRequest,
        chunks: Iterable[KnowledgeChunk],
        document_ir: DocumentIR,
    ) -> ReadingPlan:
        chunk_list = tuple(chunks)
        self._validate_inputs(chunk_list, document_ir)
        focus = set(request.focus_aspects)
        enabled = {
            ReadingTaskType.BASE_READING: True,
            ReadingTaskType.RESEARCH_QUESTION: "RESEARCH_QUESTION" in focus,
            ReadingTaskType.METHOD: bool({"METHOD", "INNOVATION"} & focus),
            ReadingTaskType.EXPERIMENT: "EXPERIMENT" in focus,
            ReadingTaskType.REPRODUCIBILITY: "REPRODUCIBILITY" in focus,
            ReadingTaskType.LIMITATION: "LIMITATION" in focus,
            ReadingTaskType.SCIENTIFIC_ELEMENTS: bool({"EQUATION", "FIGURE", "TABLE"} & focus),
            ReadingTaskType.PAPER_QA: True,
            ReadingTaskType.SELECTION_EXPLANATION: True,
        }
        scientific_object_ids = self._select_scientific_objects(request, chunk_list, document_ir)
        missing_scientific_type = self._requested_scientific_type_is_unlocated(
            request,
            document_ir,
        )
        tasks: list[PlannedReadingTask] = []
        for task_type in ReadingTaskType:
            object_ids = (
                scientific_object_ids
                if task_type == ReadingTaskType.SCIENTIFIC_ELEMENTS
                else ()
            )
            routed_object_ids = (
                None
                if task_type == ReadingTaskType.SCIENTIFIC_ELEMENTS
                and missing_scientific_type
                else set(object_ids) or None
            )
            selection = self.route(
                task_type,
                request,
                chunk_list,
                document_ir,
                object_ids=routed_object_ids,
            )
            routing_strategy = selection.routing_strategy
            if task_type == ReadingTaskType.SCIENTIFIC_ELEMENTS and missing_scientific_type:
                routing_strategy += " > missing_scientific_type_text_fallback"
            tasks.append(
                PlannedReadingTask(
                    task_type=task_type,
                    enabled=enabled[task_type],
                    preferred_section_roles=selection.preferred_roles,
                    selected_chunk_ids=tuple(chunk.chunk_id for chunk in selection.chunks),
                    selected_object_ids=(
                        tuple(object_ids)
                        if task_type == ReadingTaskType.SCIENTIFIC_ELEMENTS
                        else selection.object_ids
                    ),
                    context_character_count=selection.character_count,
                    routing_strategy=routing_strategy,
                    fallback_reason=selection.fallback_reason,
                )
            )
        enabled_names = [item.task_type.value for item in tasks if item.enabled]
        return ReadingPlan(
            goal=request.reading_goal,
            depth=request.depth,
            requested_aspects=tuple(request.focus_aspects),
            planned_tasks=tuple(tasks),
            planning_summary=(
                f"depth={request.depth}; enabled={','.join(enabled_names)}; "
                f"base_chunks={len(tasks[0].selected_chunk_ids)}; "
                f"scientific_objects={len(scientific_object_ids)}"
            ),
        )

    def route(
        self,
        task_type: ReadingTaskType,
        request: ReadingRequest,
        chunks: Iterable[KnowledgeChunk],
        document_ir: DocumentIR,
        *,
        question: str | None = None,
        page_numbers: set[int] | None = None,
        section_path: list[str] | None = None,
        chunk_ids: set[str] | None = None,
        object_ids: set[str] | None = None,
        selected_text: str | None = None,
    ) -> ContextSelection:
        chunk_list = tuple(chunks)
        self._validate_inputs(chunk_list, document_ir)
        budget = self.budgets[request.depth][task_type]
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunk_list}
        known_object_ids = self._known_object_ids(document_ir)
        if chunk_ids is not None:
            unknown = chunk_ids - set(chunk_by_id)
            if unknown:
                raise ReadingStageError("CHUNK_SCOPE_NOT_FOUND")
        if object_ids is not None:
            unknown = object_ids - known_object_ids
            if unknown:
                raise ReadingStageError("SCIENTIFIC_OBJECT_NOT_FOUND")

        explicit_scope = any(
            value is not None
            for value in (chunk_ids, object_ids, page_numbers, section_path, selected_text)
        )
        candidates = list(chunk_list)
        strategy: list[str] = []
        if chunk_ids is not None:
            candidates = [chunk for chunk in candidates if chunk.chunk_id in chunk_ids]
            strategy.append("explicit_chunks")
        if object_ids is not None:
            candidates = self._object_related_chunks(chunk_list, document_ir, object_ids)
            strategy.append("object_lineage")
        if page_numbers is not None:
            page_block_ids = {
                block.object_id
                for block in document_ir.text_blocks
                if block.page_number in page_numbers
            }
            candidates = [
                chunk for chunk in candidates
                if chunk.page in page_numbers
                or page_block_ids.intersection(chunk.document_object_ids)
            ]
            strategy.append("explicit_pages")
        if section_path is not None:
            candidates = [
                chunk for chunk in candidates
                if (chunk.section or [])[: len(section_path)] == section_path
            ]
            strategy.append("explicit_section")
        if selected_text is not None:
            needle = selected_text.strip()
            if not needle:
                raise ReadingStageError("SELECTED_TEXT_REQUIRED")
            normalized_needle = normalize_selected_text(needle)
            matching_ids = {
                chunk.chunk_id
                for chunk in candidates
                if normalized_needle in normalize_selected_text(chunk.text)
            }
            if not matching_ids:
                raise ReadingStageError(
                    "SELECTED_TEXT_NOT_FOUND",
                    candidates=_selection_candidates(normalized_needle, candidates),
                )
            candidates = self._with_neighbors(
                chunk_list,
                matching_ids,
                budget.neighbor_radius,
                allowed_ids={chunk.chunk_id for chunk in candidates},
            )
            strategy.append("selected_text_locality")
        if explicit_scope and not candidates:
            raise ReadingStageError("QUESTION_SCOPE_EMPTY")

        roles = self._preferred_roles(task_type, request)
        if explicit_scope:
            scores = {chunk.chunk_id: 1000 for chunk in candidates}
        else:
            scores = self._scores(
                task_type,
                request,
                candidates,
                roles,
                question=question,
                object_ids=object_ids,
            )
            strategy.extend(("section_roles", "object_and_lexical_relevance"))
            positive = {chunk_id for chunk_id, score in scores.items() if score > 0}
            if positive:
                if budget.neighbor_radius:
                    neighbors = self._with_neighbors(
                        chunk_list,
                        positive,
                        budget.neighbor_radius,
                    )
                    for chunk in neighbors:
                        scores.setdefault(chunk.chunk_id, 1)
                    candidates = neighbors
                else:
                    candidates = [
                        chunk for chunk in candidates if chunk.chunk_id in positive
                    ]
            else:
                candidates = []

        selected = self._bounded_select(candidates, scores, budget, chunk_list)
        fallback_reason = None
        if not selected and not explicit_scope:
            selected = self._document_fallback(chunk_list, budget)
            fallback_reason = (
                "No preferred section or lexical match; "
                "used bounded document-order fallback."
            )
            strategy.append("bounded_document_fallback")
        elif not explicit_scope and not any(
            scores.get(chunk.chunk_id, 0) >= 100 for chunk in selected
        ):
            fallback_reason = (
                "Preferred sections were unavailable; "
                "used lexical relevance and bounded document order."
            )
        return ContextSelection(
            chunks=tuple(selected),
            object_ids=tuple(sorted(object_ids or ())),
            preferred_roles=roles,
            routing_strategy=" > ".join(dict.fromkeys(strategy)) or "explicit_scope",
            fallback_reason=fallback_reason,
        )

    def chunks_from_plan(
        self,
        plan: ReadingPlan,
        task_type: ReadingTaskType,
        chunks: Iterable[KnowledgeChunk],
    ) -> tuple[KnowledgeChunk, ...]:
        selected = set(plan.task(task_type).selected_chunk_ids)
        return tuple(chunk for chunk in chunks if chunk.chunk_id in selected)

    def _preferred_roles(
        self,
        task_type: ReadingTaskType,
        request: ReadingRequest,
    ) -> tuple[SectionRole, ...]:
        if task_type != ReadingTaskType.BASE_READING:
            return TASK_ROLES.get(task_type, ())
        roles = [SectionRole.ABSTRACT, SectionRole.INTRODUCTION, SectionRole.CONCLUSION]
        focus_tasks = {
            "RESEARCH_QUESTION": ReadingTaskType.RESEARCH_QUESTION,
            "METHOD": ReadingTaskType.METHOD,
            "INNOVATION": ReadingTaskType.METHOD,
            "EXPERIMENT": ReadingTaskType.EXPERIMENT,
            "REPRODUCIBILITY": ReadingTaskType.REPRODUCIBILITY,
            "LIMITATION": ReadingTaskType.LIMITATION,
            "EQUATION": ReadingTaskType.SCIENTIFIC_ELEMENTS,
            "FIGURE": ReadingTaskType.SCIENTIFIC_ELEMENTS,
            "TABLE": ReadingTaskType.SCIENTIFIC_ELEMENTS,
        }
        for focus in request.focus_aspects:
            roles.extend(TASK_ROLES[focus_tasks[focus]])
        return tuple(dict.fromkeys(roles))

    def _scores(
        self,
        task_type: ReadingTaskType,
        request: ReadingRequest,
        chunks: list[KnowledgeChunk],
        roles: tuple[SectionRole, ...],
        *,
        question: str | None,
        object_ids: set[str] | None,
    ) -> dict[str, int]:
        query = " ".join(
            filter(
                None,
                (
                    request.reading_goal,
                    question,
                    " ".join(TASK_KEYWORDS.get(task_type, ())),
                ),
            )
        )
        query_tokens = self._tokens(query)
        scores: dict[str, int] = {}
        for index, chunk in enumerate(chunks):
            score = 0
            role = self.chunk_role(chunk)
            if role in roles:
                score += 200 - roles.index(role) * 3
            if object_ids and object_ids.intersection(chunk.document_object_ids):
                score += 400
            if task_type == ReadingTaskType.SCIENTIFIC_ELEMENTS and chunk.content_type in {
                "EQUATION", "FIGURE", "TABLE", "CAPTION"
            }:
                score += 160
            overlap = query_tokens.intersection(self._tokens(chunk.text))
            score += min(90, len(overlap) * 15)
            if role == SectionRole.REFERENCES:
                score -= 300
            scores[chunk.chunk_id] = score - index // 1000
        return scores

    @staticmethod
    def _bounded_select(
        candidates: list[KnowledgeChunk],
        scores: dict[str, int],
        budget: ContextBudget,
        document_order: tuple[KnowledgeChunk, ...],
    ) -> list[KnowledgeChunk]:
        order = {chunk.chunk_id: index for index, chunk in enumerate(document_order)}
        ranked = sorted(
            candidates,
            key=lambda chunk: (-scores.get(chunk.chunk_id, 0), order[chunk.chunk_id]),
        )
        selected: list[KnowledgeChunk] = []
        characters = 0
        for chunk in ranked:
            if len(selected) >= budget.max_chunks:
                break
            if characters + len(chunk.text) > budget.max_characters:
                continue
            selected.append(chunk)
            characters += len(chunk.text)
        return sorted(selected, key=lambda chunk: order[chunk.chunk_id])

    @staticmethod
    def _document_fallback(
        chunks: tuple[KnowledgeChunk, ...], budget: ContextBudget
    ) -> list[KnowledgeChunk]:
        selected: list[KnowledgeChunk] = []
        characters = 0
        for chunk in chunks:
            if len(selected) >= budget.max_chunks:
                break
            if characters + len(chunk.text) > budget.max_characters:
                continue
            selected.append(chunk)
            characters += len(chunk.text)
        return selected

    @staticmethod
    def _with_neighbors(
        chunks: tuple[KnowledgeChunk, ...],
        seed_ids: set[str],
        radius: int,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[KnowledgeChunk]:
        indices = [index for index, chunk in enumerate(chunks) if chunk.chunk_id in seed_ids]
        selected_indices: set[int] = set()
        for index in indices:
            for neighbor in range(max(0, index - radius), min(len(chunks), index + radius + 1)):
                if allowed_ids is None or chunks[neighbor].chunk_id in allowed_ids:
                    selected_indices.add(neighbor)
        return [chunks[index] for index in sorted(selected_indices)]

    def _object_related_chunks(
        self,
        chunks: tuple[KnowledgeChunk, ...],
        document_ir: DocumentIR,
        object_ids: set[str],
    ) -> list[KnowledgeChunk]:
        objects = {
            item.object_id: item
            for item in (*document_ir.equations, *document_ir.figures, *document_ir.tables)
        }
        targets = [objects[object_id] for object_id in sorted(object_ids)]
        labels = {target.label.casefold() for target in targets}
        pages = {target.page_number for target in targets}
        referring_block_ids = {
            block.object_id
            for block in document_ir.text_blocks
            if any(label in block.text.casefold() for label in labels)
        }
        seed_ids = {
            chunk.chunk_id
            for chunk in chunks
            if object_ids.intersection(chunk.document_object_ids)
            or referring_block_ids.intersection(chunk.document_object_ids)
            or any(label in chunk.text.casefold() for label in labels)
            or (
                chunk.page in pages
                and chunk.content_type in {"CAPTION", "EQUATION", "FIGURE", "TABLE"}
            )
        }
        if not seed_ids:
            seed_ids = {chunk.chunk_id for chunk in chunks if chunk.page in pages}
        return self._with_neighbors(chunks, seed_ids, 1)

    def _select_scientific_objects(
        self,
        request: ReadingRequest,
        chunks: tuple[KnowledgeChunk, ...],
        document_ir: DocumentIR,
    ) -> tuple[str, ...]:
        focus_types = {
            name for name in ("EQUATION", "FIGURE", "TABLE") if name in request.focus_aspects
        }
        if not focus_types:
            return ()
        per_type = {"OVERVIEW": 1, "STANDARD": 2, "DEEP": 4}[request.depth]
        selected: list[str] = []
        groups = {
            "EQUATION": document_ir.equations,
            "FIGURE": document_ir.figures,
            "TABLE": document_ir.tables,
        }
        for element_type in ("EQUATION", "FIGURE", "TABLE"):
            if element_type not in focus_types:
                continue
            ranked = sorted(
                groups[element_type],
                key=lambda item: (
                    -scientific_object_importance(
                        element_type,
                        item.label,
                        item.section_path,
                        item.content,
                        chunks,
                    )[0],
                    item.page_number,
                    item.label,
                    item.object_id,
                ),
            )
            selected.extend(item.object_id for item in ranked[:per_type])
        return tuple(selected)

    @staticmethod
    def _requested_scientific_type_is_unlocated(
        request: ReadingRequest,
        document_ir: DocumentIR,
    ) -> bool:
        located = {
            "EQUATION": bool(document_ir.equations),
            "FIGURE": bool(document_ir.figures),
            "TABLE": bool(document_ir.tables),
        }
        return any(
            focus in located and not located[focus]
            for focus in request.focus_aspects
        )

    @staticmethod
    def _known_object_ids(document_ir: DocumentIR) -> set[str]:
        return {
            item.object_id
            for item in (*document_ir.equations, *document_ir.figures, *document_ir.tables)
        }

    @staticmethod
    def _validate_inputs(
        chunks: tuple[KnowledgeChunk, ...], document_ir: DocumentIR
    ) -> None:
        if not chunks:
            raise ValueError("Context Router requires at least one Chunk")
        if any(chunk.paper_id != document_ir.paper_id for chunk in chunks):
            raise ValueError("Context Router received a Chunk outside DocumentIR paper scope")
        ids = [chunk.chunk_id for chunk in chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("Context Router requires unique Chunk IDs")

    @staticmethod
    def _tokens(text: str) -> set[str]:
        latin = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.casefold())
        chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        chinese = {
            token
            for run in chinese_runs
            for token in (run, *(run[index : index + 2] for index in range(len(run) - 1)))
        }
        return set(latin) | chinese


def render_routing_summary(plan: ReadingPlan) -> str:
    lines = ["# 上下文路由摘要", "", f"- {plan.planning_summary}"]
    for task in plan.planned_tasks:
        if not task.enabled:
            continue
        fallback = f"；回退：{task.fallback_reason}" if task.fallback_reason else ""
        lines.append(
            f"- `{task.task_type}`：{len(task.selected_chunk_ids)} Chunks / "
            f"{task.context_character_count} 字符 / "
            f"{len(task.selected_object_ids)} 对象{fallback}"
        )
    return "\n".join(lines).rstrip() + "\n"
