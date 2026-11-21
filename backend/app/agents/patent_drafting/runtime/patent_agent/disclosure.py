from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from patent_agent.claim_workflow import (
    NO_COMPARABLE_PRIOR_ART_CONCLUSION,
    PUBLICATION_OR_LINK,
)
from patent_agent.errors import DisclosureSectionRecoveryError, ParseError
from patent_agent.quality import (
    check_quantitative_facts,
    sanitize_unsupported_quantitative_facts,
)


DISCLOSURE_REQUIRED_FIELDS = (
    "title",
    "technical_field",
    "background",
    "technical_problem",
    "technical_solution",
    "beneficial_effects",
    "embodiments",
)
DISCLOSURE_FIELDS = (*DISCLOSURE_REQUIRED_FIELDS, "drawing_description")
EXPLANATORY_PREFACE = re.compile(
    r"^\s*(?:以下是|下面是|以下内容为|这是根据|交底书内容如下|说明如下)[：:]?"
)
MARKDOWN_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
LEGAL_CONCLUSION = re.compile(
    r"具备新颖性|具有创造性|必然授权|确保授权|授权概率|不侵权"
)
ILLUSTRATIVE_CUE = re.compile(r"例如|示例|举例|如[：:]|如(?:中文|英文|所述|下列|以下)")
QUOTED_LITERAL = re.compile(
    r"'([^'\n]{1,120})'|\"([^\"\n]{1,120})\"|“([^”\n]{1,120})”|‘([^’\n]{1,120})’"
)


