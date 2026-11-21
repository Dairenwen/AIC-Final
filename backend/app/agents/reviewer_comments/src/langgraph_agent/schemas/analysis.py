"""SuggestionAnalysisGraph 纯逻辑节点的输入与输出契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import ApiSchema, JsonObject


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class IssueType(StrEnum):
    """第 3.2 节定义的十个主类型及无法可靠分类的兜底值。"""

    RESEARCH_POSITIONING_CONTRIBUTION = "RESEARCH_POSITIONING_CONTRIBUTION"
    RELATED_WORK_CITATION = "RELATED_WORK_CITATION"
    METHOD_THEORY = "METHOD_THEORY"
    DATA_SAMPLE = "DATA_SAMPLE"
    EXPERIMENT_EVALUATION = "EXPERIMENT_EVALUATION"
    RESULTS_DISCUSSION_CONCLUSION = "RESULTS_DISCUSSION_CONCLUSION"
    REPRODUCIBILITY_TRANSPARENCY = "REPRODUCIBILITY_TRANSPARENCY"
    WRITING_CONTENT_PRESENTATION = "WRITING_CONTENT_PRESENTATION"
    FORMAT_SUBMISSION_COMPLIANCE = "FORMAT_SUBMISSION_COMPLIANCE"
    ETHICS_RESEARCH_INTEGRITY = "ETHICS_RESEARCH_INTEGRITY"
    UNCLASSIFIED = "UNCLASSIFIED"


class IssueSubtype(StrEnum):
    """第 3.2 节第一版子类型的稳定编码。"""

    RESEARCH_MOTIVATION = "RESEARCH_MOTIVATION"
    RESEARCH_QUESTION = "RESEARCH_QUESTION"
    NOVELTY = "NOVELTY"
    RESEARCH_SIGNIFICANCE = "RESEARCH_SIGNIFICANCE"
    CONTRIBUTION_CLAIM = "CONTRIBUTION_CLAIM"
    RESEARCH_SCOPE_BOUNDARY = "RESEARCH_SCOPE_BOUNDARY"

    LITERATURE_COVERAGE = "LITERATURE_COVERAGE"
    RECENT_RESEARCH = "RECENT_RESEARCH"
    DIFFERENCE_FROM_PRIOR_METHODS = "DIFFERENCE_FROM_PRIOR_METHODS"
    CITATION_ACCURACY = "CITATION_ACCURACY"
    CITATION_SUPPORT = "CITATION_SUPPORT"

    METHOD_CLARITY = "METHOD_CLARITY"
    METHOD_CORRECTNESS = "METHOD_CORRECTNESS"
    DESIGN_RATIONALE = "DESIGN_RATIONALE"
    ASSUMPTION_REASONABLENESS = "ASSUMPTION_REASONABLENESS"
    THEORETICAL_ANALYSIS = "THEORETICAL_ANALYSIS"
    PROOF_CORRECTNESS = "PROOF_CORRECTNESS"
    ALGORITHM_DESCRIPTION = "ALGORITHM_DESCRIPTION"
    FORMULA_VARIABLE_DEFINITION = "FORMULA_VARIABLE_DEFINITION"

    DATA_SOURCE = "DATA_SOURCE"
    SAMPLE_SIZE = "SAMPLE_SIZE"
    SAMPLE_REPRESENTATIVENESS = "SAMPLE_REPRESENTATIVENESS"
    DATA_SPLIT = "DATA_SPLIT"
    DATA_LEAKAGE = "DATA_LEAKAGE"
    DATA_PREPROCESSING = "DATA_PREPROCESSING"
    LABEL_QUALITY = "LABEL_QUALITY"
    DATA_BIAS = "DATA_BIAS"
    MISSING_DATA_HANDLING = "MISSING_DATA_HANDLING"

    BASELINE_COMPARISON = "BASELINE_COMPARISON"
    ABLATION_STUDY = "ABLATION_STUDY"
    EVALUATION_METRIC = "EVALUATION_METRIC"
    STATISTICAL_TESTING = "STATISTICAL_TESTING"
    ROBUSTNESS = "ROBUSTNESS"
    SENSITIVITY_ANALYSIS = "SENSITIVITY_ANALYSIS"
    CONTROL_EXPERIMENT = "CONTROL_EXPERIMENT"
    EXPERIMENTAL_FAIRNESS = "EXPERIMENTAL_FAIRNESS"
    GENERALIZATION_EXPERIMENT = "GENERALIZATION_EXPERIMENT"
    EFFICIENCY_EVALUATION = "EFFICIENCY_EVALUATION"

    RESULT_ANALYSIS = "RESULT_ANALYSIS"
    RESULT_CONSISTENCY = "RESULT_CONSISTENCY"
    CONCLUSION_SUPPORT = "CONCLUSION_SUPPORT"
    OVERSTATED_CONCLUSION = "OVERSTATED_CONCLUSION"
    GENERALIZATION_CLAIM = "GENERALIZATION_CLAIM"
    FAILURE_CASE = "FAILURE_CASE"
    LIMITATION = "LIMITATION"
    CAUSAL_INTERPRETATION = "CAUSAL_INTERPRETATION"
    ANOMALOUS_RESULT_EXPLANATION = "ANOMALOUS_RESULT_EXPLANATION"

    IMPLEMENTATION_DETAILS = "IMPLEMENTATION_DETAILS"
    HYPERPARAMETERS = "HYPERPARAMETERS"
    TRAINING_CONFIGURATION = "TRAINING_CONFIGURATION"
    RANDOM_SEED = "RANDOM_SEED"
    HARDWARE_COMPUTE = "HARDWARE_COMPUTE"
    CODE_AVAILABILITY = "CODE_AVAILABILITY"
    DATA_AVAILABILITY = "DATA_AVAILABILITY"
    EXPERIMENT_PROTOCOL = "EXPERIMENT_PROTOCOL"
    SOFTWARE_VERSION_DEPENDENCY = "SOFTWARE_VERSION_DEPENDENCY"

    LANGUAGE_CLARITY = "LANGUAGE_CLARITY"
    LOGICAL_ORGANIZATION = "LOGICAL_ORGANIZATION"
    TERMINOLOGY_CONSISTENCY = "TERMINOLOGY_CONSISTENCY"
    CONTENT_REDUNDANCY = "CONTENT_REDUNDANCY"
    FIGURE_READABILITY = "FIGURE_READABILITY"
    FIGURE_CAPTION = "FIGURE_CAPTION"
    TABLE_READABILITY = "TABLE_READABILITY"
    TABLE_CAPTION = "TABLE_CAPTION"
    SYMBOL_CONSISTENCY = "SYMBOL_CONSISTENCY"
    SPELLING_GRAMMAR = "SPELLING_GRAMMAR"

    TEMPLATE_COMPLIANCE = "TEMPLATE_COMPLIANCE"
    LENGTH_LIMIT = "LENGTH_LIMIT"
    SECTION_NUMBERING = "SECTION_NUMBERING"
    FIGURE_NUMBERING = "FIGURE_NUMBERING"
    TABLE_NUMBERING = "TABLE_NUMBERING"
    REFERENCE_STYLE = "REFERENCE_STYLE"
    REQUIRED_STATEMENT = "REQUIRED_STATEMENT"
    SUPPLEMENTARY_MATERIAL_FORMAT = "SUPPLEMENTARY_MATERIAL_FORMAT"

    ETHICS_APPROVAL = "ETHICS_APPROVAL"
    INFORMED_CONSENT = "INFORMED_CONSENT"
    PRIVACY_PROTECTION = "PRIVACY_PROTECTION"
    DATA_USE_COMPLIANCE = "DATA_USE_COMPLIANCE"
    CONFLICT_OF_INTEREST = "CONFLICT_OF_INTEREST"
    PLAGIARISM_CONCERN = "PLAGIARISM_CONCERN"
    DATA_INTEGRITY = "DATA_INTEGRITY"
    IMAGE_MANIPULATION = "IMAGE_MANIPULATION"
    SAFETY_RISK = "SAFETY_RISK"
    DUAL_USE_RISK = "DUAL_USE_RISK"

    UNCLASSIFIED = "UNCLASSIFIED"


CATEGORY_SUBTYPES: dict[IssueType, frozenset[IssueSubtype]] = {
    IssueType.RESEARCH_POSITIONING_CONTRIBUTION: frozenset(
        {
            IssueSubtype.RESEARCH_MOTIVATION,
            IssueSubtype.RESEARCH_QUESTION,
            IssueSubtype.NOVELTY,
            IssueSubtype.RESEARCH_SIGNIFICANCE,
            IssueSubtype.CONTRIBUTION_CLAIM,
            IssueSubtype.RESEARCH_SCOPE_BOUNDARY,
        }
    ),
    IssueType.RELATED_WORK_CITATION: frozenset(
        {
            IssueSubtype.LITERATURE_COVERAGE,
            IssueSubtype.RECENT_RESEARCH,
            IssueSubtype.DIFFERENCE_FROM_PRIOR_METHODS,
            IssueSubtype.CITATION_ACCURACY,
            IssueSubtype.CITATION_SUPPORT,
        }
    ),
    IssueType.METHOD_THEORY: frozenset(
        {
            IssueSubtype.METHOD_CLARITY,
            IssueSubtype.METHOD_CORRECTNESS,
            IssueSubtype.DESIGN_RATIONALE,
            IssueSubtype.ASSUMPTION_REASONABLENESS,
            IssueSubtype.THEORETICAL_ANALYSIS,
            IssueSubtype.PROOF_CORRECTNESS,
            IssueSubtype.ALGORITHM_DESCRIPTION,
            IssueSubtype.FORMULA_VARIABLE_DEFINITION,
        }
    ),
    IssueType.DATA_SAMPLE: frozenset(
        {
            IssueSubtype.DATA_SOURCE,
            IssueSubtype.SAMPLE_SIZE,
            IssueSubtype.SAMPLE_REPRESENTATIVENESS,
            IssueSubtype.DATA_SPLIT,
            IssueSubtype.DATA_LEAKAGE,
            IssueSubtype.DATA_PREPROCESSING,
            IssueSubtype.LABEL_QUALITY,
            IssueSubtype.DATA_BIAS,
            IssueSubtype.MISSING_DATA_HANDLING,
        }
    ),
    IssueType.EXPERIMENT_EVALUATION: frozenset(
        {
            IssueSubtype.BASELINE_COMPARISON,
            IssueSubtype.ABLATION_STUDY,
            IssueSubtype.EVALUATION_METRIC,
            IssueSubtype.STATISTICAL_TESTING,
            IssueSubtype.ROBUSTNESS,
            IssueSubtype.SENSITIVITY_ANALYSIS,
            IssueSubtype.CONTROL_EXPERIMENT,
            IssueSubtype.EXPERIMENTAL_FAIRNESS,
            IssueSubtype.GENERALIZATION_EXPERIMENT,
            IssueSubtype.EFFICIENCY_EVALUATION,
        }
    ),
    IssueType.RESULTS_DISCUSSION_CONCLUSION: frozenset(
        {
            IssueSubtype.RESULT_ANALYSIS,
            IssueSubtype.RESULT_CONSISTENCY,
            IssueSubtype.CONCLUSION_SUPPORT,
            IssueSubtype.OVERSTATED_CONCLUSION,
            IssueSubtype.GENERALIZATION_CLAIM,
            IssueSubtype.FAILURE_CASE,
            IssueSubtype.LIMITATION,
            IssueSubtype.CAUSAL_INTERPRETATION,
            IssueSubtype.ANOMALOUS_RESULT_EXPLANATION,
        }
    ),
    IssueType.REPRODUCIBILITY_TRANSPARENCY: frozenset(
        {
            IssueSubtype.IMPLEMENTATION_DETAILS,
            IssueSubtype.HYPERPARAMETERS,
            IssueSubtype.TRAINING_CONFIGURATION,
            IssueSubtype.RANDOM_SEED,
            IssueSubtype.HARDWARE_COMPUTE,
            IssueSubtype.CODE_AVAILABILITY,
            IssueSubtype.DATA_AVAILABILITY,
            IssueSubtype.EXPERIMENT_PROTOCOL,
            IssueSubtype.SOFTWARE_VERSION_DEPENDENCY,
        }
    ),
    IssueType.WRITING_CONTENT_PRESENTATION: frozenset(
        {
            IssueSubtype.LANGUAGE_CLARITY,
            IssueSubtype.LOGICAL_ORGANIZATION,
            IssueSubtype.TERMINOLOGY_CONSISTENCY,
            IssueSubtype.CONTENT_REDUNDANCY,
            IssueSubtype.FIGURE_READABILITY,
            IssueSubtype.FIGURE_CAPTION,
            IssueSubtype.TABLE_READABILITY,
            IssueSubtype.TABLE_CAPTION,
            IssueSubtype.SYMBOL_CONSISTENCY,
            IssueSubtype.SPELLING_GRAMMAR,
        }
    ),
    IssueType.FORMAT_SUBMISSION_COMPLIANCE: frozenset(
        {
            IssueSubtype.TEMPLATE_COMPLIANCE,
            IssueSubtype.LENGTH_LIMIT,
            IssueSubtype.SECTION_NUMBERING,
            IssueSubtype.FIGURE_NUMBERING,
            IssueSubtype.TABLE_NUMBERING,
            IssueSubtype.REFERENCE_STYLE,
            IssueSubtype.REQUIRED_STATEMENT,
            IssueSubtype.SUPPLEMENTARY_MATERIAL_FORMAT,
        }
    ),
    IssueType.ETHICS_RESEARCH_INTEGRITY: frozenset(
        {
            IssueSubtype.ETHICS_APPROVAL,
            IssueSubtype.INFORMED_CONSENT,
            IssueSubtype.PRIVACY_PROTECTION,
            IssueSubtype.DATA_USE_COMPLIANCE,
            IssueSubtype.CONFLICT_OF_INTEREST,
            IssueSubtype.PLAGIARISM_CONCERN,
            IssueSubtype.DATA_INTEGRITY,
            IssueSubtype.IMAGE_MANIPULATION,
            IssueSubtype.SAFETY_RISK,
            IssueSubtype.DUAL_USE_RISK,
        }
    ),
    IssueType.UNCLASSIFIED: frozenset({IssueSubtype.UNCLASSIFIED}),
}


class IssueNature(StrEnum):
    """第 3.3 节的问题性质。"""

    MISSING = "MISSING"
    INSUFFICIENT = "INSUFFICIENT"
    UNCLEAR = "UNCLEAR"
    UNSUPPORTED = "UNSUPPORTED"
    POSSIBLY_INCORRECT = "POSSIBLY_INCORRECT"
    INCONSISTENT = "INCONSISTENT"
    OUTDATED = "OUTDATED"
    NONCOMPLIANT = "NONCOMPLIANT"


class SuggestionAnalysisInput(ApiSchema):
    """一条已确认建议及可选的来源诉求。"""

    suggestion_text: NonBlankStr
    source_requests: list[NonBlankStr] = Field(default_factory=list)
    suggestion_id: NonBlankStr = "S-01"
    review_point_id: NonBlankStr = "P-01"


def _fill_missing_list_fields(data: object, list_fields: tuple[str, ...]) -> object:
    """模型常省略空列表；校验前补 []。"""
    if not isinstance(data, dict):
        return data
    patched = dict(data)
    for key in list_fields:
        if key not in patched or patched[key] is None:
            patched[key] = []
    return patched


def _coerce_str_to_list(value: object) -> object:
    """单个字符串 → [str]；None → []。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return value


