from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from math import isclose
from typing import Iterable

from llm.experiments import ExperimentAnalysis
from llm.gateway import ReadingAnalysis
from llm.scientific_elements import ScientificElement, ScientificElementAnalysis, TableNumericCheck
from schemas.models import ReadingResult


class NumericRelation(StrEnum):
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"
    EQUAL = "EQUAL"
    APPROX_EQUAL = "APPROX_EQUAL"
    DIFFERENCE = "DIFFERENCE"
    PERCENT_CHANGE = "PERCENT_CHANGE"
    BETTER = "BETTER"
    WORSE = "WORSE"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NumericComparison:
    target_label: str
    target_value: float
    baseline_label: str
    baseline_value: float
    metric: str = "metric"
    direction: MetricDirection = MetricDirection.UNKNOWN


class NumericRelationGuard:
    """Deterministic checks for bounded target/baseline numeric statements."""

    _ABS_TOLERANCE = 1e-9
    _REL_TOLERANCE = 1e-9
    _APPROX_ABS_TOLERANCE = 1e-3
    _APPROX_REL_TOLERANCE = 1e-4
    _NUMBER = r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?%?"

    _GREATER_MARKERS = (
        "高于", "超过", "超越", "大于", "不低于", "higher", "above", "exceed", "greater", ">",
    )
    _LESS_MARKERS = (
        "低于", "略低", "小于", "不及", "lower", "below", "less than", "underperform", "<",
    )
    _APPROX_EQUAL_MARKERS = (
        "近似相等", "约等于", "大致相同", "approximately equal", "roughly equal", "≈",
    )
    _EQUAL_MARKERS = (
        "持平", "相同", "相等", "无差异", "媲美", "equal", "same", "on par", "tie",
        *_APPROX_EQUAL_MARKERS,
    )
    _BETTER_MARKERS = ("优于", "更优", "较好", "better", "outperform")
    _WORSE_MARKERS = ("更差", "较差", "劣于", "worse", "underperform")
    _COMPARISON_MARKERS = tuple(
        dict.fromkeys(
            _GREATER_MARKERS
            + _LESS_MARKERS
            + _EQUAL_MARKERS
            + _BETTER_MARKERS
            + _WORSE_MARKERS
            + ("提升", "下降", "差值", "difference", "increase", "decrease", "gain")
        )
    )

    def ordering(self, comparison: NumericComparison) -> NumericRelation:
        if self.values_equal(comparison.target_value, comparison.baseline_value):
            return NumericRelation.EQUAL
        if comparison.target_value > comparison.baseline_value:
            return NumericRelation.GREATER_THAN
        return NumericRelation.LESS_THAN

    def approximate_ordering(self, comparison: NumericComparison) -> NumericRelation:
        if isclose(
            comparison.target_value,
            comparison.baseline_value,
            rel_tol=self._APPROX_REL_TOLERANCE,
            abs_tol=self._APPROX_ABS_TOLERANCE,
        ):
            return (
                NumericRelation.EQUAL
                if self.values_equal(comparison.target_value, comparison.baseline_value)
                else NumericRelation.APPROX_EQUAL
            )
        return self.ordering(comparison)

    def quality_relation(self, comparison: NumericComparison) -> NumericRelation | None:
        ordering = self.ordering(comparison)
        if comparison.direction == MetricDirection.UNKNOWN or ordering == NumericRelation.EQUAL:
            return None
        better = (
            ordering == NumericRelation.GREATER_THAN
            if comparison.direction == MetricDirection.HIGHER_IS_BETTER
            else ordering == NumericRelation.LESS_THAN
        )
        return NumericRelation.BETTER if better else NumericRelation.WORSE

    @classmethod
    def values_equal(cls, first: float, second: float) -> bool:
        return isclose(
            first,
            second,
            rel_tol=cls._REL_TOLERANCE,
            abs_tol=cls._ABS_TOLERANCE,
        )

    @staticmethod
    def difference(comparison: NumericComparison) -> float:
        return comparison.target_value - comparison.baseline_value

    @classmethod
    def percent_change(cls, comparison: NumericComparison) -> float | None:
        if cls.values_equal(comparison.baseline_value, 0.0):
            return None
        return cls.difference(comparison) / abs(comparison.baseline_value) * 100

    @classmethod
    def difference_is_consistent(
        cls, comparison: NumericComparison, asserted_difference: float
    ) -> bool:
        return isclose(
            asserted_difference,
            cls.difference(comparison),
            rel_tol=1e-4,
            abs_tol=1e-4,
        )

    @classmethod
    def percent_change_is_consistent(
        cls, comparison: NumericComparison, asserted_percent: float
    ) -> bool:
        expected = cls.percent_change(comparison)
        return expected is not None and isclose(
            asserted_percent,
            expected,
            rel_tol=1e-3,
            abs_tol=5e-2,
        )

    def statement_is_consistent(
        self, statement: str, comparison: NumericComparison
    ) -> bool:
        lowered = statement.casefold()
        ordering = self.ordering(comparison)
        approximate = self.approximate_ordering(comparison)
        has_approximate = self._contains_any(lowered, self._APPROX_EQUAL_MARKERS)
        if ordering == NumericRelation.GREATER_THAN and self._contains_any(
            lowered, self._LESS_MARKERS + self._EQUAL_MARKERS
        ) and not (has_approximate and approximate == NumericRelation.APPROX_EQUAL):
            return False
        if ordering == NumericRelation.LESS_THAN and self._contains_any(
            lowered, self._GREATER_MARKERS + self._EQUAL_MARKERS
        ) and not (has_approximate and approximate == NumericRelation.APPROX_EQUAL):
            return False
        if ordering == NumericRelation.EQUAL and self._contains_any(
            lowered,
            self._GREATER_MARKERS
            + self._LESS_MARKERS
            + self._BETTER_MARKERS
            + self._WORSE_MARKERS
            + ("提升", "下降", "increase", "decrease", "gain"),
        ):
            return False

        quality = self.quality_relation(comparison)
        has_better = self._contains_any(lowered, self._BETTER_MARKERS)
        has_worse = self._contains_any(lowered, self._WORSE_MARKERS)
        if quality is None and (has_better or has_worse):
            return False
        if quality == NumericRelation.BETTER and has_worse:
            return False
        if quality == NumericRelation.WORSE and has_better:
            return False
        return True

    def sanitize_text(
        self,
        statement: str,
        comparisons: Iterable[NumericComparison] = (),
        *,
        unsafe_unbound: str = "preserve",
        infer_unbound: bool = True,
    ) -> str | None:
        matched = [
            comparison
            for comparison in comparisons
            if self._mentions_values(statement, comparison)
        ]
        if matched:
            if all(self.statement_is_consistent(statement, item) for item in matched):
                return statement
            return "；".join(self.neutral_statement(item) for item in matched) + "。"

        if infer_unbound:
            inferred = self._infer_two_value_comparison(statement)
            if inferred is not None:
                if self.statement_is_consistent(statement, inferred):
                    return statement
                return self.neutral_statement(inferred) + "。"

        if unsafe_unbound != "preserve" and self.is_numeric_comparison(statement):
            if unsafe_unbound == "drop":
                return None
            return "该处包含多组数值；未保留缺少结构化 target/baseline 绑定的比较判断。"
        return statement

    def sanitize_reading_analysis(
        self,
        analysis: ReadingAnalysis,
        comparisons: Iterable[NumericComparison] = (),
    ) -> ReadingAnalysis:
        facts = tuple(comparisons)
        claims = []
        for claim in analysis.claims:
            content = self.sanitize_text(claim.content, facts, unsafe_unbound="neutralize")
            claims.append(claim.model_copy(update={"content": content}))
        return analysis.model_copy(update={"claims": claims})

    def sanitize_reading_result(
        self,
        result: ReadingResult,
        comparisons: Iterable[NumericComparison] = (),
    ) -> ReadingResult:
        facts = tuple(comparisons)
        claims = []
        for claim in result.claims:
            content = self.sanitize_text(claim.content, facts, unsafe_unbound="neutralize")
            claims.append(claim.model_copy(update={"content": content}))
        return result.model_copy(update={"claims": claims})

    def sanitize_experiment_analysis(
        self,
        analysis: ExperimentAnalysis,
        comparisons: Iterable[NumericComparison] = (),
    ) -> ExperimentAnalysis:
        facts = tuple(comparisons)
        findings = []
        for finding in analysis.findings:
            content = self.sanitize_text(finding.content, facts, unsafe_unbound="drop")
            if content is not None:
                findings.append(finding.model_copy(update={"content": content}))
        assessments = []
        for assessment in analysis.conclusion_assessments:
            conclusion = self.sanitize_text(
                assessment.conclusion, facts, unsafe_unbound="neutralize"
            )
            reason = self.sanitize_text(assessment.reason, facts, unsafe_unbound="neutralize")
            assessments.append(
                assessment.model_copy(update={"conclusion": conclusion, "reason": reason})
            )
        return analysis.model_copy(
            update={"findings": findings, "conclusion_assessments": assessments}
        )

    def sanitize_scientific_analysis(
        self, analysis: ScientificElementAnalysis
    ) -> ScientificElementAnalysis:
        return analysis.model_copy(
            update={
                "elements": [self.sanitize_scientific_element(item) for item in analysis.elements]
            }
        )

    def sanitize_scientific_element(self, element: ScientificElement) -> ScientificElement:
        if element.element_type != "TABLE":
            return element
        accepted_checks = [
            check
            for check in element.table_checks
            if self.table_check_semantics_are_consistent(check)
        ]
        facts = tuple(self.from_table_check(check) for check in accepted_checks)
        configuration_table = self.is_configuration_table(element)
        removed_all_checks = bool(element.table_checks) and not accepted_checks
        stale_check_summary = any(
            marker in element.explanation.casefold()
            for marker in (
                "数值关系",
                "accepted cell",
                "accepted structured",
                "structured cell",
            )
        )
        explanation = (
            (
                "该表的数值关系仅依据已接受的结构化单元格核验生成。"
                if facts
                else (
                    "该表保留了独立单元格核验的配置数值；"
                    "未生成缺少对照证据的比较判断。"
                )
                if element.table_cell_facts
                else "该表未保留缺少已接受单元格核验的数值说明。"
            )
            if (
                (removed_all_checks and stale_check_summary)
                or self.is_table_numeric_statement(element.explanation)
                or (
                    self.has_table_numeric_value(element.explanation)
                    and not configuration_table
                )
            )
            else element.explanation
        )
        findings = []
        for finding in element.findings:
            if self.is_table_numeric_statement(finding) or (
                self.has_table_numeric_value(finding) and not configuration_table
            ):
                continue
            value = self.sanitize_text(
                finding,
                facts,
                unsafe_unbound="drop",
                infer_unbound=False,
            )
            if value is not None:
                findings.append(value)
        generated = [self.table_check_statement(check) for check in accepted_checks]
        return element.model_copy(
            update={
                "explanation": explanation,
                "findings": [*generated, *findings],
                "table_checks": accepted_checks,
            }
        )

    @classmethod
    def table_check_semantics_are_consistent(cls, check: TableNumericCheck) -> bool:
        """Reject a semantic role that contradicts the check's own values/direction."""

        if check.check_type != "BEST_VALUE":
            return True
        if check.direction == "NEUTRAL":
            return False
        if cls.values_equal(check.target_value, check.baseline_value):
            return True
        if check.direction == "HIGHER_IS_BETTER":
            return check.target_value > check.baseline_value
        return check.target_value < check.baseline_value

    @staticmethod
    def from_table_check(check: TableNumericCheck) -> NumericComparison:
        direction = {
            "HIGHER_IS_BETTER": MetricDirection.HIGHER_IS_BETTER,
            "LOWER_IS_BETTER": MetricDirection.LOWER_IS_BETTER,
            "NEUTRAL": MetricDirection.UNKNOWN,
        }[check.direction]
        return NumericComparison(
            target_label=check.target_label,
            target_value=check.target_value,
            baseline_label=check.baseline_label,
            baseline_value=check.baseline_value,
            metric=check.metric,
            direction=direction,
        )

    @classmethod
    def is_numeric_comparison(cls, statement: str) -> bool:
        lowered = statement.casefold()
        numbers = re.findall(cls._NUMBER, statement)
        decimal_or_percent = any("." in value or "%" in value for value in numbers)
        return (
            len(numbers) >= 2
            and decimal_or_percent
            and cls._contains_any(lowered, cls._COMPARISON_MARKERS)
        )

    @classmethod
    def is_table_numeric_statement(cls, statement: str) -> bool:
        """Detect table prose that tries to bind two or more measured values."""

        numbers = re.findall(cls._NUMBER, statement)
        if len(numbers) < 2:
            return False
        if any("." in value or "%" in value for value in numbers):
            return True
        return re.search(
            rf"{cls._NUMBER}\s*(?:→|->|<|>)\s*{cls._NUMBER}",
            statement,
        ) is not None

    @classmethod
    def has_table_numeric_value(cls, statement: str) -> bool:
        """Find measured/configuration values while leaving Big-O notation qualitative."""

        for match in re.finditer(cls._NUMBER, statement):
            if statement[max(0, match.start() - 2):match.start()].casefold() == "o(":
                continue
            return True
        return False

    @staticmethod
    def is_configuration_table(element: ScientificElement) -> bool:
        context = " ".join([element.label, element.explanation]).casefold()
        return any(
            marker in context
            for marker in ("hyperparameter", "configuration", "config.", "超参数", "配置")
        )

    @staticmethod
    def neutral_statement(comparison: NumericComparison) -> str:
        return (
            f"{comparison.target_label} 的 {comparison.metric} 为 "
            f"{comparison.target_value:g}，{comparison.baseline_label} 为 "
            f"{comparison.baseline_value:g}"
        )

    def table_check_statement(self, check: TableNumericCheck) -> str:
        """Render one relation only from an independently accepted table check."""

        comparison = self.from_table_check(check)
        ordering = self.ordering(comparison)
        relation = {
            NumericRelation.GREATER_THAN: "高于",
            NumericRelation.LESS_THAN: "低于",
            NumericRelation.EQUAL: "等于",
        }[ordering]
        difference = abs(self.difference(comparison))
        statement = (
            f"{comparison.target_label} 的 {comparison.metric} 为 {comparison.target_value:g}，"
            f"{comparison.baseline_label} 为 {comparison.baseline_value:g}；"
            f"前者{relation}后者"
        )
        if ordering != NumericRelation.EQUAL:
            statement += f" {difference:g}"
        quality = self.quality_relation(comparison)
        if quality is not None:
            statement += "，按该指标方向前者数值" + (
                "更优" if quality == NumericRelation.BETTER else "更差"
            )
        if check.relative_difference_percent is not None:
            statement += f"，相对差异为 {check.relative_difference_percent:g}%"
        return statement + "。"

    @classmethod
    def _infer_two_value_comparison(cls, statement: str) -> NumericComparison | None:
        if not cls.is_numeric_comparison(statement):
            return None
        values = []
        for match in re.finditer(cls._NUMBER, statement):
            value = match.group(0)
            before = statement[max(0, match.start() - 16):match.start()].casefold()
            after = statement[match.end():match.end() + 8]
            if re.search(
                r"(?:table|figure|equation|page|fig\.?|gpt-?|bert-?)\s*$",
                before,
            ):
                continue
            if re.search(r"(?:表|图|公式|方程|第)\s*$", before):
                continue
            if re.match(r"\s*(?:M|B|K|层|页|维)\b", after):
                continue
            values.append(value)
        if len(values) != 2:
            return None
        parsed = [float(value.rstrip("%")) for value in values]
        return NumericComparison(
            target_label="目标",
            target_value=parsed[0],
            baseline_label="基线",
            baseline_value=parsed[1],
        )

    @classmethod
    def _mentions_values(cls, statement: str, comparison: NumericComparison) -> bool:
        values = [float(value.rstrip("%")) for value in re.findall(cls._NUMBER, statement)]
        return any(cls.values_equal(value, comparison.target_value) for value in values) and any(
            cls.values_equal(value, comparison.baseline_value) for value in values
        )

    @staticmethod
    def _contains_any(value: str, markers: Iterable[str]) -> bool:
        return any(marker in value for marker in markers)