def _validate_section_text(value: str, field_name: str, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    if "```" in normalized:
        raise ValueError(f"{field_name} must not contain a Markdown code fence")
    if MARKDOWN_HEADING.search(normalized):
        raise ValueError(f"{field_name} must not contain a Markdown heading")
    if normalized and EXPLANATORY_PREFACE.search(normalized):
        raise ValueError(f"{field_name} must not contain an explanatory preface")
    return normalized


class DisclosureSections(BaseModel):
    """Business-state contract for newly generated technical disclosures."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str
    technical_field: str
    background: str
    technical_problem: str
    technical_solution: str
    beneficial_effects: str
    embodiments: str
    drawing_description: str = ""

    @field_validator("*")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _validate_section_text(
            value,
            info.field_name,
            required=info.field_name in DISCLOSURE_REQUIRED_FIELDS,
        )


class DisclosureSectionsDraft(BaseModel):
    """Initial-response shape that permits only genuinely missing required fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str | None = None
    technical_field: str | None = None
    background: str | None = None
    technical_problem: str | None = None
    technical_solution: str | None = None
    beneficial_effects: str | None = None
    embodiments: str | None = None
    drawing_description: str = ""

    @model_validator(mode="before")
    @classmethod
    def empty_required_fields_are_missing(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field in DISCLOSURE_REQUIRED_FIELDS:
            if isinstance(normalized.get(field), str) and not normalized[field].strip():
                normalized[field] = None
        return normalized

    @field_validator("*")
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_section_text(value, info.field_name, required=False)


def parse_disclosure_draft(
    value: Any,
) -> tuple[DisclosureSectionsDraft, list[str], list[dict[str, Any]]]:
    normalizations: list[dict[str, Any]] = []
    normalized_value = value
    if isinstance(value, dict):
        normalized_value = dict(value)
        for field in DISCLOSURE_FIELDS:
            body = normalized_value.get(field)
            if not isinstance(body, list):
                continue
            if not body or any(
                not isinstance(item, str) or not item.strip()
                for item in body
            ):
                continue
            normalized_value[field] = "\n".join(item.strip() for item in body)
            normalizations.append(
                {
                    "rule": "string_array_to_text",
                    "field": field,
                    "item_count": len(body),
                }
            )
    try:
        draft = DisclosureSectionsDraft.model_validate(normalized_value)
    except ValidationError as exc:
        raise ParseError(f"structured disclosure JSON is invalid: {exc.error_count()} field error(s)") from None
    missing = [field for field in DISCLOSURE_REQUIRED_FIELDS if not getattr(draft, field)]
    return draft, missing, normalizations


def merge_and_validate_disclosure_sections(
    draft: DisclosureSectionsDraft,
    recovered: Any | None = None,
) -> DisclosureSections:
    values = draft.model_dump()
    missing = [field for field in DISCLOSURE_REQUIRED_FIELDS if not values.get(field)]
    if missing:
        if not isinstance(recovered, dict):
            raise DisclosureSectionRecoveryError(
                "structured disclosure remains incomplete after the allowed recovery",
                missing_sections_before=missing,
                missing_sections_after=missing,
            )
        if set(recovered) != set(missing):
            raise DisclosureSectionRecoveryError(
                "recovery fields do not exactly match the requested missing fields",
                missing_sections_before=missing,
                missing_sections_after=missing,
            )
        try:
            recovery_draft = DisclosureSectionsDraft.model_validate(recovered)
        except ValidationError as exc:
            raise DisclosureSectionRecoveryError(
                f"structured disclosure recovery is invalid: {exc.error_count()} field error(s)",
                missing_sections_before=missing,
                missing_sections_after=missing,
            ) from None
        still_missing: list[str] = []
        for field in missing:
            body = getattr(recovery_draft, field)
            if not body:
                still_missing.append(field)
                continue
            if values.get(field):
                continue
            values[field] = body
        if still_missing:
            raise DisclosureSectionRecoveryError(
                "structured disclosure recovery did not provide every requested field",
                missing_sections_before=missing,
                missing_sections_after=still_missing,
            )
    try:
        return DisclosureSections.model_validate(values)
    except ValidationError as exc:
        remaining = [
            field for field in DISCLOSURE_REQUIRED_FIELDS if not values.get(field)
        ]
        raise DisclosureSectionRecoveryError(
            f"structured disclosure failed final validation: {exc.error_count()} field error(s)",
            missing_sections_before=missing,
            missing_sections_after=remaining,
        ) from None


def render_disclosure_markdown(
    sections: DisclosureSections,
    *,
    search_limitation: str | None = None,
    fixture_notice: str | None = None,
) -> str:
    blocks = [f"# {sections.title}"]
    if search_limitation:
        blocks.append(f"> 检索限制：{search_limitation.strip()}")
    if fixture_notice:
        blocks.append(f"> 检索状态说明：{fixture_notice.strip()}")
    blocks.extend(
        [
            f"## 一、技术领域\n\n{sections.technical_field}",
            f"## 二、背景技术\n\n{sections.background}",
            f"## 三、要解决的技术问题\n\n{sections.technical_problem}",
            f"## 四、技术方案\n\n{sections.technical_solution}",
            f"## 五、有益效果\n\n{sections.beneficial_effects}",
            "## 六、附图说明\n\n"
            + (sections.drawing_description or "本阶段未生成附图。"),
            f"## 七、具体实施方式\n\n{sections.embodiments}",
        ]
    )
    return "\n\n".join(blocks).rstrip() + "\n"


def sanitize_disclosure_quantitative_facts(
    sections: DisclosureSections,
    *,
    source_materials: str,
) -> tuple[DisclosureSections, list[str]]:
    """Remove unsupported values from structured sections without changing the contract."""
    payload = sections.model_dump()
    changed_fields: list[str] = []
    for field in DISCLOSURE_FIELDS:
        original = payload[field]
        sanitized = sanitize_unsupported_quantitative_facts(
            original,
            source_materials,
        )
        if sanitized != original:
            payload[field] = sanitized
            changed_fields.append(field)
    return DisclosureSections.model_validate(payload), changed_fields


def basic_disclosure_content_check(
    sections: DisclosureSections,
    *,
    source_materials: str,
    prior_art: dict[str, Any],
) -> dict[str, Any]:
    text = "\n".join(getattr(sections, field) for field in DISCLOSURE_FIELDS)
    issues: list[dict[str, Any]] = []
    for issue in check_quantitative_facts(text, source_materials):
        issues.append({"rule": "unsupported_quantitative_fact", **issue})

    normalized_source = re.sub(r"\s+", "", source_materials)
    unsupported_literals: list[str] = []
    for statement in re.split(r"(?<=[。！？；])|\n+", text):
        if not ILLUSTRATIVE_CUE.search(statement):
            continue
        for match in QUOTED_LITERAL.finditer(statement):
            literal = next(
                group for group in match.groups() if group is not None
            ).strip()
            if literal and re.sub(r"\s+", "", literal) not in normalized_source:
                unsupported_literals.append(literal)
    if unsupported_literals:
        issues.append(
            {
                "rule": "unsupported_illustrative_literal",
                "message": "结构化交底书包含 CASE MATERIALS 未提供的示例字面量。",
                "count": len(set(unsupported_literals)),
            }
        )

    search_status = str(prior_art.get("search_status") or "")
    if search_status in {
        "complete_zero_results",
        "partial_with_records",
        "partial_no_records",
    } and NO_COMPARABLE_PRIOR_ART_CONCLUSION.search(text):
        issues.append(
            {
                "rule": "unsupported_prior_art_conclusion",
                "message": "零结果或部分检索不得形成现有技术不存在或确定法律结论。",
            }
        )
    if LEGAL_CONCLUSION.search(text):
        issues.append(
            {
                "rule": "prohibited_legal_conclusion",
                "message": "结构化交底书不得包含确定的新颖性、创造性、授权或侵权结论。",
            }
        )

    allowed_tokens: set[str] = set()
    for record in prior_art.get("records") or []:
        if not isinstance(record, dict):
            continue
        for key in ("publication_number", "source_url"):
            token = record.get(key)
            if isinstance(token, str) and token:
                allowed_tokens.add(token.casefold())
    detected_tokens = {
        match.group(0).rstrip(".,，。；;）)]}")
        for match in PUBLICATION_OR_LINK.finditer(text)
        if not match.group(0).casefold().startswith(("http://", "https://"))
    }
    detected_tokens.update(
        match.group(0).rstrip(".,，。；;）)]}")
        for match in re.finditer(r"https?://[^\s，。；;）)]+", text)
    )
    unknown = sorted(
        token for token in detected_tokens if token.casefold() not in allowed_tokens
    )
    if unknown:
        issues.append(
            {
                "rule": "unknown_publication_or_link",
                "message": "结构化交底书包含当前检索记录无法追溯的公开号或链接。",
                "count": len(unknown),
            }
        )
    report = {
        "schema_version": "basic_disclosure_content_check_v1",
        "passed": not issues,
        "issues": issues,
        "warnings": [
            "公式、参数边界、实施例、专利语言、技术效果和图示仍可由专业人员进一步优化。",
            "DOCX 未执行自动逐页视觉检查，专业交付前仍需人工审核。",
        ],
    }
    return report