class ClassificationDecision(ApiSchema):
    """可分别保存自动结果和用户确认结果的分类内容。"""

    primary_type: IssueType
    target_subtype: IssueSubtype
    secondary_types: list[IssueType] = Field(default_factory=list)
    issue_natures: list[IssueNature] = Field(min_length=1)
    explicit_action: str | None = None
    inferred_action: str | None = None
    implicit_concern: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: object) -> object:
        return _fill_missing_list_fields(data, ("secondary_types",))

    @field_validator(
        "explicit_action", "inferred_action", "implicit_concern", mode="before"
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def validate_type_relationships(self) -> "ClassificationDecision":
        if self.target_subtype not in CATEGORY_SUBTYPES[self.primary_type]:
            raise ValueError("target_subtype 不属于 primary_type")
        if self.primary_type in self.secondary_types:
            raise ValueError("secondary_types 不得重复 primary_type")
        if IssueType.UNCLASSIFIED in self.secondary_types:
            raise ValueError("UNCLASSIFIED 不得作为次要类型")
        if len(set(self.secondary_types)) != len(self.secondary_types):
            raise ValueError("secondary_types 不得重复")
        return self


class LlmClassificationResult(ClassificationDecision):
    """结构化 LLM 直接返回的分类字段。"""

    # 次要字段：降级到 json_object 时允许缺失。
    classification_confidence: float = Field(
        default=0.75,
        ge=0,
        le=1,
        description="分类置信度，范围 0 到 1；缺省 0.75",
    )
    classification_reason: NonBlankStr
    candidate_types: list[IssueType] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_lists(cls, data: object) -> object:
        data = _fill_missing_list_fields(
            data, ("secondary_types", "candidate_types")
        )
        # 兼容模型把 reason 写到其它键名
        if isinstance(data, dict):
            patched = dict(data)
            if not patched.get("classification_reason"):
                for key in ("reason", "rationale", "explanation", "summary"):
                    value = patched.get(key)
                    if isinstance(value, str) and value.strip():
                        patched["classification_reason"] = value.strip()
                        break
            return patched
        return data

    @field_validator("classification_confidence", mode="before")
    @classmethod
    def default_classification_confidence(cls, value: object) -> object:
        if value is None or value == "":
            return 0.75
        return value

    @field_validator("candidate_types")
    @classmethod
    def validate_candidate_types(cls, value: list[IssueType]) -> list[IssueType]:
        if len(set(value)) != len(value):
            raise ValueError("candidate_types 不得重复")
        return value


class ClassificationResult(LlmClassificationResult):
    """第 3.3 节多维分类结果。"""

    automatic_result: ClassificationDecision
    confirmed_result: ClassificationDecision | None = None

    @model_validator(mode="after")
    def validate_automatic_result(self) -> "ClassificationResult":
        field_names = ClassificationDecision.model_fields
        current = {name: getattr(self, name) for name in field_names}
        if self.automatic_result != ClassificationDecision.model_validate(current):
            raise ValueError("automatic_result 必须保存当前自动分类结果")
        return self


class PaperExcerpt(ApiSchema):
    """调用方提供的候选论文原文；节点不自行检索。"""

    section: NonBlankStr | None = None
    location: NonBlankStr | None = None
    quote: NonBlankStr
    surrounding_context: NonBlankStr | None = None


class EvidenceRelation(StrEnum):
    """第 20.4 节和 AnalysisSnapshot 约定的证据关系。"""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MISSING = "missing"
    USER_STATED = "user_stated"


class EvidenceJudgement(StrEnum):
    """第 4.5 节描述的证据判断。"""

    SUPPORTS_REVIEWER_CONCERN = "SUPPORTS_REVIEWER_CONCERN"
    REFUTES_REVIEWER_CONCERN = "REFUTES_REVIEWER_CONCERN"
    PARTIALLY_ADDRESSES_CONCERN = "PARTIALLY_ADDRESSES_CONCERN"
    USER_STATEMENT = "USER_STATEMENT"


class EvidenceConfirmationStatus(StrEnum):
    """本轮不接 interrupt，因此证据仅标记为待确认。"""

    UNCONFIRMED = "UNCONFIRMED"


class Coverage(StrEnum):
    """AnalysisSnapshot 使用的覆盖状态。"""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class LlmEvidenceCandidate(ApiSchema):
    """LLM 只引用候选片段序号，不允许复述或改写论文内容。"""

    source_index: int = Field(ge=1)
    relevance: float = Field(ge=0, le=1)
    evidence_judgement: EvidenceJudgement
    relation: EvidenceRelation

    @model_validator(mode="after")
    def reject_user_fact_values(self) -> "LlmEvidenceCandidate":
        if self.evidence_judgement is EvidenceJudgement.USER_STATEMENT:
            raise ValueError("论文证据不得标为 USER_STATEMENT")
        if self.relation is EvidenceRelation.USER_STATED:
            raise ValueError("论文证据不得标为 user_stated")
        return self


class LlmEvidenceAssessment(ApiSchema):
    """候选论文片段的结构化判断。"""

    evidence_items: list[LlmEvidenceCandidate]
    coverage: Coverage

    @model_validator(mode="before")
    @classmethod
    def _wrap_bare_list(cls, data: object) -> object:
        """兜底：模型有时直接返回证据数组而漏掉外层对象与 coverage。

        遇到裸数组时自动包成 {"evidence_items": [...], "coverage": "UNKNOWN"}，
        避免因个别模型不遵守对象结构而使整条证据分析链失败；coverage 缺省用
        UNKNOWN，语义上等价于“候选片段不足以判断”，不会虚构覆盖结论。
        """
        if isinstance(data, list):
            return {"evidence_items": data, "coverage": Coverage.UNKNOWN.value}
        if isinstance(data, dict) and "evidence_items" not in data:
            # 少数情况下模型把数组塞进其它键名（如 items/results）。
            for key in ("items", "results", "evidences", "evidence"):
                value = data.get(key)
                if isinstance(value, list):
                    merged = {
                        k: v
                        for k, v in data.items()
                        if k not in {"items", "results", "evidences", "evidence"}
                    }
                    merged["evidence_items"] = value
                    merged.setdefault("coverage", Coverage.UNKNOWN.value)
                    return merged
        return data


class EvidenceItem(ApiSchema):
    """第 4.5 节证据卡片，并带第 20.4 节统一关系字段。"""

    evidence_id: NonBlankStr
    review_point_id: NonBlankStr
    section: str | None
    location: str | None
    quote: NonBlankStr
    surrounding_context: str | None
    relevance: float = Field(ge=0, le=1)
    evidence_judgement: EvidenceJudgement
    confirmation_status: EvidenceConfirmationStatus
    relation: EvidenceRelation


class EvidenceAssessment(ApiSchema):
    """证据卡片及建议级覆盖状态。"""

    evidence_items: list[EvidenceItem]
    coverage: Coverage


class AcademicImpact(StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    EDITORIAL = "EDITORIAL"


class HandlingRequirement(StrEnum):
    MUST_ADDRESS = "MUST_ADDRESS"
    SHOULD_ADDRESS = "SHOULD_ADDRESS"
    CLARIFY_OR_RESPOND = "CLARIFY_OR_RESPOND"
    OPTIONAL = "OPTIONAL"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


class RevisionEffort(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class Feasibility(StrEnum):
    FEASIBLE = "FEASIBLE"
    CONSTRAINED = "CONSTRAINED"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


class WorkPriority(StrEnum):
    """持久化契约中的 P0/P1/P2/P3，对应 Immediate/High/Normal/Low。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ScheduleFlag(StrEnum):
    LONG_LEAD = "LONG_LEAD"
    QUICK_WIN = "QUICK_WIN"
    BATCH_EDIT = "BATCH_EDIT"
    STRATEGY_REQUIRED = "STRATEGY_REQUIRED"


class LlmPriorityAssessment(ApiSchema):
    """LLM 判断的优先级维度，最终优先级由程序校正规则保证。"""

    academic_impact: AcademicImpact
    handling_requirement: HandlingRequirement
    revision_effort: RevisionEffort
    feasibility: Feasibility
    work_priority: WorkPriority
    schedule_flag: ScheduleFlag | None = None
    risk_signals: list[NonBlankStr] = Field(default_factory=list)
    # 次要字段：降级到 json_object 时允许缺失。
    assessment_confidence: float = Field(
        default=0.75,
        ge=0,
        le=1,
        description="优先级评估置信度，范围 0 到 1；缺省 0.75",
    )
    assessment_reason: NonBlankStr

    @model_validator(mode="before")
    @classmethod
    def _default_lists_and_aliases(cls, data: object) -> object:
        data = _fill_missing_list_fields(data, ("risk_signals",))
        if not isinstance(data, dict):
            return data
        patched = dict(data)
        if not patched.get("assessment_reason"):
            for key in ("reason", "rationale", "explanation", "summary"):
                value = patched.get(key)
                if isinstance(value, str) and value.strip():
                    patched["assessment_reason"] = value.strip()
                    break
        return patched

    @field_validator("assessment_confidence", mode="before")
    @classmethod
    def default_assessment_confidence(cls, value: object) -> object:
        if value is None or value == "":
            return 0.75
        return value


class PriorityDecision(ApiSchema):
    """可分别保存自动结果和用户确认结果的优先级内容。"""

    academic_impact: AcademicImpact
    handling_requirement: HandlingRequirement
    revision_effort: RevisionEffort
    feasibility: Feasibility
    work_priority: WorkPriority
    schedule_flag: ScheduleFlag | None = None
    risk_signals: list[NonBlankStr] = Field(default_factory=list)
    repeated_reviewer_count: int = Field(ge=1)


class PriorityAssessment(PriorityDecision):
    """第 5.7 节结构化优先级结果。"""

    assessment_confidence: float = Field(ge=0, le=1)
    assessment_reason: NonBlankStr
    automatic_result: PriorityDecision
    confirmed_result: PriorityDecision | None = None

    @model_validator(mode="after")
    def validate_automatic_result(self) -> "PriorityAssessment":
        field_names = PriorityDecision.model_fields
        current = {name: getattr(self, name) for name in field_names}
        if self.automatic_result != PriorityDecision.model_validate(current):
            raise ValueError("automatic_result 必须保存当前自动优先级结果")
        return self


class ActionType(StrEnum):
    CONTENT_PRESENTATION = "CONTENT_PRESENTATION"
    LITERATURE_CITATION = "LITERATURE_CITATION"
    EXPERIMENT_EVALUATION = "EXPERIMENT_EVALUATION"
    METHOD_DATA_ANALYSIS = "METHOD_DATA_ANALYSIS"
    CLAIM_SCOPE = "CLAIM_SCOPE"
    TRANSPARENCY_COMPLIANCE = "TRANSPARENCY_COMPLIANCE"


class ActionNecessity(StrEnum):
    CORE = "CORE"
    SUPPORTING = "SUPPORTING"
    OPTIONAL = "OPTIONAL"


class ResolutionLevel(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    COMMUNICATION_ONLY = "COMMUNICATION_ONLY"


class LlmActionCandidate(ApiSchema):
    """LLM 生成的动作内容，标识符由程序统一生成。"""

    action_type: ActionType
    title: NonBlankStr
    description: NonBlankStr
    addressed_concern: NonBlankStr
    necessity: ActionNecessity
    recommendation_basis: list[NonBlankStr] = Field(min_length=1)
    required_facts: list[NonBlankStr] = Field(default_factory=list)
    prerequisites: list[NonBlankStr] = Field(default_factory=list)
    expected_outputs: list[NonBlankStr] = Field(min_length=1)
    estimated_cost: RevisionEffort
    feasibility: Feasibility
    expected_resolution_level: ResolutionLevel
    alternative_to_index: int | None = Field(default=None, ge=1)
    alternative_actions: list[NonBlankStr] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases_and_lists(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        patched = dict(data)
        # 字段别名（模型常写单数/近义名）
        if not patched.get("expected_outputs"):
            for key in ("expected_output", "outputs", "deliverables", "expected_results"):
                value = patched.get(key)
                if value is None:
                    continue
                patched["expected_outputs"] = _coerce_str_to_list(value)
                break
        if not patched.get("recommendation_basis"):
            for key in ("basis", "rationale", "reasons", "justification"):
                value = patched.get(key)
                if value is None:
                    continue
                patched["recommendation_basis"] = _coerce_str_to_list(value)
                break
        if not patched.get("addressed_concern"):
            for key in ("concern", "target_concern", "reviewer_concern"):
                value = patched.get(key)
                if isinstance(value, str) and value.strip():
                    patched["addressed_concern"] = value.strip()
                    break
        if not patched.get("description"):
            for key in ("detail", "details", "summary", "action_description"):
                value = patched.get(key)
                if isinstance(value, str) and value.strip():
                    patched["description"] = value.strip()
                    break
        if not patched.get("title"):
            for key in ("name", "action_title", "label"):
                value = patched.get(key)
                if isinstance(value, str) and value.strip():
                    patched["title"] = value.strip()
                    break
        # 列表字段：字符串 → 列表；缺省 → []
        for key in (
            "recommendation_basis",
            "required_facts",
            "prerequisites",
            "expected_outputs",
            "alternative_actions",
        ):
            if key not in patched or patched[key] is None:
                if key in {"recommendation_basis", "expected_outputs"}:
                    continue  # 仍由 min_length 约束，不擅自造空
                patched[key] = []
            else:
                patched[key] = _coerce_str_to_list(patched[key])
        # 丢掉模型常多写的非契约字段（ApiSchema extra=forbid）
        allowed = set(cls.model_fields)
        return {k: v for k, v in patched.items() if k in allowed}


class LlmActionRecommendations(ApiSchema):
    recommendations: list[LlmActionCandidate] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _wrap_bare_list(cls, data: object) -> object:
        if isinstance(data, list):
            return {"recommendations": data}
        if isinstance(data, dict) and "recommendations" not in data:
            for key in ("actions", "items", "results", "candidates"):
                value = data.get(key)
                if isinstance(value, list):
                    merged = {k: v for k, v in data.items() if k != key}
                    merged["recommendations"] = value
                    return merged
        return data


class ActionRecommendation(ApiSchema):
    """第 10.7 节候选动作结构。"""

    recommendation_id: NonBlankStr
    suggestion_id: NonBlankStr
    action_type: ActionType
    title: NonBlankStr
    description: NonBlankStr
    addressed_concern: NonBlankStr
    necessity: ActionNecessity
    recommendation_basis: list[NonBlankStr]
    required_facts: list[NonBlankStr]
    prerequisites: list[NonBlankStr]
    expected_outputs: list[NonBlankStr]
    estimated_cost: RevisionEffort
    feasibility: Feasibility
    expected_resolution_level: ResolutionLevel
    alternative_to: NonBlankStr | None
    alternative_actions: list[NonBlankStr]
    model_metadata: JsonObject = Field(default_factory=dict)


class ActionRecommendations(ApiSchema):
    """一条建议对应的一组候选修改动作。"""

    recommendations: list[ActionRecommendation] = Field(min_length=1)
