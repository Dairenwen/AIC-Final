from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Literal

from llm.experiments import ExperimentAnalysis
from llm.gateway import ClaimSupportCheck
from llm.scientific_elements import ScientificElementAnalysis
from pydantic import BaseModel, ConfigDict, Field
from schemas.models import ReadingClaim, ReadingResult


class ReliabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReliabilitySource(StrEnum):
    AUTHOR_STATEMENT = "AUTHOR_STATEMENT"
    EVIDENCE_SUMMARY = "EVIDENCE_SUMMARY"
    AGENT_INFERENCE = "AGENT_INFERENCE"


class LimitationSource(StrEnum):
    AUTHOR_ACKNOWLEDGED = "AUTHOR_ACKNOWLEDGED"
    AGENT_INFERRED = "AGENT_INFERRED"


class ReliabilityRecord(BaseModel):
    """Internal audit record; it deliberately does not change the public Claim schema."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_type: str
    status: ReliabilityStatus
    source: ReliabilitySource
    original_content: str
    final_content: str | None = None
    review_candidate_content: str | None = None
    unsupported_fragments: list[str] = Field(default_factory=list)
    reason: str
    limitation_source: LimitationSource | None = None


class CoreReliabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["core_reliability_v1"] = "core_reliability_v1"
    records: list[ReliabilityRecord] = Field(default_factory=list)


@dataclass(frozen=True)
class _Decision:
    status: ReliabilityStatus
    content: str | None
    unsupported_fragments: tuple[str, ...]
    reason: str
    review_candidate_content: str | None = None


class ClaimEvidenceReliabilityGuard:
    """Conservatively constrain high-value statements to their bound Evidence."""

    _CLAUSE_SPLIT = re.compile(r"[。！？!?；;]\s*|\.(?![A-Za-z0-9])\s*|，\s*")
    _NUMBER = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?", re.IGNORECASE)
    _LATIN_TOKEN = re.compile(r"[A-Za-zΑ-Ωα-ω][A-Za-z0-9_Α-Ωα-ω+./-]*")
    _CHINESE = re.compile(r"[\u4e00-\u9fff]")

    _CAUSAL = (
        "导致", "证明", "因此必然", "因而必然", "causes", "caused by", "proves",
        "therefore necessarily", "must result", "必然带来",
    )
    _NECESSITY = (
        "必须", "需要更高", "需要更大", "要求更高", "require higher", "requires higher",
        "need higher", "must use", "is necessary", "are necessary",
    )
    _UNIVERSAL = (
        "所有任务", "所有模型", "普遍有效", "普遍规律", "通用地", "始终",
        "all tasks", "all models", "universally", "general rule", "always",
    )
    _AUTHOR_ATTRIBUTION = (
        "作者指出", "作者证明", "作者认为", "论文明确表明", "论文指出", "论文证明",
        "the authors state", "the authors show", "the paper explicitly", "the paper proves",
    )
    _COMPARATIVE = (
        "优于", "更优", "最佳", "胜过", "提高", "提升", "降低", "下降",
        "outperform", "superior", "better than", "best", "improve", "increase", "decrease",
    )
    _CONTROLLED_EVIDENCE = (
        "消融", "对照", "控制变量", "移除", "去掉", "without", "ablation", "controlled",
        "control group", "remove", "removed", "intervention", "causal experiment",
    )
    _HIGH_RISK_QUALIFIER_GROUPS = (
        (
            re.compile(r"首次|首个|首创"),
            re.compile(
                r"\bfirst-ever\b|\bthe\s+first\b|\bfor\s+the\s+first\s+time\b|"
                r"\bfirst\s+(?=(?:work|study|survey|method|approach|framework|taxonomy|"
                r"model|system|algorithm|architecture|technique)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            re.compile(r"唯一(?:的)?"),
            re.compile(r"\bthe\s+only\b|(?<![-\w])only(?![-\w])", re.IGNORECASE),
            re.compile(r"\bunique\b", re.IGNORECASE),
        ),
        (
            re.compile(r"最全面(?:的)?"),
            re.compile(r"\b(?:the\s+)?most\s+comprehensive\b", re.IGNORECASE),
        ),
    )
    _STATE_OF_THE_ART = re.compile(r"\bstate[- ]of[- ]the[- ]art\b", re.IGNORECASE)
    _STATE_OF_THE_ART_ASSERTION = re.compile(
        r"(?:achiev\w*|attain\w*|establish\w*|set\w*|is|are|remains?)"
        r".{0,80}\bstate[- ]of[- ]the[- ]art\b|"
        r"\bstate[- ]of[- ]the[- ]art\b.{0,80}"
        r"(?:across|overall|among|all\b|to date|ever|worldwide)",
        re.IGNORECASE,
    )
    _KNOWLEDGE_FRAMEWORK_CONSTRUCTION = re.compile(
        r"(?:构建|建立|打造).{0,100}知识框架|"
        r"\b(?:build|construct|establish|create)\b.{0,100}\bknowledge framework\b",
        re.IGNORECASE,
    )
    _KNOWLEDGE_FRAMEWORK = re.compile(r"知识框架|\bknowledge framework\b", re.IGNORECASE)
    _FRAMEWORK_SCOPE_QUALIFIERS = (
        re.compile(r"全面|\bcomprehensive\b", re.IGNORECASE),
        re.compile(r"可复现|\breproducible\b", re.IGNORECASE),
        re.compile(r"面向未来|未来导向|\bfuture[- ]oriented\b", re.IGNORECASE),
    )
    _BASE_RELATION_GROUPS = (
        ("提出", "propose", "present", "introduce"),
        ("设计", "design"),
        ("采用", "use", "employ", "adopt"),
        ("依据", "based on", "according to"),
        ("包含", "包括", "include", "contain", "comprise"),
        ("分类", "taxonomy", "classification", "categorize"),
        ("综述", "survey", "overview", "review"),
    )
    _DANGLING_REDUCTION_ENDINGS = (
        "进行",
        "以",
        "为",
        "和",
        "与",
        "及",
        "或",
        "的",
        "是",
        "通过",
        "采用",
        "包括",
        "包含",
        "构建",
        "提出",
        " to",
        " and",
        " or",
        " with",
        " by",
        " for",
        " of",
        " the",
        " a",
        " an",
        " is",
        " are",
        " using",
        " including",
    )

    _SOURCE_MAP = {
        "AUTHOR_STATED": ReliabilitySource.AUTHOR_STATEMENT,
        "EVIDENCE_DERIVED": ReliabilitySource.EVIDENCE_SUMMARY,
        "AGENT_INFERRED": ReliabilitySource.AGENT_INFERENCE,
    }

    def __init__(
        self,
        semantic_checker: object | None = None,
        *,
        max_semantic_workers: int = 1,
    ) -> None:
        if max_semantic_workers < 1:
            raise ValueError("max_semantic_workers must be positive")
        self.semantic_checker = semantic_checker
        self.max_semantic_workers = max_semantic_workers

    def consolidate_reading_result(
        self, result: ReadingResult
    ) -> tuple[ReadingResult, tuple[ReliabilityRecord, ...]]:
        evidence_by_id = {item.evidence_id: item.evidence_text for item in result.evidence}
        kept_claims: list[ReadingClaim] = []
        kept_ids: set[str] = set()
        records: list[ReliabilityRecord] = []

        def evaluate_claim(
            claim: ReadingClaim,
        ) -> tuple[ReliabilitySource, _Decision]:
            evidence = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
            source = self._SOURCE_MAP[claim.claim_source]
            decision = self.evaluate(claim.content, evidence, source)
            return source, decision

        if self.max_semantic_workers > 1 and len(result.claims) > 1:
            with ThreadPoolExecutor(
                max_workers=min(self.max_semantic_workers, len(result.claims)),
                thread_name_prefix="claim-evidence-check",
            ) as executor:
                evaluations = list(executor.map(evaluate_claim, result.claims))
        else:
            evaluations = [evaluate_claim(claim) for claim in result.claims]

        for claim, (source, decision) in zip(result.claims, evaluations, strict=True):
            final_content = decision.content
            if final_content:
                kept_claims.append(claim.model_copy(update={"content": final_content}))
                kept_ids.add(claim.claim_id)
            else:
                kept_claims.append(
                    claim.model_copy(
                        update={
                            "content": (
                                "该候选结论未进入可靠核心结论；"
                                "如需查看，请检查 Claim–Evidence 可靠性记录。"
                                if decision.review_candidate_content
                                else "证据不足，原结论未作为可靠核心结论保留。"
                            )
                        }
                    )
                )
            records.append(
                self._record(
                    claim.claim_id,
                    claim.claim_type,
                    source,
                    claim.content,
                    decision,
                    limitation=claim.claim_type == "LIMITATION",
                )
            )

        section_fields = (
            "research_questions",
            "method_structure",
            "key_equations_and_figures",
            "experiment_findings",
            "innovations",
            "limitations",
        )
        updates = {field: [item for item in getattr(result, field) if item in kept_ids] for field in section_fields}
        updates["claims"] = kept_claims
        return result.model_copy(update=updates), tuple(records)

    def consolidate_experiments(
        self,
        analysis: ExperimentAnalysis,
        evidence_by_chunk: dict[str, str],
    ) -> tuple[ExperimentAnalysis, tuple[ReliabilityRecord, ...]]:
        findings = []
        assessments = []
        records: list[ReliabilityRecord] = []
        for index, finding in enumerate(analysis.findings, start=1):
            decision = self.evaluate(
                finding.content,
                self._evidence(finding.chunk_ids, evidence_by_chunk),
                ReliabilitySource.EVIDENCE_SUMMARY,
            )
            item_id = f"experiment_finding_{index:03d}"
            records.append(self._record(item_id, "EXPERIMENT_FINDING", ReliabilitySource.EVIDENCE_SUMMARY, finding.content, decision))
            if decision.content:
                findings.append(finding.model_copy(update={"content": decision.content}))
        for index, assessment in enumerate(analysis.conclusion_assessments, start=1):
            combined = f"{assessment.conclusion}；{assessment.reason}"
            decision = self.evaluate(
                combined,
                self._evidence(assessment.chunk_ids, evidence_by_chunk),
                ReliabilitySource.AGENT_INFERENCE,
            )
            item_id = f"conclusion_assessment_{index:03d}"
            records.append(self._record(item_id, "CONCLUSION", ReliabilitySource.AGENT_INFERENCE, combined, decision))
            if decision.content:
                conclusion, separator, reason = decision.content.partition("；")
                assessments.append(
                    assessment.model_copy(
                        update={
                            "conclusion": conclusion,
                            "reason": reason if separator and reason else "该判断仅限于绑定 Evidence。",
                        }
                    )
                )
        return analysis.model_copy(update={"findings": findings, "conclusion_assessments": assessments}), tuple(records)

    def consolidate_scientific(
        self,
        analysis: ScientificElementAnalysis,
        evidence_by_chunk: dict[str, str],
    ) -> tuple[ScientificElementAnalysis, tuple[ReliabilityRecord, ...]]:
        elements = []
        records: list[ReliabilityRecord] = []
        for element in analysis.elements:
            evidence = self._evidence(element.chunk_ids, evidence_by_chunk)
            if element.table_checks:
                structured = " ".join(
                    f"{check.metric} {check.scope} {check.baseline_label} {check.baseline_value:g} "
                    f"{check.target_label} {check.target_value:g}"
                    for check in element.table_checks
                )
                evidence = [*evidence, structured]
            explanation_decision = self.evaluate(
                element.explanation,
                evidence,
                ReliabilitySource.EVIDENCE_SUMMARY,
                structured_binding=True,
            )
            records.append(
                self._record(
                    f"{element.element_id}:explanation",
                    "EQUATION_FIGURE",
                    ReliabilitySource.EVIDENCE_SUMMARY,
                    element.explanation,
                    explanation_decision,
                )
            )
            findings = []
            for index, finding in enumerate(element.findings, start=1):
                decision = self.evaluate(
                    finding,
                    evidence,
                    ReliabilitySource.EVIDENCE_SUMMARY,
                    structured_binding=True,
                )
                records.append(
                    self._record(
                        f"{element.element_id}:finding:{index}",
                        "EQUATION_FIGURE",
                        ReliabilitySource.EVIDENCE_SUMMARY,
                        finding,
                        decision,
                    )
                )
                if decision.content:
                    findings.append(decision.content)
            if explanation_decision.content:
                elements.append(
                    element.model_copy(
                        update={
                            "explanation": explanation_decision.content,
                            "findings": findings,
                        }
                    )
                )
        return ScientificElementAnalysis(elements=elements), tuple(records)

    def evaluate(
        self,
        claim: str,
        evidence: Iterable[str],
        source: ReliabilitySource,
        *,
        structured_binding: bool = False,
    ) -> _Decision:
        original = claim.strip()
        evidence_text = "\n".join(item for item in evidence if item and item.strip())
        if not evidence_text:
            return _Decision(
                ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                None,
                (original,),
                "No bound Evidence text is available.",
            )

        normalized_evidence = evidence_text.casefold()
        fragments = self._clauses(original)
        kept: list[str] = []
        unsupported: list[str] = []
        attribution_rewritten = False
        qualifier_rewritten = False
        for fragment in fragments:
            value = fragment.strip(" \t\n，,；;。.!！?？")
            if not value:
                continue
            lowered = value.casefold()
            if self._has_any(lowered, self._AUTHOR_ATTRIBUTION) and source != ReliabilitySource.AUTHOR_STATEMENT:
                value = self._strip_author_attribution(value).strip(" \t\n，,；;。.!！?？")
                lowered = value.casefold()
                attribution_rewritten = True
            value, unsupported_qualifiers = self._strip_unsupported_high_risk_qualifiers(
                value,
                evidence_text,
            )
            if unsupported_qualifiers:
                unsupported.extend(unsupported_qualifiers)
                qualifier_rewritten = True
                value = value.strip(" \t\n，,；;。.!！?？")
                if not value:
                    continue
                lowered = value.casefold()
            if not self._knowledge_framework_scope_supported(value, evidence_text):
                unsupported.append(fragment.strip())
                continue
            if self._has_any(lowered, self._CAUSAL + self._NECESSITY) and not self._causal_supported(normalized_evidence):
                unsupported.append(fragment.strip())
                continue
            if self._has_any(lowered, self._UNIVERSAL) and not self._has_any(normalized_evidence, self._UNIVERSAL):
                unsupported.append(fragment.strip())
                continue
            if self._has_any(lowered, self._COMPARATIVE) and not self._comparison_supported(normalized_evidence):
                unsupported.append(fragment.strip())
                continue
            if not structured_binding and not self._numbers_supported(value, evidence_text):
                unsupported.append(fragment.strip())
                continue
            kept.append(value)

        final = "，".join(kept).strip("，,；; ") or None
        if final and self._ends_with_sentence(original):
            final += "。" if self._contains_chinese(final) else "."

        if final is None:
            return _Decision(
                ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                None,
                tuple(unsupported or [original]),
                "Bound Evidence does not support the statement's core relation or required qualifiers.",
            )
        if qualifier_rewritten and not self._base_fact_supported_after_qualifier_removal(
            final,
            evidence_text,
        ):
            semantic = self._semantic_check(final, evidence_text, source)
            if semantic is None or semantic.status != "SUPPORTED":
                return _Decision(
                    ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                    None,
                    tuple(unsupported or [original]),
                    "The high-risk qualifier was unsupported and the remaining base fact was not established by bound Evidence.",
                )
        if unsupported or attribution_rewritten:
            return _Decision(
                ReliabilityStatus.PARTIALLY_SUPPORTED,
                final,
                tuple(unsupported),
                "Unsupported novelty, superlative, causal, scope, comparison, numeric, or attribution language was removed.",
            )
        if not structured_binding and not self._core_overlap(final, evidence_text):
            if source == ReliabilitySource.AGENT_INFERENCE:
                return _Decision(
                    ReliabilityStatus.SUPPORTED,
                    final,
                    (),
                    "The inference is explicitly classified and bound to its supporting Evidence.",
                )
            semantic = self._semantic_check(final, evidence_text, source)
            if semantic is None:
                return _Decision(
                    ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                    None,
                    (original,),
                    "Evidence is topically related but deterministic checks cannot establish the core fact.",
                    self._ordinary_review_candidate(original, evidence_text),
                )
            if semantic.status == "SUPPORTED":
                return _Decision(
                    ReliabilityStatus.SUPPORTED,
                    final,
                    (),
                    semantic.reason,
                )
            unsupported_semantic = tuple(semantic.unsupported_fragments or [original])
            if semantic.status == "PARTIALLY_SUPPORTED":
                reduced = final
                for fragment in semantic.unsupported_fragments:
                    reduced = reduced.replace(fragment, "")
                reduced = reduced.strip(" \t\n，,；;。.!！?？")
                if reduced and self._is_meaningful_semantic_reduction(reduced):
                    reduced += "。" if self._contains_chinese(reduced) else "."
                    return _Decision(
                        ReliabilityStatus.PARTIALLY_SUPPORTED,
                        reduced,
                        unsupported_semantic,
                        semantic.reason,
                    )
                return _Decision(
                    ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                    None,
                    unsupported_semantic,
                    "Removing unsupported semantic fragments would leave a syntactically incomplete Claim.",
                )
            return _Decision(
                ReliabilityStatus.INSUFFICIENT_EVIDENCE,
                None,
                unsupported_semantic,
                semantic.reason,
                self._ordinary_review_candidate(original, evidence_text),
            )
        return _Decision(
            ReliabilityStatus.SUPPORTED,
            final,
            (),
            "Bound Evidence covers the retained statement and its high-risk qualifiers.",
        )

    @staticmethod
    def _evidence(chunk_ids: Iterable[str], evidence_by_chunk: dict[str, str]) -> list[str]:
        return [evidence_by_chunk[item] for item in chunk_ids if item in evidence_by_chunk]

    @staticmethod
    def _record(
        item_id: str,
        item_type: str,
        source: ReliabilitySource,
        original: str,
        decision: _Decision,
        *,
        limitation: bool = False,
    ) -> ReliabilityRecord:
        limitation_source = None
        if limitation:
            limitation_source = (
                LimitationSource.AUTHOR_ACKNOWLEDGED
                if source == ReliabilitySource.AUTHOR_STATEMENT
                else LimitationSource.AGENT_INFERRED
            )
        return ReliabilityRecord(
            item_id=item_id,
            item_type=item_type,
            status=decision.status,
            source=source,
            original_content=original,
            final_content=decision.content,
            unsupported_fragments=list(decision.unsupported_fragments),
            reason=decision.reason,
            review_candidate_content=decision.review_candidate_content,
            limitation_source=limitation_source,
        )

    @classmethod
    def _clauses(cls, value: str) -> list[str]:
        clauses = [item for item in cls._CLAUSE_SPLIT.split(value) if item.strip()]
        return clauses or [value]

    @classmethod
    def _causal_supported(cls, evidence: str) -> bool:
        return cls._has_any(evidence, cls._CAUSAL + cls._NECESSITY + cls._CONTROLLED_EVIDENCE)

    @classmethod
    def _comparison_supported(cls, evidence: str) -> bool:
        return cls._has_any(
            evidence,
            cls._COMPARATIVE
            + cls._CONTROLLED_EVIDENCE
            + ("高于", "低于", "higher", "lower", "increase", "decrease", "提升", "下降"),
        )

    @classmethod
    def _numbers_supported(cls, claim: str, evidence: str) -> bool:
        evidence_numbers = {cls._normalize_number(item) for item in cls._NUMBER.findall(evidence)}
        return all(cls._normalize_number(item) in evidence_numbers for item in cls._NUMBER.findall(claim))

    @staticmethod
    def _normalize_number(value: str) -> str:
        return value.casefold().lstrip("+")

    @classmethod
    def _core_overlap(cls, claim: str, evidence: str) -> bool:
        claim_latin = {item.casefold() for item in cls._LATIN_TOKEN.findall(claim) if len(item) > 1}
        evidence_latin = {item.casefold() for item in cls._LATIN_TOKEN.findall(evidence)}
        if claim_latin:
            shared_latin = claim_latin & evidence_latin
            if shared_latin:
                return True
        claim_chinese = "".join(cls._CHINESE.findall(claim))
        evidence_chinese = "".join(cls._CHINESE.findall(evidence))
        if len(claim_chinese) >= 4:
            claim_bigrams = {claim_chinese[index:index + 2] for index in range(len(claim_chinese) - 1)}
            evidence_bigrams = {evidence_chinese[index:index + 2] for index in range(len(evidence_chinese) - 1)}
            overlap = len(claim_bigrams & evidence_bigrams) / max(1, len(claim_bigrams))
            return overlap >= 0.12
        return bool(set(claim.casefold().split()) & set(evidence.casefold().split()))

    def _semantic_check(
        self,
        claim: str,
        evidence: str,
        source: ReliabilitySource,
    ) -> ClaimSupportCheck | None:
        checker = getattr(self.semantic_checker, "check_claim_support", None)
        if checker is None:
            return None
        try:
            result = checker(claim, evidence, source.value)
            return result if isinstance(result, ClaimSupportCheck) else ClaimSupportCheck.model_validate(result)
        except Exception:
            return None

    @classmethod
    def _strip_author_attribution(cls, value: str) -> str:
        result = value
        for marker in cls._AUTHOR_ATTRIBUTION:
            result = re.sub(re.escape(marker), "", result, flags=re.IGNORECASE)
        return result.lstrip("：:，, ")

    @classmethod
    def _strip_unsupported_high_risk_qualifiers(
        cls,
        claim: str,
        evidence: str,
    ) -> tuple[str, list[str]]:
        result = claim
        unsupported: list[str] = []
        for patterns in cls._HIGH_RISK_QUALIFIER_GROUPS:
            matches = [match.group(0) for pattern in patterns for match in pattern.finditer(result)]
            if not matches or any(pattern.search(evidence) for pattern in patterns):
                continue
            unsupported.extend(matches)
            for pattern in patterns:
                result = pattern.sub("", result)

        if (
            cls._STATE_OF_THE_ART_ASSERTION.search(result)
            and not cls._STATE_OF_THE_ART_ASSERTION.search(evidence)
        ):
            unsupported.extend(
                match.group(0) for match in cls._STATE_OF_THE_ART.finditer(result)
            )
            result = cls._STATE_OF_THE_ART.sub("", result)
        result = re.sub(r"\s{2,}", " ", result)
        result = re.sub(r"的\s+的", "的", result)
        return result, unsupported

    @classmethod
    def _base_fact_supported_after_qualifier_removal(
        cls,
        claim: str,
        evidence: str,
    ) -> bool:
        claim_folded = claim.casefold()
        evidence_folded = evidence.casefold()
        relation_groups = [
            group for group in cls._BASE_RELATION_GROUPS if cls._has_any(claim_folded, group)
        ]
        if relation_groups and not any(
            cls._has_any(evidence_folded, group) for group in relation_groups
        ):
            return False
        return cls._core_overlap(claim, evidence)

    @classmethod
    def _knowledge_framework_scope_supported(cls, claim: str, evidence: str) -> bool:
        if not cls._KNOWLEDGE_FRAMEWORK_CONSTRUCTION.search(claim):
            return True
        if not cls._KNOWLEDGE_FRAMEWORK.search(evidence):
            return False
        return all(
            not qualifier.search(claim) or qualifier.search(evidence)
            for qualifier in cls._FRAMEWORK_SCOPE_QUALIFIERS
        )

    @classmethod
    def _ordinary_review_candidate(cls, claim: str, evidence: str) -> str | None:
        """Retain only low-risk unresolved prose as an explicitly untrusted candidate."""

        value = claim.strip()
        lowered = value.casefold()
        if not value or not evidence.strip() or cls._NUMBER.search(value):
            return None
        if cls._has_any(
            lowered,
            cls._CAUSAL
            + cls._NECESSITY
            + cls._UNIVERSAL
            + cls._COMPARATIVE
            + cls._AUTHOR_ATTRIBUTION,
        ):
            return None
        if cls._STATE_OF_THE_ART_ASSERTION.search(value):
            return None
        if cls._KNOWLEDGE_FRAMEWORK_CONSTRUCTION.search(value):
            return None
        if any(
            pattern.search(value)
            for group in cls._HIGH_RISK_QUALIFIER_GROUPS
            for pattern in group
        ):
            return None
        return value

    @staticmethod
    def _has_any(value: str, markers: Iterable[str]) -> bool:
        return any(marker in value for marker in markers)

    @staticmethod
    def _ends_with_sentence(value: str) -> bool:
        return value.rstrip().endswith(("。", ".", "！", "!", "？", "?"))

    @classmethod
    def _contains_chinese(cls, value: str) -> bool:
        return cls._CHINESE.search(value) is not None

    @classmethod
    def _is_meaningful_semantic_reduction(cls, value: str) -> bool:
        compact = value.strip(" \t\n，,；;。.!！?？:：").casefold()
        if not compact:
            return False
        return not any(compact.endswith(ending) for ending in cls._DANGLING_REDUCTION_ENDINGS)


def render_reliability_markdown(result: CoreReliabilityResult) -> str:
    lines = ["# 核心 Claim–Evidence 可靠性", ""]
    if not result.records:
        return "\n".join([*lines, "暂无需要处理的核心结论。", ""])
    for record in result.records:
        source = record.source.value
        limitation = f" / {record.limitation_source.value}" if record.limitation_source else ""
        lines.append(f"- `{record.status.value}` / `{source}{limitation}` / `{record.item_id}`")
        if record.final_content:
            lines.append(f"  - 保留：{record.final_content}")
        elif record.review_candidate_content:
            lines.append(
                "  - 待核验候选（未进入可靠核心结论）："
                + record.review_candidate_content
            )
        else:
            lines.append("  - 处理：未作为可靠核心结论输出。")
        if record.unsupported_fragments:
            lines.append("  - 移除：" + "；".join(record.unsupported_fragments))
    return "\n".join(lines).rstrip() + "\n"
