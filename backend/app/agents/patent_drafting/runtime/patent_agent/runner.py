from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from patent_agent.adapters.cnipa import (
    SEARCH_STATUSES,
    CnipaAdapter,
    FixtureCnipaAdapter,
    SearchResult,
    dedupe_records,
    normalize_hit,
)
from patent_agent.adapters.documents import DocumentParser
from patent_agent.adapters.fake import FakeModelAdapter
from patent_agent.adapters.qwen import QwenAdapter
from patent_agent.claim_workflow import (
    PARTIAL_SEARCH_LIMITATION,
    PUBLICATION_OR_LINK,
    contains_prohibited_prior_art_conclusion,
    is_no_comparable_analysis,
    normalize_claim_plan,
    normalize_claims,
    normalize_difference_analysis,
    render_claims_markdown,
    validate_claims,
)
from patent_agent.claim_evidence import (
    write_claim_evidence_review,
    write_disclosure_evidence_review,
)
from patent_agent.config import AppConfig
from patent_agent.disclosure import (
    DisclosureSections,
    DisclosureSectionsDraft,
    basic_disclosure_content_check,
    merge_and_validate_disclosure_sections,
    parse_disclosure_draft,
    render_disclosure_markdown,
    sanitize_disclosure_quantitative_facts,
)
from patent_agent.errors import (
    DisclosureSectionRecoveryError,
    DisclosureSemanticContractError,
    ExportError,
    InputChangedError,
    MarkdownNormalizationError,
    ParseError,
    SearchError,
    WAITING_FOR_INPUT,
)
from patent_agent.exporter import (
    export_markdown_and_docx,
    resolve_docx_font,
)
from patent_agent.quality import check_quantitative_facts, sanitize_unsupported_quantitative_facts
from patent_agent.utils import (
    hash_case,
    list_case_files,
    local_timestamp,
    read_json,
    redact_text,
    sha256_file,
    utc_now,
    write_json,
)


STAGES = [
    "load_case",
    "parse_materials",
    "analyze_patent_points",
    "wait_for_patent_point_selection",
    "patent_point_selection",
    "plan_prior_art_queries",
    "search_prior_art",
    "analyze_prior_art_differences",
    "generate_disclosure_preview",
    "generate_disclosure_sections",
    "validate_disclosure_sections",
    "recover_missing_disclosure_fields",
    "render_disclosure_markdown",
    "basic_disclosure_content_check",
    "build_disclosure_evidence_review",
    "build_claim_plan",
    "draft_claims",
    "validate_claims",
    "build_claim_evidence_review",
    "export_results",
]
QUALITY_GATE_FAILED = 24
SEARCH_FAILURE_STATUSES = SEARCH_STATUSES - {"success", "zero_results"}
ZERO_RESULT_LIMITATION = (
    "zero_results 仅表示对应查询未命中，不能据此认定现有技术不存在，"
    "也不能形成新颖性、创造性或授权结论。"
)
DISCLOSURE_REQUIRED_SECTIONS = (
    "TITLE",
    "TECHNICAL_FIELD",
    "BACKGROUND",
    "TECHNICAL_PROBLEM",
    "TECHNICAL_SOLUTION",
    "BENEFICIAL_EFFECTS",
    "EMBODIMENTS",
)
PATENT_POINT_TEXT_FIELDS = (
    "title",
    "technical_background",
    "innovation",
    "difference",
    "feasibility",
)
UNSUPPORTED_EVIDENCE_CUE = re.compile(
    r"(?:已|经)(?:在|由|通过)?[^。；\n]{0,80}"
    r"(?:验证|实测|测试|实验|部署|成熟应用|实现)"
)
UNSUPPORTED_PRIOR_ART_CUE = re.compile(
    r"(?:现有(?:技术|文献|公开技术|公开方案)[^。；\n]{0,80}"
    r"(?:未见|未将|未规定|未建立|未采用|未包含|未涉及|未公开|未披露|没有|不存在)|"
    r"首次(?:提出|显式)|尚无相关方案)"
)
UNVERIFIED_FUTURE_MARKER = re.compile(
    r"(?:需|待|有待|尚未|未经)[^。；\n]{0,40}(?:验证|测试|实验|核对)"
)
CNIPA_BROADENING_SUFFIXES = (
    "决策方法",
    "控制方法",
    "实现机制",
    "决策机制",
    "预测方法",
    "调优方法",
    "建模方法",
    "决策",
    "机制",
    "方法",
    "系统",
    "模型",
    "建模",
    "控制",
    "约束",
    "反馈",
    "评分",
    "预测",
)
DISCLOSURE_SECTION_ALIASES = {
    "TITLE": ("发明名称", "专利名称", "案件名称"),
    "TECHNICAL_FIELD": ("技术领域", "所属技术领域"),
    "BACKGROUND": (
        "背景技术",
        "现有技术及其缺陷",
        "现有技术",
        "技术背景与现有技术",
        "介绍相关技术背景",
    ),
    "TECHNICAL_PROBLEM": (
        "要解决的技术问题",
        "发明要解决的问题",
        "本发明所要解决的技术问题",
        "本方案解决的技术问题",
    ),
    "TECHNICAL_SOLUTION": (
        "技术方案",
        "发明内容",
        "解决方案",
        "本发明技术方案的详细阐述",
    ),
    "BENEFICIAL_EFFECTS": (
        "有益效果",
        "技术效果",
        "本方案的效果",
        "与现有技术相比的优点",
        "与现有技术相比本发明具有哪些优点",
    ),
    "EMBODIMENTS": ("具体实施方式", "实施例", "详细实施方式"),
    "DRAWING_DESCRIPTION": ("附图说明",),
}
DISCLOSURE_SECTION_ORDER = (
    "TITLE",
    "TECHNICAL_FIELD",
    "BACKGROUND",
    "TECHNICAL_PROBLEM",
    "TECHNICAL_SOLUTION",
    "BENEFICIAL_EFFECTS",
    "DRAWING_DESCRIPTION",
    "EMBODIMENTS",
)
DISCLOSURE_SECTION_FIELDS = {
    "TITLE": "title",
    "TECHNICAL_FIELD": "technical_field",
    "BACKGROUND": "background",
    "TECHNICAL_PROBLEM": "technical_problem",
    "TECHNICAL_SOLUTION": "technical_solution",
    "BENEFICIAL_EFFECTS": "beneficial_effects",
    "DRAWING_DESCRIPTION": "drawing_description",
    "EMBODIMENTS": "embodiments",
}
DISCLOSURE_SECTION_HEADINGS = {
    "TITLE": "发明名称",
    "TECHNICAL_FIELD": "技术领域",
    "BACKGROUND": "背景技术",
    "TECHNICAL_PROBLEM": "要解决的技术问题",
    "TECHNICAL_SOLUTION": "技术方案",
    "BENEFICIAL_EFFECTS": "有益效果",
    "DRAWING_DESCRIPTION": "附图说明",
    "EMBODIMENTS": "具体实施方式",
}
MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
NUMBERED_HEADING = re.compile(
    r"^\s*(?:第?[一二三四五六七八九十百]+[章节部分]?|[0-9]+(?:\.[0-9]+)*)\s*[、.．)）：:]\s*(.+?)\s*$"
)
BOLD_FIELD_HEADING = re.compile(r"^\s*\*\*(.+?)\*\*\s*[：:]\s*(.*?)\s*$")
MARKDOWN_FENCE_LINE = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,})(?P<info>[^\r\n]*)$"
)
MARKDOWN_DOCUMENT_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
ALLOWED_OUTER_MARKDOWN_FENCES = {"```", "```md", "```markdown"}


@dataclass(frozen=True)
class MarkdownNormalizationResult:
    text: str
    status: str
    reason: str
    raw_sha256: str
    normalized_sha256: str
    content_changed: bool
    fence_count: int
    first_fence_line: int | None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_model_markdown_document(text: str) -> MarkdownNormalizationResult:
    """Remove only a uniquely identifiable, unclosed whole-document Markdown fence."""
    if not isinstance(text, str):
        raise ParseError("model Markdown document must be a string")

    lines = text.splitlines(keepends=True)
    fence_rows: list[tuple[int, re.Match[str]]] = []
    first_nonempty_index: int | None = None
    for index, line in enumerate(lines):
        value = line.rstrip("\r\n")
        if first_nonempty_index is None and value.strip():
            first_nonempty_index = index
        match = MARKDOWN_FENCE_LINE.match(value)
        if match:
            fence_rows.append((index, match))

    raw_sha256 = _sha256_text(text)
    if not fence_rows:
        return MarkdownNormalizationResult(
            text=text,
            status="not_needed",
            reason="no_markdown_fence",
            raw_sha256=raw_sha256,
            normalized_sha256=raw_sha256,
            content_changed=False,
            fence_count=0,
            first_fence_line=None,
        )

    open_fence: tuple[int, str, int] | None = None
    ambiguous_nested_marker = False
    for index, match in fence_rows:
        marker = match.group("fence")
        info = match.group("info")
        if open_fence is None:
            open_fence = (index, marker[0], len(marker))
            continue
        _open_index, open_character, open_length = open_fence
        is_closer = (
            marker[0] == open_character
            and len(marker) >= open_length
            and not info.strip()
        )
        if is_closer:
            open_fence = None
        else:
            ambiguous_nested_marker = True

    if open_fence is None and not ambiguous_nested_marker:
        return MarkdownNormalizationResult(
            text=text,
            status="not_needed",
            reason="markdown_fences_balanced",
            raw_sha256=raw_sha256,
            normalized_sha256=raw_sha256,
            content_changed=False,
            fence_count=len(fence_rows),
            first_fence_line=fence_rows[0][0] + 1,
        )

    first_fence_index = fence_rows[0][0]
    is_allowed_outer = (
        len(fence_rows) == 1
        and first_nonempty_index == first_fence_index
        and lines[first_fence_index].strip() in ALLOWED_OUTER_MARKDOWN_FENCES
        and any(
            MARKDOWN_DOCUMENT_HEADING.match(line.rstrip("\r\n"))
            for line in lines[first_fence_index + 1 :]
        )
    )
    if is_allowed_outer:
        normalized = "".join(
            [*lines[:first_fence_index], *lines[first_fence_index + 1 :]]
        )
        return MarkdownNormalizationResult(
            text=normalized,
            status="normalized",
            reason="unclosed_outer_markdown_fence",
            raw_sha256=raw_sha256,
            normalized_sha256=_sha256_text(normalized),
            content_changed=True,
            fence_count=1,
            first_fence_line=first_fence_index + 1,
        )

    raise MarkdownNormalizationError(
        "Markdown fence structure is ambiguous and was not modified",
        fence_count=len(fence_rows),
        first_fence_line=first_fence_index + 1,
        raw_sha256=raw_sha256,
    )


def aggregate_search_status(query_results: list[dict[str, Any]]) -> str:
    """Classify completed real-query evidence without treating Fixture data as real success."""
    statuses = {str(row.get("status") or "") for row in query_results}
    has_records = any(
        int(row.get("result_count") or 0) > 0 or bool(row.get("records"))
        for row in query_results
    )
    has_failure = bool(statuses & SEARCH_FAILURE_STATUSES)
    has_normal_completion = bool(statuses & {"success", "zero_results"})
    if has_records:
        return "partial_with_records" if has_failure else "complete_with_records"
    if has_failure and has_normal_completion:
        return "partial_no_records"
    if has_failure:
        return "failed"
    return "complete_zero_results"


def search_limitation_for(status: str) -> str:
    if status in {"partial_with_records", "partial_no_records"}:
        return PARTIAL_SEARCH_LIMITATION
    if status == "complete_zero_results":
        return ZERO_RESULT_LIMITATION
    if status == "failed":
        return (
            "本次真实专利检索未获得正常完成证据，后续草案仅基于案件材料生成；"
            "不得据此形成现有技术、新颖性、创造性、授权或其他法律结论。"
        )
    return (
        "本次差异分析仅基于当前返回的检索记录，不构成完整查新、"
        "新颖性、创造性、授权或其他法律结论。"
    )


def prior_search_limitation(prior: dict[str, Any]) -> str:
    limitations = prior.get("search_limitations")
    if isinstance(limitations, list) and limitations:
        return str(limitations[0])
    if isinstance(limitations, str) and limitations:
        return limitations
    return search_limitation_for(str(prior.get("search_status") or ""))


def ensure_markdown_search_limitation(markdown: str, prior: dict[str, Any]) -> str:
    limitation = prior_search_limitation(prior)
    if prior.get("search_status") not in {"partial_with_records", "partial_no_records"}:
        return markdown
    if limitation in markdown:
        return markdown
    lines = markdown.splitlines()
    insertion = 1 if lines else 0
    lines[insertion:insertion] = ["", f"> 检索限制：{limitation}", ""]
    return "\n".join(lines)


def normalize_patent_points(
    points: Any,
    *,
    minimum: int = 3,
    maximum: int = 5,
) -> list[dict[str, Any]]:
    if not isinstance(points, list) or not minimum <= len(points) <= maximum:
        raise ParseError(
            f"candidate patent point contract requires {minimum} to {maximum} points"
        )
    normalized: list[dict[str, Any]] = []
    for index, point in enumerate(points, 1):
        if not isinstance(point, dict):
            raise ParseError("candidate patent point must be an object")
        row = dict(point)
        row["id"] = f"PP-{index:03d}"
        normalized.append(row)
    return normalized


def _sanitize_candidate_statement(
    text: str,
    *,
    source_materials: str,
) -> tuple[str, list[str]]:
    rules: list[str] = []
    sanitized = sanitize_unsupported_quantitative_facts(text, source_materials)
    if sanitized != text:
        rules.append("unsupported_quantitative_fact")

    normalized_source = re.sub(r"\s+", "", source_materials)
    pieces = re.split(r"(?<=[。；])", sanitized)
    grounded: list[str] = []
    for piece in pieces:
        compact = piece.strip()
        if not compact:
            continue
        normalized_piece = re.sub(r"\s+", "", compact)
        if UNSUPPORTED_PRIOR_ART_CUE.search(compact):
            grounded.append("该候选差异需结合后续真实专利检索进一步核对。")
            rules.append("unsupported_prior_art_claim")
            continue
        if (
            UNSUPPORTED_EVIDENCE_CUE.search(compact)
            and not UNVERIFIED_FUTURE_MARKER.search(compact)
            and normalized_piece not in normalized_source
        ):
            grounded.append("该实现效果属于技术推演，尚未经实验验证。")
            rules.append("unsupported_validation_claim")
            continue
        if "现有技术" in compact and "现有技术" not in source_materials:
            compact = compact.replace("现有技术", "案件材料所述原型")
            rules.append("ungrounded_background_scope")
        grounded.append(compact)
    return "".join(grounded).strip(), list(dict.fromkeys(rules))


def ground_patent_points(
    points: list[dict[str, Any]],
    *,
    source_materials: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grounded: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for point in points:
        row = dict(point)
        for field in PATENT_POINT_TEXT_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ParseError(
                    f"candidate patent point {field} must be a non-empty string"
                )
            row[field], rules = _sanitize_candidate_statement(
                " ".join(value.split()).strip(),
                source_materials=source_materials,
            )
            for rule in rules:
                issues.append(
                    {
                        "point_id": row["id"],
                        "field": field,
                        "rule": rule,
                    }
                )
        grounded.append(row)
    return grounded, {
        "schema_version": "patent_point_grounding_v1",
        "status": "sanitized" if issues else "passed",
        "issue_count": len(issues),
        "issues": issues,
    }


def derive_broader_cnipa_query(queries: list[str]) -> str | None:
    candidates: list[str] = []
    for query in queries:
        compact = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]+", "", query)
        if compact.startswith("基于"):
            compact = compact[2:]
        for suffix in CNIPA_BROADENING_SUFFIXES:
            if compact.endswith(suffix) and len(compact) - len(suffix) >= 2:
                compact = compact[: -len(suffix)]
                break
        if 2 <= len(compact) <= 10 and compact not in queries:
            candidates.append(compact)
    if not candidates:
        return None
    return min(dict.fromkeys(candidates), key=len)


def _compact_text(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _best_effort_difference_analysis(
    *,
    selected: dict[str, Any],
    search_mode: str,
    search_status: str,
    prior_art_records: list[dict[str, Any]],
) -> dict[str, Any]:
    feature_texts: list[str] = []
    for key in ("innovation", "feasibility", "title"):
        text = _compact_text(selected.get(key), "")
        if text and not contains_prohibited_prior_art_conclusion(text):
            feature_texts.append(text)
    feature_texts = list(dict.fromkeys(feature_texts))[:5]
    if not feature_texts:
        feature_texts = ["依据案件材料形成技术方案处理流程"]
    features = [
        {
            "feature_id": f"RAW-{index:03d}",
            "feature_text": text,
            "source_summary": "best-effort 从选定专利点提取，需人工核对材料支撑。",
        }
        for index, text in enumerate(feature_texts, 1)
    ]
    comparisons: list[dict[str, Any]] = []
    distinguishing: list[dict[str, str]] = []
    if prior_art_records:
        record = prior_art_records[0]
        for index, feature in enumerate(features):
            comparisons.append(
                {
                    "feature_id": feature["feature_id"],
                    "publication_number": record["publication_number"],
                    "prior_art_title": record["title"],
                    "disclosure_status": "uncertain",
                    "analysis": "best-effort 未完成可靠语义比较，当前结果必须由人工核对。",
                    "recommended_claim_role": (
                        "independent" if index == 0 else "background_only"
                    ),
                }
            )
        distinguishing.append(
            {
                "feature_id": features[0]["feature_id"],
                "reason": "仅作为本方案保护候选，区别关系仍待人工核对。",
                "recommended_claim_role": "independent",
            }
        )
    return normalize_difference_analysis(
        {
            "target_features": features,
            "comparisons": comparisons,
            "candidate_distinguishing_features": distinguishing,
            "limitations": ["best-effort 自动降级结果，差异关系和保护范围均待人工核对。"],
        },
        selected_patent_point_id=str(selected.get("id") or "PP-001"),
        search_mode=search_mode,
        prior_art_records=prior_art_records,
        search_status=search_status,
    )


def _best_effort_preview(
    selected: dict[str, Any],
    difference: dict[str, Any],
) -> dict[str, Any]:
    features = [
        str(row.get("feature_text") or "").strip()
        for row in difference.get("target_features", [])
        if str(row.get("feature_text") or "").strip()
    ]
    return {
        "working_title": _compact_text(
            selected.get("title"),
            "基于案件材料的专利技术方案",
        ),
        "technical_problems": [
            _compact_text(
                selected.get("technical_background"),
                "现有实现仍存在需要进一步解决的技术问题。",
            )
        ],
        "core_steps": features[:6] or ["依据案件材料执行相应技术处理流程"],
        "closest_difference": "best-effort 未形成可靠差异结论，待人工结合检索结果核对。",
    }


def _best_effort_disclosure_sections(
    *,
    initial_response: Any,
    selected: dict[str, Any],
    preview: dict[str, Any],
) -> DisclosureSections:
    accepted: dict[str, str] = {}
    if isinstance(initial_response, dict):
        for field, value in initial_response.items():
            if field not in {
                "title",
                "technical_field",
                "background",
                "technical_problem",
                "technical_solution",
                "beneficial_effects",
                "embodiments",
                "drawing_description",
            }:
                continue
            try:
                draft = DisclosureSectionsDraft.model_validate({field: value})
            except Exception:
                continue
            body = getattr(draft, field)
            if body:
                accepted[field] = body

    core_steps = preview.get("core_steps")
    if not isinstance(core_steps, list):
        core_steps = []
    technical_problems = preview.get("technical_problems")
    if not isinstance(technical_problems, list):
        technical_problems = []
    title = accepted.get("title") or _compact_text(
        preview.get("working_title") or selected.get("title"),
        "基于案件材料的专利技术方案",
    )
    technical_solution = accepted.get("technical_solution") or "；".join(
        _compact_text(item, "") for item in core_steps if _compact_text(item, "")
    )
    technical_solution = technical_solution or _compact_text(
        selected.get("innovation"),
        "依据案件材料执行相应技术处理流程。",
    )
    values = {
        "title": title,
        "technical_field": accepted.get("technical_field")
        or "本方案涉及与案件材料所述技术方案相关的计算机实现领域。",
        "background": accepted.get("background")
        or _compact_text(
            selected.get("technical_background"),
            "现有实现背景和具体约束仍需结合案件材料进一步补充。",
        ),
        "technical_problem": accepted.get("technical_problem")
        or "；".join(
            _compact_text(item, "")
            for item in technical_problems
            if _compact_text(item, "")
        )
        or "需要解决案件材料所描述的技术处理与实现稳定性问题。",
        "technical_solution": technical_solution,
        "beneficial_effects": accepted.get("beneficial_effects")
        or "本方案预期能够形成与上述技术问题相对应的技术处理效果，具体效果待人工核对。",
        "embodiments": accepted.get("embodiments")
        or f"一种可行实施方式为：{technical_solution}",
        "drawing_description": accepted.get("drawing_description") or "",
    }
    return DisclosureSections.model_validate(values)


def _best_effort_claim_plan(
    *,
    difference: dict[str, Any],
    title: str,
    search_mode: str,
) -> dict[str, Any]:
    features = list(difference.get("target_features") or [])
    if not features:
        features = [
            {
                "feature_id": "TF-001",
                "feature_text": "依据案件材料执行相应技术处理流程",
            }
        ]
    essentials = [
        {
            "feature_id": str(row["feature_id"]),
            "text": _compact_text(row.get("feature_text"), "案件材料技术特征"),
            "reason": "best-effort 保护候选，需人工核对必要性和材料支撑。",
        }
        for row in features
    ]
    search_status = str(
        difference.get("analysis_scope", {}).get("search_status") or ""
    )
    return {
        "schema_version": "claim_plan_v1",
        "artifact_metadata": {
            "search_mode": search_mode,
            "search_status": search_status or None,
            "search_limitation": search_limitation_for(search_status),
            "fixture_based": search_mode != "real_cnipa",
            "analysis_status": difference.get("analysis_status"),
            "notice": "best-effort AI 辅助规划，必须由专利专业人员审核。",
        },
        "title": title,
        "recommended_claim_types": ["method"],
        "independent_claims": [
            {
                "claim_type": "method",
                "technical_subject": f"一种{title}方法",
                "essential_features": essentials,
            }
        ],
        "dependent_feature_groups": [],
        "excluded_or_background_features": [],
        "warnings": [
            "best-effort 仅保证形成最小权利要求规划，类型、层级和保护范围待优化。"
        ],
    }


def _best_effort_claims(
    *,
    plan: dict[str, Any],
    search_mode: str,
    search_status: str,
) -> dict[str, Any]:
    raw_claims: list[dict[str, Any]] = []
    next_number = 1
    independent_numbers: dict[str, int] = {}
    independent_features: dict[str, set[str]] = {}
    for planned in plan.get("independent_claims") or []:
        claim_type = str(planned.get("claim_type") or "method")
        subject = _compact_text(
            planned.get("technical_subject"),
            f"一种{plan.get('title') or '技术处理'}方法",
        )
        features = list(planned.get("essential_features") or [])
        feature_ids = [str(row.get("feature_id")) for row in features if row.get("feature_id")]
        bodies = [
            PUBLICATION_OR_LINK.sub(
                "相关技术标识",
                _compact_text(row.get("text"), "案件材料技术特征"),
            )
            for row in features
        ]
        raw_claims.append(
            {
                "claim_id": f"RAW-{next_number:03d}",
                "claim_number": next_number,
                "claim_type": f"independent_{claim_type}",
                "depends_on": [],
                "text": f"{next_number}. {subject}，其特征在于，包括：{'；'.join(bodies)}。",
                "feature_ids": feature_ids,
            }
        )
        independent_numbers[claim_type] = next_number
        independent_features[claim_type] = set(feature_ids)
        next_number += 1

    for group in plan.get("dependent_feature_groups") or []:
        claim_type = str(group.get("parent_claim_type") or "")
        parent = independent_numbers.get(claim_type)
        if not parent:
            continue
        for feature in group.get("features") or []:
            feature_id = str(feature.get("feature_id") or "")
            if not feature_id or feature_id in independent_features.get(claim_type, set()):
                continue
            body = PUBLICATION_OR_LINK.sub(
                "相关技术标识",
                _compact_text(feature.get("text"), "进一步限定的技术特征"),
            )
            raw_claims.append(
                {
                    "claim_id": f"RAW-{next_number:03d}",
                    "claim_number": next_number,
                    "claim_type": f"dependent_{claim_type}",
                    "depends_on": [parent],
                    "text": (
                        f"{next_number}. 根据权利要求{parent}所述的"
                        f"{'系统' if claim_type in {'system', 'device'} else '方法'}，"
                        f"其特征在于，{body}。"
                    ),
                    "feature_ids": [feature_id],
                }
            )
            next_number += 1
    return normalize_claims(
        {"claims": raw_claims},
        search_mode=search_mode,
        search_status=search_status or None,
        search_limitation=search_limitation_for(search_status),
    )


def _normalize_disclosure_heading(value: str) -> str:
    value = value.strip().strip("*_` ")
    value = re.sub(
        r"^(?:第?[一二三四五六七八九十百]+[章节部分]?|[0-9]+(?:\.[0-9]+)*)\s*[、.．)）:：-]\s*",
        "",
        value,
    )
    value = re.sub(r"[\s“”\"'《》【】\[\]（）()、，,。？?！!：:；;·_\-]+", "", value)
    return value


def _disclosure_section_for_heading(value: str) -> tuple[str | None, str]:
    normalized = _normalize_disclosure_heading(value)
    matches: list[tuple[int, str, str]] = []
    for section, aliases in DISCLOSURE_SECTION_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_disclosure_heading(alias)
            index = normalized.find(normalized_alias)
            if index >= 0:
                matches.append((index, section, normalized_alias))
    if not matches:
        return None, ""
    index, section, alias = min(matches, key=lambda row: (row[0], -len(row[2])))
    inline_body = normalized[index + len(alias) :]
    return section, inline_body


def _disclosure_heading_from_line(line: str) -> tuple[str | None, str]:
    heading = MARKDOWN_HEADING.match(line)
    numbered = NUMBERED_HEADING.match(line) if not heading else None
    bold_field = BOLD_FIELD_HEADING.match(line) if not heading and not numbered else None
    raw_heading = (
        heading.group(1)
        if heading
        else numbered.group(1)
        if numbered
        else bold_field.group(1)
        if bold_field
        else None
    )
    if raw_heading is None:
        return None, ""
    section, inline_body = _disclosure_section_for_heading(raw_heading)
    if bold_field and bold_field.group(2).strip():
        inline_body = "\n".join(
            part for part in (inline_body, bold_field.group(2).strip()) if part
        )
    return section, inline_body


def _disclosure_semantic_sections(markdown: str) -> dict[str, str]:
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current:
                bodies[current].append(line)
            continue
        if in_fence:
            if current:
                bodies[current].append(line)
            continue
        section, inline_body = _disclosure_heading_from_line(line)
        if section:
            current = section
            bodies.setdefault(section, [])
            if inline_body:
                bodies[section].append(inline_body)
            continue
        if current:
            bodies[current].append(line)
    return {section: "\n".join(lines).strip() for section, lines in bodies.items()}


def disclosure_missing_sections(markdown: Any) -> tuple[list[str], list[str]]:
    if not isinstance(markdown, str):
        return list(DISCLOSURE_REQUIRED_SECTIONS), []
    sections = _disclosure_semantic_sections(markdown.strip())
    absent = [section for section in DISCLOSURE_REQUIRED_SECTIONS if section not in sections]
    empty = [
        section
        for section in DISCLOSURE_REQUIRED_SECTIONS
        if section in sections and not sections[section]
    ]
    return absent + empty, empty


def normalize_disclosure_markdown(value: Any) -> str:
    if not isinstance(value, str):
        raise DisclosureSemanticContractError(list(DISCLOSURE_REQUIRED_SECTIONS))
    markdown = value.strip()
    lines = markdown.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip() in ALLOWED_OUTER_MARKDOWN_FENCES
        and lines[-1].strip() == "```"
    ):
        markdown = "\n".join(lines[1:-1]).strip()
    missing, empty = disclosure_missing_sections(markdown)
    if missing:
        raise DisclosureSemanticContractError(missing, empty)
    return markdown


def normalize_disclosure_section_recovery(
    value: Any,
    missing_sections: list[str],
) -> dict[str, str]:
    expected_fields = [DISCLOSURE_SECTION_FIELDS[section] for section in missing_sections]
    if not isinstance(value, dict):
        raise DisclosureSectionRecoveryError(
            "disclosure section recovery returned invalid JSON object",
            missing_sections_before=missing_sections,
        )
    if set(value) != set(expected_fields):
        raise DisclosureSectionRecoveryError(
            "disclosure section recovery fields do not exactly match requested sections",
            missing_sections_before=missing_sections,
        )
    recovered: dict[str, str] = {}
    for section in missing_sections:
        field = DISCLOSURE_SECTION_FIELDS[section]
        body = value.get(field)
        if not isinstance(body, str) or not body.strip():
            raise DisclosureSectionRecoveryError(
                f"disclosure section recovery field is empty: {field}",
                missing_sections_before=missing_sections,
            )
        if "```" in body:
            raise DisclosureSectionRecoveryError(
                f"disclosure section recovery field contains a Markdown code fence: {field}",
                missing_sections_before=missing_sections,
            )
        recovered[section] = body.strip()
    return recovered


def merge_disclosure_sections(
    markdown: str,
    recovered_sections: dict[str, str],
) -> str:
    lines = markdown.strip().splitlines()
    order = {section: index for index, section in enumerate(DISCLOSURE_SECTION_ORDER)}
    for section in DISCLOSURE_SECTION_ORDER:
        body = recovered_sections.get(section)
        if body is None:
            continue
        existing = _disclosure_semantic_sections("\n".join(lines))
        if existing.get(section):
            continue
        heading_rows = [
            (index, detected)
            for index, line in enumerate(lines)
            for detected, _inline in [_disclosure_heading_from_line(line)]
            if detected
        ]
        same_heading = next(
            (index for index, detected in heading_rows if detected == section),
            None,
        )
        if same_heading is not None:
            lines[same_heading + 1 : same_heading + 1] = ["", body]
            continue
        insertion = next(
            (
                index
                for index, detected in heading_rows
                if order[detected] > order[section]
            ),
            len(lines),
        )
        block = [f"## {DISCLOSURE_SECTION_HEADINGS[section]}", "", body, ""]
        if insertion == len(lines) and lines and lines[-1].strip():
            block.insert(0, "")
        lines[insertion:insertion] = block
    return "\n".join(lines).strip()


class RunStore:
    def __init__(self, config: AppConfig, run_id: str):
        if (
            not run_id
            or run_id in {".", ".."}
            or "/" in run_id
            or "\\" in run_id
            or "\0" in run_id
            or Path(run_id).is_absolute()
        ):
            raise ParseError("invalid run id: expected one directory name without path separators")
        runs_root = config.runs_dir.expanduser().resolve()
        run_path = (runs_root / run_id).resolve()
        if run_path.parent != runs_root:
            raise ParseError("invalid run id: resolved path is outside runs directory")
        self.config = config
        self.run_id = run_id
        self.path = run_path
        self.state_path = self.path / "run.json"

    def load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            raise ParseError(f"run not found: {self.run_id}")
        return read_json(self.state_path)

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        write_json(self.state_path, state)

    def begin_stage(self, state: dict[str, Any], stage: str) -> None:
        state["status"] = "running"
        state["current_stage"] = stage
        state["error"] = None
        timings = state.setdefault("stage_timings", [])
        timings.append(
            {
                "stage": stage,
                "attempt": 1
                + sum(
                    1
                    for row in timings
                    if isinstance(row, dict) and row.get("stage") == stage
                ),
                "started_at": self._timing_now(),
                "completed_at": None,
                "elapsed_seconds": None,
                "outcome": "running",
            }
        )
        self.save(state)

    def complete_stage(self, state: dict[str, Any], stage: str) -> None:
        if stage not in state["completed_stages"]:
            state["completed_stages"].append(stage)
        self._finish_timing(state, stage, "completed")
        self.save(state)

    def fail(self, state: dict[str, Any], exc: Exception) -> None:
        state["status"] = "failed"
        self._finish_timing(state, str(state.get("current_stage") or ""), "failed")
        error = {"type": type(exc).__name__, "message": str(exc), "at": utc_now()}
        if isinstance(exc, DisclosureSemanticContractError):
            error.update(
                {
                    "stage": state.get("current_stage"),
                    "error_type": exc.error_type,
                    "missing_sections": exc.missing_sections,
                    "empty_sections": exc.empty_sections,
                }
            )
        elif isinstance(exc, DisclosureSectionRecoveryError):
            error.update(
                {
                    "stage": state.get("current_stage"),
                    "error_type": exc.error_type,
                    "missing_sections_before": exc.missing_sections_before,
                    "missing_sections_after": exc.missing_sections_after,
                }
            )
            if state.get("disclosure_generation_mode") == "structured_sections":
                recovery = state.setdefault("section_recovery", {})
                recovery.update(
                    {
                        "attempted": True,
                        "requested_fields": exc.missing_sections_before,
                        "recovered_fields": [
                            field
                            for field in exc.missing_sections_before
                            if field not in exc.missing_sections_after
                        ],
                        "status": "failed",
                    }
                )
            else:
                recovery = state.setdefault("disclosure_section_recovery", {})
                recovery.update(
                    {
                        "attempted": True,
                        "missing_sections_before": exc.missing_sections_before,
                        "missing_sections_after": exc.missing_sections_after,
                        "status": "failed",
                    }
                )
        elif isinstance(exc, MarkdownNormalizationError):
            error.update(
                {
                    "stage": state.get("current_stage"),
                    "error_type": exc.error_type,
                    "fence_count": exc.fence_count,
                    "first_fence_line": exc.first_fence_line,
                }
            )
            normalization = state.setdefault("markdown_normalization", {})
            normalization.update(
                {
                    "attempted": True,
                    "status": "failed",
                    "reason": exc.error_type,
                    "raw_sha256": exc.raw_sha256,
                    "normalized_sha256": None,
                    "content_changed": False,
                    "fence_count": exc.fence_count,
                    "first_fence_line": exc.first_fence_line,
                }
            )
        state["error"] = error
        self.save(state)

    @staticmethod
    def _timing_now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _finish_timing(
        state: dict[str, Any],
        stage: str,
        outcome: str,
    ) -> None:
        if not stage:
            return
        for row in reversed(state.get("stage_timings") or []):
            if (
                isinstance(row, dict)
                and row.get("stage") == stage
                and row.get("outcome") == "running"
            ):
                completed_at = RunStore._timing_now()
                row["completed_at"] = completed_at
                try:
                    started = datetime.fromisoformat(
                        str(row["started_at"]).replace("Z", "+00:00")
                    )
                    completed = datetime.fromisoformat(
                        completed_at.replace("Z", "+00:00")
                    )
                    row["elapsed_seconds"] = max(
                        0.0,
                        round((completed - started).total_seconds(), 3),
                    )
                except (TypeError, ValueError):
                    row["elapsed_seconds"] = None
                row["outcome"] = outcome
                return


class PatentRunner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.vendor_root = config.root / "vendor" / "patent-disclosure-skill"

    def _model(self, fake: bool):
        return FakeModelAdapter() if fake else QwenAdapter(self.config.qwen)

    def _prompt(self, name: str) -> str:
        return (self.vendor_root / "prompts" / name).read_text(encoding="utf-8")

    def _project_prompt(self, name: str) -> str:
        return (Path(__file__).resolve().parent / "prompts" / name).read_text(encoding="utf-8")

    def _flow_first(self, state: dict[str, Any] | None = None) -> bool:
        if state is not None:
            return state.get("workflow_mode", "strict") == "flow_first"
        return self.config.workflow_mode == "flow_first"

    def _record_best_effort(
        self,
        store: RunStore,
        state: dict[str, Any],
        *,
        stage: str,
        reason: str,
        fallback: str,
    ) -> None:
        evidence = state.setdefault(
            "best_effort",
            {"used": False, "events": []},
        )
        event = {
            "stage": stage,
            "reason": reason,
            "fallback": fallback,
        }
        if event not in evidence["events"]:
            evidence["events"].append(event)
        evidence["used"] = True
        warning = f"flow_first 在 {stage} 使用 best-effort：{fallback}"
        state["warnings"] = list(
            dict.fromkeys([*(state.get("warnings") or []), warning])
        )
        store.save(state)

    def _legacy_normalize_markdown_artifact(
        self,
        *,
        store: RunStore,
        state: dict[str, Any],
        text: str,
        phase: str,
        raw_path: Path,
        normalized_path: Path,
    ) -> str:
        """Legacy Markdown generation helper; structured new Runs never call it."""
        try:
            result = normalize_model_markdown_document(text)
        except MarkdownNormalizationError as exc:
            pass_evidence = {
                "attempted": True,
                "status": "failed",
                "reason": exc.error_type,
                "raw_path": raw_path.relative_to(store.path).as_posix(),
                "normalized_path": None,
                "raw_sha256": exc.raw_sha256,
                "normalized_sha256": None,
                "content_changed": False,
                "fence_count": exc.fence_count,
                "first_fence_line": exc.first_fence_line,
            }
            normalization = state.setdefault("markdown_normalization", {})
            passes = normalization.setdefault("passes", {})
            passes[phase] = pass_evidence
            normalization.update(pass_evidence)
            normalization["passes"] = passes
            store.save(state)
            raise

        normalized_path.write_text(result.text, encoding="utf-8")
        pass_evidence = {
            "attempted": True,
            "status": result.status,
            "reason": result.reason,
            "raw_path": raw_path.relative_to(store.path).as_posix(),
            "normalized_path": normalized_path.relative_to(store.path).as_posix(),
            "raw_sha256": result.raw_sha256,
            "normalized_sha256": result.normalized_sha256,
            "content_changed": result.content_changed,
            "fence_count": result.fence_count,
            "first_fence_line": result.first_fence_line,
        }
        normalization = state.setdefault("markdown_normalization", {})
        passes = normalization.setdefault("passes", {})
        passes[phase] = pass_evidence
        changed_pass = next(
            (
                evidence
                for evidence in passes.values()
                if evidence.get("status") == "normalized"
            ),
            pass_evidence,
        )
        normalization.update(changed_pass)
        normalization["attempted"] = True
        normalization["status"] = (
            "normalized"
            if any(row.get("status") == "normalized" for row in passes.values())
            else "not_needed"
        )
        normalization["content_changed"] = any(
            bool(row.get("content_changed")) for row in passes.values()
        )
        normalization["passes"] = passes
        store.save(state)
        return result.text

    def start(self, case_dir: Path, *, fake: bool | None = None, allow_fixture_fallback: bool = False) -> tuple[str, int]:
        case_dir = case_dir.expanduser().resolve()
        if not case_dir.is_dir():
            raise ParseError(f"case directory not found: {case_dir}")
        input_hash = hash_case(case_dir)
        if not list_case_files(case_dir):
            raise ParseError("case contains no supported materials")
        run_id = f"{local_timestamp()}-{input_hash[:8]}"
        suffix = 1
        while (self.config.runs_dir / run_id).exists():
            suffix += 1
            run_id = f"{local_timestamp()}-{input_hash[:8]}-{suffix}"
        store = RunStore(self.config, run_id)
        for rel in ("inputs", "parsed", "interrupts", "prior_art", "artifacts", "logs"):
            (store.path / rel).mkdir(parents=True, exist_ok=True)
        source_ref = os.path.relpath(case_dir, self.config.root)
        use_fake = self.config.fake_mode if fake is None else fake
        state: dict[str, Any] = {
            "schema_version": "run_state_v1",
            "run_id": run_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "status": "running",
            "current_stage": "load_case",
            "completed_stages": [],
            "stage_timings": [],
            "pending_action": None,
            "source_case": source_ref,
            "input_sha256": input_hash,
            "input_snapshot": {"path": "inputs", "sha256": None},
            "provider_mode": "fake" if use_fake else "qwen",
            "workflow_mode": self.config.workflow_mode,
            "best_effort": {"used": False, "events": []},
            "search_mode": None,
            "search_status": None,
            "allow_fixture_fallback": bool(allow_fixture_fallback or self.config.cnipa.allow_fixture_fallback),
            "external_checks": {"qwen": "fixture" if use_fake else "not_run", "cnipa": "fixture" if use_fake else "not_run"},
            "error": None,
            "artifacts": {},
            "difference_analysis": None,
            "disclosure_generation_mode": "structured_sections",
            "section_recovery": {
                "attempted": False,
                "requested_fields": [],
                "recovered_fields": [],
                "status": "not_needed",
            },
            "disclosure_sections": None,
            "disclosure_evidence_review": None,
            "claim_plan": None,
            "claims": None,
            "claim_validation": None,
            "claim_evidence_review": None,
            "release_readiness": {
                "status": "not_evaluated",
                "human_disclosure_evidence_review_required": True,
                "human_claim_evidence_review_required": True,
            },
            "warnings": [],
            "quality_gate": {
                "status": "not_run",
                "self_check_round_count": 0,
                "rounds": [],
                "final_quality_status": "not_run",
                "unresolved_issues": [],
            },
        }
        store.save(state)
        try:
            self._stage_load_case(store, state, case_dir)
            self._stage_parse_materials(store, state)
            self._stage_analyze_points(store, state, use_fake)
            self._stage_interrupt(store, state)
            return run_id, WAITING_FOR_INPUT
        except Exception as exc:
            store.fail(state, exc)
            raise

    def revalidate(
        self,
        parent_run_id: str,
        *,
        disclosure_sections_file: Path | None = None,
        claim_plan_file: Path | None = None,
        claims_file: Path | None = None,
    ) -> tuple[str, int]:
        parent_store = RunStore(self.config, parent_run_id)
        parent = parent_store.load()
        if parent.get("status") not in {
            "completed",
            "completed_with_warnings",
            "demo_completed_with_fixture",
        }:
            raise ParseError(
                "revalidate requires a completed parent Run; "
                "use resume for an interrupted or failed Run"
            )
        if parent.get("disclosure_generation_mode") != "structured_sections":
            raise ParseError("legacy Markdown Runs cannot create a revalidation revision")
        snapshot_dir = parent_store.path / str(
            parent.get("input_snapshot", {}).get("path") or "inputs"
        )
        expected_snapshot_hash = parent.get("input_snapshot", {}).get("sha256")
        if (
            not snapshot_dir.is_dir()
            or not expected_snapshot_hash
            or hash_case(snapshot_dir) != expected_snapshot_hash
        ):
            raise InputChangedError(
                "parent Run input snapshot is missing or changed; "
                "revalidation evidence cannot be reused"
            )

        required_parent_files = (
            "case.json",
            "patent_points.json",
            "prior_art/queries.json",
            "prior_art/query_results.json",
            "prior_art/prior_art.json",
            "artifacts/difference_analysis.json",
            "artifacts/disclosure_preview.json",
            "artifacts/disclosure_sections.json",
            "artifacts/disclosure_sections_initial_response.json",
            "artifacts/claim_plan.json",
            "artifacts/claims.json",
        )
        missing = [
            relative
            for relative in required_parent_files
            if not (parent_store.path / relative).is_file()
        ]
        if missing:
            raise ParseError(
                "parent Run is missing revalidation evidence: "
                + ", ".join(missing)
            )
        parent_prior = read_json(
            parent_store.path / "prior_art" / "prior_art.json"
        )
        parent_queries = read_json(
            parent_store.path / "prior_art" / "queries.json"
        )
        parent_query_results = read_json(
            parent_store.path / "prior_art" / "query_results.json"
        )
        if (
            parent_prior.get("queries") != parent_queries.get("queries")
            or parent_prior.get("query_results")
            != parent_query_results.get("query_results")
            or parent_prior.get("search_mode") != parent.get("search_mode")
            or parent_prior.get("search_status")
            != parent.get("search_status")
        ):
            raise InputChangedError(
                "parent CNIPA evidence files are inconsistent; "
                "revalidation evidence cannot be reused"
            )
        parent_manifest_path = (
            parent_store.path / "artifacts" / "manifest.json"
        )
        if not parent_manifest_path.is_file():
            raise ParseError(
                "parent Run has no artifact manifest; "
                "revalidation evidence cannot be reused"
            )
        parent_manifest = read_json(parent_manifest_path)
        for manifest_key, relative in (
            ("difference_analysis", "artifacts/difference_analysis.json"),
            ("disclosure_preview", "artifacts/disclosure_preview.json"),
        ):
            expected = (parent_manifest.get(manifest_key) or {}).get(
                "sha256"
            )
            if (
                not expected
                or sha256_file(parent_store.path / relative) != expected
            ):
                raise InputChangedError(
                    f"parent artifact does not match manifest: {relative}"
                )

        def selected_json(
            supplied: Path | None,
            parent_relative: str,
        ) -> Any:
            if supplied is None:
                return read_json(parent_store.path / parent_relative)
            selected = supplied.expanduser().resolve()
            if not selected.is_file():
                raise ParseError(f"rework artifact not found: {selected}")
            try:
                return read_json(selected)
            except (OSError, json.JSONDecodeError) as exc:
                raise ParseError(
                    f"rework artifact is not readable JSON: {selected.name}"
                ) from exc

        difference = read_json(
            parent_store.path / "artifacts" / "difference_analysis.json"
        )
        try:
            disclosure_sections = DisclosureSections.model_validate(
                selected_json(
                    disclosure_sections_file,
                    "artifacts/disclosure_sections.json",
                )
            )
        except Exception as exc:
            if isinstance(exc, ParseError):
                raise
            raise ParseError(
                "rework disclosure sections do not satisfy the structured contract"
            ) from exc
        claim_plan = normalize_claim_plan(
            selected_json(claim_plan_file, "artifacts/claim_plan.json"),
            search_mode=str(parent.get("search_mode") or ""),
            difference_analysis=difference,
        )
        search_status = str(
            difference.get("analysis_scope", {}).get("search_status")
            or parent.get("search_status")
            or ""
        )
        claims = normalize_claims(
            selected_json(claims_file, "artifacts/claims.json"),
            search_mode=str(parent.get("search_mode") or ""),
            search_status=search_status or None,
            search_limitation=(
                PARTIAL_SEARCH_LIMITATION
                if search_status in {"partial_with_records", "partial_no_records"}
                else None
            ),
        )

        new_run_id = f"{local_timestamp()}-{str(expected_snapshot_hash)[:8]}-rework"
        suffix = 1
        while (self.config.runs_dir / new_run_id).exists():
            suffix += 1
            new_run_id = (
                f"{local_timestamp()}-{str(expected_snapshot_hash)[:8]}"
                f"-rework-{suffix}"
            )
        store = RunStore(self.config, new_run_id)
        for relative in (
            "inputs",
            "parsed",
            "interrupts",
            "prior_art",
            "artifacts",
            "logs",
        ):
            (store.path / relative).mkdir(parents=True, exist_ok=True)
        for relative in ("inputs", "parsed", "interrupts", "prior_art"):
            shutil.copytree(
                parent_store.path / relative,
                store.path / relative,
                dirs_exist_ok=True,
            )
        for relative in (
            "case.json",
            "patent_points.json",
            "patent_points_initial_response.json",
        ):
            source = parent_store.path / relative
            if source.is_file():
                shutil.copy2(source, store.path / relative)
        for relative in (
            "artifacts/difference_analysis.json",
            "artifacts/disclosure_preview.json",
            "artifacts/disclosure_sections_initial_response.json",
        ):
            shutil.copy2(parent_store.path / relative, store.path / relative)

        write_json(
            store.path / "artifacts" / "disclosure_sections.json",
            disclosure_sections.model_dump(),
        )
        write_json(store.path / "artifacts" / "claim_plan.json", claim_plan)
        write_json(store.path / "artifacts" / "claims.json", claims)
        (store.path / "artifacts" / "claims.md").write_text(
            render_claims_markdown(claims, claim_plan["title"]),
            encoding="utf-8",
        )
        rework_artifacts = {}
        for name, supplied in (
            ("disclosure_sections", disclosure_sections_file),
            ("claim_plan", claim_plan_file),
            ("claims", claims_file),
        ):
            relative = f"artifacts/{name}.json"
            parent_sha256 = sha256_file(parent_store.path / relative)
            revision_sha256 = sha256_file(store.path / relative)
            rework_artifacts[name] = {
                "source": "supplied_revision" if supplied else "parent_run",
                "parent_sha256": parent_sha256,
                "revision_sha256": revision_sha256,
                "changed": parent_sha256 != revision_sha256,
            }

        reused_paths = (
            "prior_art/queries.json",
            "prior_art/query_results.json",
            "prior_art/prior_art.json",
            "artifacts/difference_analysis.json",
            "artifacts/disclosure_preview.json",
        )
        reuse_hashes = {
            relative: sha256_file(parent_store.path / relative)
            for relative in reused_paths
        }
        for relative, expected in reuse_hashes.items():
            if sha256_file(store.path / relative) != expected:
                raise InputChangedError(
                    f"reused evidence hash mismatch after copy: {relative}"
                )

        state: dict[str, Any] = {
            "schema_version": "run_state_v1",
            "run_id": new_run_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "status": "running",
            "current_stage": "reuse_parent_run_evidence",
            "completed_stages": [],
            "stage_timings": [],
            "pending_action": None,
            "source_case": parent.get("source_case"),
            "parent_run_id": parent_run_id,
            "input_sha256": expected_snapshot_hash,
            "input_snapshot": {
                "path": "inputs",
                "sha256": expected_snapshot_hash,
            },
            "provider_mode": "rework_no_model_call",
            "source_provider_mode": parent.get("provider_mode"),
            "workflow_mode": parent.get("workflow_mode", "strict"),
            "best_effort": parent.get(
                "best_effort",
                {"used": False, "events": []},
            ),
            "search_mode": parent.get("search_mode"),
            "search_status": parent.get("search_status"),
            "allow_fixture_fallback": False,
            "external_checks": {
                "qwen": "not_called_rework",
                "cnipa": "reused_from_parent",
            },
            "external_evidence_reuse": {
                "schema_version": "external_evidence_reuse_v1",
                "parent_run_id": parent_run_id,
                "input_sha256": expected_snapshot_hash,
                "search_mode": parent.get("search_mode"),
                "search_status": parent.get("search_status"),
                "source_external_checks": parent.get("external_checks"),
                "reused_artifact_sha256": reuse_hashes,
                "reused_at": utc_now(),
                "new_external_calls": {
                    "qwen": 0,
                    "cnipa": 0,
                },
            },
            "rework_artifacts": rework_artifacts,
            "error": None,
            "artifacts": {},
            "difference_analysis": parent.get("difference_analysis"),
            "disclosure_generation_mode": "structured_sections",
            "section_recovery": {
                "attempted": False,
                "requested_fields": [],
                "recovered_fields": [],
                "status": "not_needed_rework",
            },
            "disclosure_sections": {
                "path": "artifacts/disclosure_sections.json",
                "schema_version": "disclosure_sections_v1",
                "required_fields_complete": True,
                "sha256": sha256_file(
                    store.path / "artifacts" / "disclosure_sections.json"
                ),
            },
            "disclosure_evidence_review": None,
            "claim_plan": {
                "path": "artifacts/claim_plan.json",
                "schema_version": "claim_plan_v1",
                "recommended_claim_types": claim_plan[
                    "recommended_claim_types"
                ],
                "independent_claim_count": len(
                    claim_plan["independent_claims"]
                ),
            },
            "claims": {
                "json_path": "artifacts/claims.json",
                "markdown_path": "artifacts/claims.md",
                "schema_version": "claims_v1",
                "claim_count": len(claims["claims"]),
                "claim_types": list(
                    dict.fromkeys(
                        row["claim_type"] for row in claims["claims"]
                    )
                ),
            },
            "claim_validation": None,
            "claim_evidence_review": None,
            "release_readiness": {
                "status": "not_evaluated",
                "human_disclosure_evidence_review_required": True,
                "human_claim_evidence_review_required": True,
            },
            "warnings": list(
                dict.fromkeys(
                    [
                        *(parent.get("warnings") or []),
                        (
                            "本修订 Run 未调用 Qwen 或 CNIPA；外部证据严格复用自"
                            f"父 Run {parent_run_id}。"
                        ),
                    ]
                )
            ),
            "quality_gate": {
                "status": "not_run",
                "self_check_round_count": 0,
                "rounds": [],
                "final_quality_status": "not_run",
                "unresolved_issues": [],
            },
            "case_title": parent.get("case_title"),
            "parse_summary": parent.get("parse_summary"),
            "patent_point_grounding": parent.get("patent_point_grounding"),
            "selected_patent_point_id": parent.get(
                "selected_patent_point_id"
            ),
        }
        store.save(state)
        try:
            store.begin_stage(state, "reuse_parent_run_evidence")
            store.complete_stage(state, "reuse_parent_run_evidence")
            store.begin_stage(state, "load_rework_artifacts")
            store.complete_stage(state, "load_rework_artifacts")
            self._stage_render_disclosure(store, state)
            self._stage_basic_disclosure_check(store, state)
            self._stage_disclosure_evidence_review(store, state)
            self._stage_validate_claims(store, state)
            self._stage_claim_evidence_review(store, state)
            self._stage_export(store, state)
            quality_passed = (
                state.get("quality_gate", {}).get("final_quality_status")
                == "passed"
                and state.get("claim_validation", {}).get("passed") is True
            )
            if not quality_passed:
                state["status"] = "failed"
                state["current_stage"] = "failed"
            elif state.get("search_mode", "").startswith(
                ("fixture_", "fake")
            ):
                state["status"] = "demo_completed_with_fixture"
                state["current_stage"] = "demo_completed_with_fixture"
            else:
                state["status"] = "completed_with_warnings"
                state["current_stage"] = "completed_with_warnings"
            state["pending_action"] = None
            store.save(state)
            self._write_phase_result(store, state)
            return new_run_id, 0 if quality_passed else QUALITY_GATE_FAILED
        except Exception as exc:
            store.fail(state, exc)
            self._write_phase_result(store, state)
            raise

    def resume(
        self,
        run_id: str,
        response_file: Path | None = None,
        *,
        allow_fixture_fallback: bool = False,
    ) -> int:
        store = RunStore(self.config, run_id)
        state = store.load()
        if state.get("status") in {"completed", "completed_with_warnings", "demo_completed_with_fixture"}:
            return 0
        if state.get("disclosure_generation_mode") != "structured_sections":
            raise ParseError(
                "legacy Markdown Runs are read-only; use status to inspect existing artifacts and errors"
            )
        # Compatibility with rework runs created before scope consolidation.
        if state.get("status") == "quality_gate_failed":
            return QUALITY_GATE_FAILED
        snapshot_dir = store.path / str(state.get("input_snapshot", {}).get("path") or "inputs")
        expected_snapshot_hash = state.get("input_snapshot", {}).get("sha256")
        if not snapshot_dir.is_dir() or not expected_snapshot_hash or hash_case(snapshot_dir) != expected_snapshot_hash:
            exc = InputChangedError("run input snapshot is missing or changed; start a new run")
            store.fail(state, exc)
            raise exc
        response: Any = None
        if "patent_point_selection" not in state["completed_stages"]:
            if response_file is None:
                raise ParseError(
                    "selection response is required until patent point selection is completed"
                )
            response_file = response_file.expanduser().resolve()
            if not response_file.is_file():
                raise ParseError(f"selection response not found: {response_file}")
            response = read_json(response_file)
        fake = state.get("provider_mode") == "fake"
        if allow_fixture_fallback:
            state["allow_fixture_fallback"] = True
            store.save(state)
        try:
            if "patent_point_selection" not in state["completed_stages"]:
                self._accept_selection(store, state, response)
            if "plan_prior_art_queries" not in state["completed_stages"]:
                self._stage_plan_queries(store, state, fake)
            if "search_prior_art" not in state["completed_stages"]:
                self._stage_search(store, state, fake)
            if "analyze_prior_art_differences" not in state["completed_stages"]:
                self._stage_difference_analysis(store, state, fake)
            if "generate_disclosure_preview" not in state["completed_stages"]:
                self._stage_preview(store, state, fake)
            if "validate_disclosure_sections" not in state["completed_stages"]:
                self._stage_disclosure_sections(store, state, fake)
            if "render_disclosure_markdown" not in state["completed_stages"]:
                self._stage_render_disclosure(store, state)
            if "basic_disclosure_content_check" not in state["completed_stages"]:
                self._stage_basic_disclosure_check(store, state)
            if "build_disclosure_evidence_review" not in state[
                "completed_stages"
            ]:
                self._stage_disclosure_evidence_review(store, state)
            if "build_claim_plan" not in state["completed_stages"]:
                self._stage_claim_plan(store, state, fake)
            if "draft_claims" not in state["completed_stages"]:
                self._stage_claims(store, state, fake)
            if "validate_claims" not in state["completed_stages"]:
                self._stage_validate_claims(store, state)
            if "build_claim_evidence_review" not in state["completed_stages"]:
                self._stage_claim_evidence_review(store, state)
            if "export_results" not in state["completed_stages"]:
                self._stage_export(store, state)
            quality_passed = (
                state.get("quality_gate", {}).get("final_quality_status") == "passed"
                and state.get("claim_validation", {}).get("passed") is True
            )
            warnings = list(state.get("warnings") or []) + list(state.get("artifacts", {}).get("warnings") or [])
            state["warnings"] = warnings
            if not quality_passed:
                state["status"] = "failed"
                state["current_stage"] = "failed"
            elif state.get("search_mode", "").startswith(("fixture_", "fake")):
                state["status"] = "demo_completed_with_fixture"
                state["current_stage"] = "demo_completed_with_fixture"
            elif warnings:
                state["status"] = "completed_with_warnings"
                state["current_stage"] = "completed_with_warnings"
            else:
                state["status"] = "completed"
                state["current_stage"] = "completed"
            state["pending_action"] = None
            store.save(state)
            self._write_phase_result(store, state)
            return 0 if quality_passed else QUALITY_GATE_FAILED
        except Exception as exc:
            store.fail(state, exc)
            self._write_phase_result(store, state)
            raise

    def _stage_load_case(self, store: RunStore, state: dict[str, Any], case_dir: Path) -> None:
        stage = "load_case"
        store.begin_stage(state, stage)
        for source in list_case_files(case_dir):
            rel = source.relative_to(case_dir)
            target = store.path / "inputs" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        meta = {"title": case_dir.name, "language": "zh-CN", "contact": {"name": "待填写", "phone": "待填写", "email": "待填写"}}
        for candidate in (case_dir / "case.yaml", case_dir / "case.yml"):
            if candidate.is_file():
                loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    meta.update(loaded)
                break
        write_json(store.path / "case.json", meta)
        state["case_title"] = str(meta.get("title") or case_dir.name)
        state["input_snapshot"] = {"path": "inputs", "sha256": hash_case(store.path / "inputs")}
        store.complete_stage(state, stage)

    def _stage_parse_materials(self, store: RunStore, state: dict[str, Any]) -> None:
        stage = "parse_materials"
        store.begin_stage(state, stage)
        parser = DocumentParser(self.vendor_root)
        chunks: list[str] = []
        manifest: list[dict[str, Any]] = []
        for source in list_case_files(store.path / "inputs"):
            if source.name in {"case.yaml", "case.yml"}:
                continue
            rel = source.relative_to(store.path / "inputs").as_posix()
            output = store.path / "parsed" / "files" / f"{rel}.md"
            row: dict[str, Any] = {
                "source_path": rel,
                "file_type": source.suffix.lower(),
                "parse_status": "failed",
                "effective_character_count": 0,
                "output_path": None,
                "warning": None,
                "error": None,
                "sha256": sha256_file(source),
            }
            try:
                text = parser.parse(source, store.path / "parsed")
                effective_count = len("".join(text.split()))
                row["effective_character_count"] = effective_count
                if effective_count == 0:
                    row["parse_status"] = "empty"
                    row["warning"] = "parsed material contains only whitespace"
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(text, encoding="utf-8")
                    row["parse_status"] = "parsed"
                    row["output_path"] = output.relative_to(store.path).as_posix()
                    chunks.append(f"\n\n## Source: {rel}\n\n{text.strip()}\n")
            except ParseError as exc:
                row["parse_status"] = "unsupported" if "unsupported input format" in str(exc) else "failed"
                row["error"] = str(exc)
            manifest.append(row)
        total_effective = sum(int(row["effective_character_count"]) for row in manifest if row["parse_status"] == "parsed")
        write_json(store.path / "parsed" / "manifest.json", manifest)
        state["parse_summary"] = {
            "minimum_effective_characters": self.config.min_effective_characters,
            "total_effective_character_count": total_effective,
            "parsed_files": sum(row["parse_status"] == "parsed" for row in manifest),
            "empty_files": sum(row["parse_status"] == "empty" for row in manifest),
            "failed_files": sum(row["parse_status"] in {"failed", "unsupported"} for row in manifest),
        }
        if total_effective < self.config.min_effective_characters:
            raise ParseError(
                f"effective parsed material is below minimum threshold "
                f"({total_effective} < {self.config.min_effective_characters}); model was not called"
            )
        bundle = "# Parsed case materials\n" + "".join(chunks)
        (store.path / "parsed" / "materials.md").write_text(bundle, encoding="utf-8")
        store.complete_stage(state, stage)

    def _stage_analyze_points(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "analyze_patent_points"
        store.begin_stage(state, stage)
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        user = f"""TASK:CANDIDATE_POINTS
Using the supplied Chinese Skill instructions, return one JSON object with key patent_points.
patent_points must contain 3 to 5 objects with keys: id, title, technical_background, innovation, difference, feasibility.
CASE MATERIALS is the only factual source. Do not invent benchmarks, prototype validation, deployments,
mature adoption, prior-art absence, novelty, formulas, thresholds, or numeric values that do not appear verbatim
in CASE MATERIALS. A feasibility inference must begin with "技术推演：" and must not claim that it was tested.
The difference field is only a candidate distinction and must say that real patent-search verification is pending.

CASE MATERIALS:
{materials[:120000]}
"""
        model_name = "best-effort-deterministic"
        initial_response: dict[str, Any] = {}
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._prompt("patent_points_analyzer.md"),
                user_prompt=user,
            )
            initial_response = dict(result.data)
            points = normalize_patent_points(
                result.data.get("patent_points"),
                minimum=1 if self._flow_first(state) else 3,
            )
            points, grounding = ground_patent_points(
                points,
                source_materials=materials,
            )
            model_name = result.model
        except Exception as exc:
            if not self._flow_first(state):
                raise
            points = normalize_patent_points(
                [
                    {
                        "id": "PP-001",
                        "title": "基于案件材料的候选专利技术方案",
                        "technical_background": "案件材料描述了待解决的技术背景与实现约束。",
                        "innovation": "依据案件材料形成技术处理流程和实现机制。",
                        "difference": "best-effort 未完成差异判断，待人工核对。",
                        "feasibility": "可依据案件材料进一步细化实现。",
                    }
                ],
                minimum=1,
            )
            points, grounding = ground_patent_points(
                points,
                source_materials=materials,
            )
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="生成一个可人工选择的最小候选专利点",
            )
        write_json(
            store.path / "patent_points_initial_response.json",
            initial_response,
        )
        payload = {
            "schema_version": "patent_points_v1",
            "generated_at": utc_now(),
            "model": model_name,
            "patent_points": points,
            "grounding": grounding,
        }
        write_json(store.path / "patent_points.json", payload)
        state["patent_point_grounding"] = grounding
        if not fake:
            state["external_checks"]["qwen"] = (
                "best_effort_after_failure"
                if model_name == "best-effort-deterministic"
                else "passed"
            )
        store.complete_stage(state, stage)

    def _stage_interrupt(self, store: RunStore, state: dict[str, Any]) -> None:
        stage = "wait_for_patent_point_selection"
        store.begin_stage(state, stage)
        points = read_json(store.path / "patent_points.json")["patent_points"]
        request = {
            "interrupt_type": "PATENT_POINT_SELECTION",
            "run_id": store.run_id,
            "instructions": "Select one patent point by id and save the response JSON.",
            "candidates": [{"id": p["id"], "title": p["title"]} for p in points],
            "response_schema": {"selected_ids": ["PP-001"], "notes": "optional"},
        }
        write_json(store.path / "interrupts" / "patent_point_selection_request.json", request)
        state["status"] = "waiting_for_patent_point_selection"
        state["pending_action"] = "PATENT_POINT_SELECTION"
        store.complete_stage(state, stage)
        store.save(state)

    def _accept_selection(self, store: RunStore, state: dict[str, Any], response: Any) -> None:
        if state.get("pending_action") != "PATENT_POINT_SELECTION":
            if "patent_point_selection" in state.get("completed_stages", []):
                return
            raise ParseError("run is not waiting for PATENT_POINT_SELECTION")
        store.begin_stage(state, "patent_point_selection")
        if not isinstance(response, dict) or not isinstance(response.get("selected_ids"), list) or len(response["selected_ids"]) != 1:
            raise ParseError("selection response must contain exactly one selected_ids entry")
        points = read_json(store.path / "patent_points.json")["patent_points"]
        valid = {p["id"] for p in points}
        selected = response["selected_ids"][0]
        if selected not in valid:
            raise ParseError(f"unknown patent point id: {selected}")
        write_json(store.path / "interrupts" / "patent_point_selection_response.json", response)
        state["selected_patent_point_id"] = selected
        state["pending_action"] = None
        state["status"] = "running"
        store.complete_stage(state, "patent_point_selection")

    def _selected_point(self, store: RunStore, state: dict[str, Any]) -> dict[str, Any]:
        points = read_json(store.path / "patent_points.json")["patent_points"]
        return next(p for p in points if p["id"] == state["selected_patent_point_id"])

    def _stage_plan_queries(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "plan_prior_art_queries"
        store.begin_stage(state, stage)
        selected = self._selected_point(store, state)
        user = f"""TASK:PRIOR_ART_QUERIES
Return JSON {{"queries": [...]}} containing 3 to 7 concise Chinese technical semantic blocks.
Each query must be an abstract phrase suitable for a patent search. Do not include confidential full materials, patent numbers, URLs, generic words such as 系统/方法 alone, or explanations.
Preserve the literal technical nouns and operations used by the selected point. Cover each distinct
technical object plus core operation with one focused phrase, such as "缓存动态管理" rather than
replacing every source term with a plausible synonym. Include at least one broader core technical
noun phrase of 2 to 6 Chinese characters; use the remaining queries for focused core phrases and
narrower feature combinations. Do not spend the query budget on near-duplicate synonyms.
SELECTED POINT:
{json.dumps(selected, ensure_ascii=False)}
"""
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._prompt("prior_art_search.md"),
                user_prompt=user,
                temperature=0,
            )
            queries = result.data.get("queries")
            if not isinstance(queries, list):
                raise ParseError("prior-art query contract missing queries list")
            queries = [" ".join(str(q).split()).strip() for q in queries]
            queries = list(dict.fromkeys(q for q in queries if q))
            minimum = 1 if self._flow_first(state) else 2
            if not minimum <= len(queries) <= min(8, self.config.cnipa.max_queries):
                raise ParseError(
                    f"prior-art query contract requires {minimum} to 8 unique queries"
                )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            queries = [
                _compact_text(
                    selected.get("title") or selected.get("innovation"),
                    "案件材料技术方案",
                )[:80]
            ]
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="使用选定专利点生成一个最小检索词",
            )
        broadening_query = None
        if not fake and len(queries) >= 4:
            broadening_query = derive_broader_cnipa_query(queries)
            if broadening_query and broadening_query not in queries:
                if len(queries) >= self.config.cnipa.max_queries:
                    queries[-1] = broadening_query
                else:
                    queries.append(broadening_query)
        write_json(
            store.path / "prior_art" / "queries.json",
            {
                "queries": queries,
                "broadening_query": broadening_query,
            },
        )
        store.complete_stage(state, stage)

    def _stage_search(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "search_prior_art"
        store.begin_stage(state, stage)
        queries = read_json(store.path / "prior_art" / "queries.json")["queries"]
        query_specs = [
            {"query_id": f"Q-{index:03d}", "query": str(query)}
            for index, query in enumerate(queries, 1)
        ]
        query_results_path = store.path / "prior_art" / "query_results.json"
        query_results = self._load_query_results(query_results_path, query_specs)
        completed = {row["query_id"]: row for row in query_results}
        fallback_reason: str | None = None
        adapter = (
            FixtureCnipaAdapter(self.config.cnipa.fixture)
            if fake
            else CnipaAdapter(self.config.cnipa)
        )
        for spec in query_specs:
            if spec["query_id"] in completed:
                continue
            result = adapter.search(spec["query"])
            row = self._persisted_query_result(spec, result)
            completed[spec["query_id"]] = row
            query_results = [completed[item["query_id"]] for item in query_specs if item["query_id"] in completed]
            self._write_query_results(
                query_results_path,
                query_specs,
                query_results,
                aggregate_status=(
                    aggregate_search_status(query_results)
                    if len(query_results) == len(query_specs)
                    else "in_progress"
                ),
            )
            # A CNIPA WAF/network failure is shared by the remaining queries.
            # Stop retrying the same unavailable dependency and preserve an
            # explicit result row for each skipped query so the final evidence
            # remains auditable and flow_first can continue deterministically.
            if not fake and result.status in SEARCH_FAILURE_STATUSES:
                for pending in query_specs:
                    if pending["query_id"] in completed:
                        continue
                    skipped = SearchResult(
                        query=pending["query"],
                        status=result.status,
                        result_count=0,
                        records=[],
                        error_type="CircuitBreaker",
                        error_message=(
                            "未重复请求：前一条 CNIPA 检索已出现外部依赖失败"
                        ),
                        elapsed_seconds=0.0,
                    )
                    completed[pending["query_id"]] = self._persisted_query_result(
                        pending, skipped
                    )
                query_results = [
                    completed[item["query_id"]]
                    for item in query_specs
                    if item["query_id"] in completed
                ]
                self._write_query_results(
                    query_results_path,
                    query_specs,
                    query_results,
                    aggregate_status=aggregate_search_status(query_results),
                )
                break

        query_results = [completed[spec["query_id"]] for spec in query_specs]
        search_status = aggregate_search_status(query_results)
        state["search_status"] = search_status
        self._write_query_results(
            query_results_path,
            query_specs,
            query_results,
            aggregate_status=search_status,
        )
        records = dedupe_records(
            [
                normalize_hit(record)
                for row in query_results
                for record in row.get("records", [])
                if isinstance(record, dict)
            ]
        )

        if fake:
            state["search_mode"] = "fake"
            state["external_checks"]["cnipa"] = "fixture_not_real"
        else:
            state["search_mode"] = "real_cnipa"
            if search_status == "complete_with_records":
                state["external_checks"]["cnipa"] = "passed"
            elif search_status == "complete_zero_results":
                state["external_checks"]["cnipa"] = "passed"
            elif search_status in {"partial_with_records", "partial_no_records"}:
                state["external_checks"]["cnipa"] = "partial"
            else:
                state["external_checks"]["cnipa"] = "failed"

            if not records and state.get("allow_fixture_fallback"):
                statuses = {row["status"] for row in query_results}
                if statuses == {"zero_results"}:
                    fallback_reason = "zero_results"
                elif statuses & {"transport_error", "timeout", "waf_or_verification_error"}:
                    fallback_reason = "transport_error"
                else:
                    fallback_reason = "tool_error"
                fixture = FixtureCnipaAdapter(self.config.cnipa.fixture)
                records = dedupe_records(
                    [
                        record
                        for spec in query_specs
                        for record in fixture.search(spec["query"]).records
                    ]
                )
                state["search_mode"] = f"fixture_after_{fallback_reason}"
                state["external_checks"]["cnipa"] = "failed_real_fixture_used"
            elif search_status in {"complete_zero_results", "partial_with_records", "partial_no_records"}:
                warning = search_limitation_for(search_status)
                state["warnings"] = list(
                    dict.fromkeys([*(state.get("warnings") or []), warning])
                )

        records = dedupe_records(records)
        limitation = search_limitation_for(search_status)
        payload = {
            "schema_version": "prior_art_v3",
            "search_mode": state["search_mode"],
            "search_status": search_status,
            "queries": queries,
            "query_results": query_results,
            "query_results_path": "prior_art/query_results.json",
            "fixture_fallback_authorized": bool(state.get("allow_fixture_fallback")),
            "fixture_fallback_reason": fallback_reason,
            "search_limitations": [limitation],
            "records": [r.to_dict() for r in records],
        }
        write_json(store.path / "prior_art" / "prior_art.json", payload)
        store.save(state)
        if (
            search_status == "failed"
            and state["search_mode"] == "real_cnipa"
            and not self._flow_first(state)
        ):
            statuses = ", ".join(sorted({row["status"] for row in query_results}))
            raise SearchError(
                f"CNIPA queries all failed without a normal completion; query statuses: {statuses or 'not_run'}"
            )
        if search_status == "failed" and state["search_mode"] == "real_cnipa":
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason="all_cnipa_queries_failed",
                fallback="保留真实失败证据并以无可比较记录继续，不使用 Fixture",
            )
        store.complete_stage(state, stage)

    def _persisted_query_result(
        self,
        spec: dict[str, str],
        result: SearchResult,
    ) -> dict[str, Any]:
        row = result.to_dict()
        message = str(row.get("error_message") or "")
        if message:
            message = redact_text(message, [self.config.qwen.api_key, spec["query"]])
            message = re.sub(
                r"(?i)\b(api[-_ ]?key|authorization|bearer)\b\s*[:= ]\s*\S+",
                r"\1=[REDACTED]",
                message,
            )
            message = " ".join(message.split())[:500]
        return {
            "query_id": spec["query_id"],
            "query": spec["query"],
            "status": row["status"],
            "result_count": int(row.get("result_count") or 0),
            "records": list(row.get("records") or []),
            "elapsed_seconds": float(row.get("elapsed_seconds") or 0),
            "error_type": row.get("error_type"),
            "error_message": message or None,
            "completed_at": utc_now(),
        }

    @staticmethod
    def _load_query_results(
        path: Path,
        query_specs: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        raw = read_json(path)
        rows = raw.get("query_results", []) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return []
        expected = {spec["query_id"]: spec["query"] for spec in query_specs}
        completed: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            query_id = str(row.get("query_id") or "")
            if (
                query_id in expected
                and row.get("query") == expected[query_id]
                and row.get("status") in SEARCH_STATUSES
            ):
                completed.setdefault(query_id, row)
        return [
            completed[spec["query_id"]]
            for spec in query_specs
            if spec["query_id"] in completed
        ]

    @staticmethod
    def _write_query_results(
        path: Path,
        query_specs: list[dict[str, str]],
        query_results: list[dict[str, Any]],
        *,
        aggregate_status: str,
    ) -> None:
        write_json(
            path,
            {
                "schema_version": "prior_art_query_results_v1",
                "query_count": len(query_specs),
                "completed_query_count": len(query_results),
                "aggregate_status": aggregate_status,
                "query_results": query_results,
            },
        )

    def _stage_difference_analysis(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "analyze_prior_art_differences"
        store.begin_stage(state, stage)
        selected = self._selected_point(store, state)
        all_points = read_json(store.path / "patent_points.json")["patent_points"]
        auxiliary_points = [point for point in all_points if point["id"] != selected["id"]]
        prior = read_json(store.path / "prior_art" / "prior_art.json")
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        user = f"""TASK:DIFFERENCE_ANALYSIS
Return a JSON object with analysis_scope, target_features, comparisons,
candidate_distinguishing_features, and limitations.
Use only publication_number values present in PRIOR ART RECORDS.
The target_features must have feature_id, feature_text, source_summary.
Each comparison must have feature_id, publication_number, prior_art_title,
disclosure_status, analysis, recommended_claim_role.
Each candidate_distinguishing_features item must have feature_id, reason,
recommended_claim_role.
Search mode is {prior['search_mode']}; aggregate search status is {prior.get('search_status')}.
Search limitation: {prior_search_limitation(prior)}
If search mode is not real_cnipa, state exactly:
"该分析仅用于演示 Agent 流程，不构成真实现有技术检索结论。"
zero_results only means that one query returned no hits and never supports an absence-of-prior-art conclusion.
If aggregate search status is partial_with_records or partial_no_records, preserve the search limitation and do not
present the available results as a complete novelty or inventiveness search.
If PRIOR ART RECORDS is empty, extract grounded target_features but return empty comparisons and
candidate_distinguishing_features. These features may be used only as claim-drafting candidates from this solution.
Do not provide novelty, inventiveness, grant, infringement, or other legal conclusions.
SELECTED MAIN PATENT POINT:
{json.dumps(selected, ensure_ascii=False)}
AUXILIARY CANDIDATE POINTS (context only, not separately selected):
{json.dumps(auxiliary_points, ensure_ascii=False)}
PRIOR ART RECORDS:
{json.dumps(prior, ensure_ascii=False)[:100000]}
CASE MATERIALS:
{materials[:120000]}
"""
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._project_prompt("difference_analysis.md"),
                user_prompt=user,
                temperature=0,
            )
            analysis = normalize_difference_analysis(
                result.data,
                selected_patent_point_id=state["selected_patent_point_id"],
                search_mode=str(state["search_mode"]),
                prior_art_records=prior["records"],
                search_status=str(
                    prior.get("search_status") or state.get("search_status") or ""
                ),
            )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            analysis = _best_effort_difference_analysis(
                selected=selected,
                search_mode=str(state["search_mode"]),
                search_status=str(
                    prior.get("search_status") or state.get("search_status") or ""
                ),
                prior_art_records=list(prior.get("records") or []),
            )
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="从选定专利点生成保守差异分析，所有比较均标记待人工核对",
            )
        path = store.path / "artifacts" / "difference_analysis.json"
        write_json(path, analysis)
        state["difference_analysis"] = {
            "path": path.relative_to(store.path).as_posix(),
            "schema_version": analysis["schema_version"],
            "analysis_status": analysis["analysis_status"],
            "comparable_record_count": analysis["analysis_scope"]["comparable_record_count"],
            "target_feature_count": len(analysis["target_features"]),
            "candidate_distinguishing_feature_count": len(analysis["candidate_distinguishing_features"]),
            "claim_drafting_candidate_count": len(analysis["claim_drafting_candidates"]),
        }
        store.complete_stage(state, stage)

    def _stage_preview(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "generate_disclosure_preview"
        store.begin_stage(state, stage)
        selected = self._selected_point(store, state)
        prior = read_json(store.path / "prior_art" / "prior_art.json")
        difference = read_json(store.path / "artifacts" / "difference_analysis.json")
        user = f"""TASK:DISCLOSURE_PREVIEW
Return a JSON object with working_title, technical_problems (1-3), core_steps (3-6), closest_difference.
Do not invent patent metadata or links. Search mode is {prior['search_mode']};
aggregate search status is {prior.get('search_status')}.
Preserve this search limitation: {prior_search_limitation(prior)}
Any zero_results entry means only that the specific query returned no hits. It does not mean that prior art is absent,
that no related solution exists, or that the invention is novel.
SELECTED POINT: {json.dumps(selected, ensure_ascii=False)}
PRIOR ART: {json.dumps(prior, ensure_ascii=False)[:80000]}
DIFFERENCE ANALYSIS: {json.dumps(difference, ensure_ascii=False)[:80000]}
"""
        required = {"working_title", "technical_problems", "core_steps", "closest_difference"}
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._prompt("disclosure_preview.md"),
                user_prompt=user,
            )
            if not required.issubset(result.data):
                raise ParseError("disclosure preview contract is incomplete")
            preview = dict(result.data)
        except Exception as exc:
            if not self._flow_first(state):
                raise
            preview = _best_effort_preview(selected, difference)
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="从选定专利点和目标特征生成最小交底书预览",
            )
        preview["artifact_metadata"] = {
            "search_mode": prior.get("search_mode"),
            "search_status": prior.get("search_status"),
            "search_limitation": prior_search_limitation(prior),
        }
        write_json(store.path / "artifacts" / "disclosure_preview.json", preview)
        store.complete_stage(state, stage)

    def _stage_disclosure_sections(
        self,
        store: RunStore,
        state: dict[str, Any],
        fake: bool,
    ) -> None:
        stage = (
            "validate_disclosure_sections"
            if "generate_disclosure_sections" in state["completed_stages"]
            else "generate_disclosure_sections"
        )
        store.begin_stage(state, stage)
        selected = self._selected_point(store, state)
        preview = read_json(store.path / "artifacts" / "disclosure_preview.json")
        prior = read_json(store.path / "prior_art" / "prior_art.json")
        difference = read_json(store.path / "artifacts" / "difference_analysis.json")
        materials = (store.path / "parsed" / "materials.md").read_text(
            encoding="utf-8"
        )
        user = f"""TASK:DISCLOSURE_SECTIONS
Return only one strict JSON object with exactly these fields:
title, technical_field, background, technical_problem, technical_solution,
beneficial_effects, embodiments, drawing_description.
Every required field except drawing_description must be a non-empty string.
Do not output Markdown headings, numbering, code fences, Mermaid, explanatory text, or keys outside the contract.
Do not draft formal claims. Do not invent patent numbers, links, experiment results, thresholds, formulas, or source facts.
Do not invent illustrative embodiments, named datasets, literal field values, record counts,
performance or complexity claims, deployment claims, implementation environments, or examples.
An embodiment must restate only an implemented flow from CASE MATERIALS unless CASE MATERIALS
itself supplies the exact example and labels it as such.
Do not infer that prior art or related solutions do not exist from zero results or a partial search.
Do not make novelty, inventiveness, grant, infringement, or other legal conclusions.
Keep the technical solution and embodiments faithful to CASE MATERIALS.
Treat SELECTED POINT, PREVIEW, and DIFFERENCE ANALYSIS as derived summaries, not authority for new
technical detail. A scope-changing modifier, relationship, algorithm, behavior, data structure, or
numeric value may appear only when CASE MATERIALS directly supports it; do not strengthen
"threshold" into "dynamic threshold" or "combine" into "weighted combine" unless the source says so.
When CASE MATERIALS labels numeric values as examples, either omit them or preserve the same values
with an explicit example/non-mandatory label. Never derive new weights, durations, or thresholds.
SEARCH STATUS: {prior.get('search_status')}
SEARCH LIMITATION: {prior_search_limitation(prior)}
SELECTED POINT: {json.dumps(selected, ensure_ascii=False)}
PREVIEW: {json.dumps(preview, ensure_ascii=False)}
DIFFERENCE ANALYSIS: {json.dumps(difference, ensure_ascii=False)[:80000]}
CASE MATERIALS: {materials[:120000]}
"""
        raw_path = (
            store.path / "artifacts" / "disclosure_sections_initial_response.json"
        )
        if raw_path.is_file():
            initial_response = read_json(raw_path)
        else:
            try:
                result = self._model(fake).complete_json(
                    system_prompt=(
                        "You generate structured Chinese patent disclosure sections. "
                        "Return JSON only, follow the exact field contract, and treat "
                        "CASE MATERIALS as the only authority for technical facts."
                    ),
                    user_prompt=user,
                )
                initial_response = result.data
            except Exception as exc:
                if not self._flow_first(state):
                    raise
                initial_response = {}
                self._record_best_effort(
                    store,
                    state,
                    stage=stage,
                    reason=type(exc).__name__,
                    fallback="模型请求失败后进入最小结构化交底书补齐路径",
                )
            write_json(raw_path, initial_response)
        try:
            draft, missing_fields, input_normalizations = parse_disclosure_draft(
                initial_response
            )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            sections = _best_effort_disclosure_sections(
                initial_response=initial_response,
                selected=selected,
                preview=preview,
            )
            sections_path = store.path / "artifacts" / "disclosure_sections.json"
            write_json(sections_path, sections.model_dump())
            state["section_recovery"] = {
                "attempted": False,
                "requested_fields": [],
                "recovered_fields": [],
                "status": "best_effort_completed",
            }
            state["disclosure_sections"] = {
                "path": sections_path.relative_to(store.path).as_posix(),
                "schema_version": "disclosure_sections_v1",
                "required_fields_complete": True,
                "sha256": sha256_file(sections_path),
            }
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="保留可接受字段并从专利点和预览补齐最小结构化交底书",
            )
            store.complete_stage(state, "generate_disclosure_sections")
            store.begin_stage(state, "validate_disclosure_sections")
            store.complete_stage(state, "validate_disclosure_sections")
            return
        store.complete_stage(state, "generate_disclosure_sections")
        if stage != "validate_disclosure_sections":
            store.begin_stage(state, "validate_disclosure_sections")
        recovered_response: dict[str, Any] | None = None
        used_best_effort_recovery = False
        if missing_fields:
            store._finish_timing(
                state,
                "validate_disclosure_sections",
                "needs_recovery",
            )
            store.save(state)
            store.begin_stage(state, "recover_missing_disclosure_fields")
            recovery_path = (
                store.path
                / "artifacts"
                / "disclosure_sections_recovery_response.json"
            )
            prior_recovery = state.get("section_recovery") or {}
            if prior_recovery.get("attempted") and not recovery_path.is_file():
                raise DisclosureSectionRecoveryError(
                    "the single structured disclosure recovery was already attempted",
                    missing_sections_before=missing_fields,
                    missing_sections_after=missing_fields,
                )
            state["section_recovery"] = {
                "attempted": True,
                "requested_fields": list(missing_fields),
                "recovered_fields": [],
                "status": "failed",
                "input_normalizations": input_normalizations,
            }
            store.save(state)
            recovery_user = f"""TASK:DISCLOSURE_SECTION_RECOVERY
Return only one JSON object containing exactly these missing fields and no others.
MISSING SECTION FIELDS: {json.dumps(missing_fields, ensure_ascii=False)}
Do not return Markdown, code fences, headings, numbering, explanations, or the existing fields.
Do not invent facts, measured results, patent metadata, links, or legal conclusions.
SEARCH STATUS: {prior.get('search_status')}
SEARCH LIMITATION: {prior_search_limitation(prior)}
SELECTED POINT: {json.dumps(selected, ensure_ascii=False)}
DIFFERENCE ANALYSIS: {json.dumps(difference, ensure_ascii=False)[:80000]}
CASE MATERIALS: {materials[:120000]}
"""
            if recovery_path.is_file():
                recovered_response = read_json(recovery_path)
            else:
                try:
                    result = self._model(fake).complete_json(
                        system_prompt=self._project_prompt(
                            "disclosure_section_recovery.md"
                        ),
                        user_prompt=recovery_user,
                        temperature=0,
                    )
                except Exception as exc:
                    if not self._flow_first(state):
                        raise DisclosureSectionRecoveryError(
                            "structured disclosure recovery model request failed",
                            missing_sections_before=missing_fields,
                            missing_sections_after=missing_fields,
                        ) from exc
                    recovered_response = None
                else:
                    recovered_response = result.data
                if recovered_response is not None:
                    write_json(recovery_path, recovered_response)

        try:
            sections = merge_and_validate_disclosure_sections(
                draft,
                recovered_response,
            )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            sections = _best_effort_disclosure_sections(
                initial_response={
                    **draft.model_dump(exclude_none=True),
                    **(recovered_response if isinstance(recovered_response, dict) else {}),
                },
                selected=selected,
                preview=preview,
            )
            used_best_effort_recovery = True
            self._record_best_effort(
                store,
                state,
                stage="recover_missing_disclosure_fields",
                reason=type(exc).__name__,
                fallback="保留有效章节并确定性补齐最小交底书字段",
            )
        sections_path = store.path / "artifacts" / "disclosure_sections.json"
        write_json(sections_path, sections.model_dump())
        if missing_fields:
            state["section_recovery"].update(
                {
                    "recovered_fields": list(missing_fields),
                    "status": (
                        "best_effort_completed"
                        if used_best_effort_recovery
                        else "passed"
                    ),
                }
            )
            store.complete_stage(state, "recover_missing_disclosure_fields")
            store.begin_stage(state, "validate_disclosure_sections")
        else:
            state["section_recovery"] = {
                "attempted": False,
                "requested_fields": [],
                "recovered_fields": [],
                "status": "not_needed",
                "input_normalizations": input_normalizations,
            }
        state["disclosure_sections"] = {
            "path": sections_path.relative_to(store.path).as_posix(),
            "schema_version": "disclosure_sections_v1",
            "required_fields_complete": True,
            "sha256": sha256_file(sections_path),
        }
        store.complete_stage(state, "validate_disclosure_sections")

    def _stage_render_disclosure(
        self,
        store: RunStore,
        state: dict[str, Any],
    ) -> None:
        stage = "render_disclosure_markdown"
        store.begin_stage(state, stage)
        sections = DisclosureSections.model_validate(
            read_json(store.path / "artifacts" / "disclosure_sections.json")
        )
        prior = read_json(store.path / "prior_art" / "prior_art.json")
        fixture_notice = None
        if state.get("search_mode") != "real_cnipa":
            fixture_notice = (
                "本次运行使用固定 Fixture 演示数据；不得将其作为真实现有技术、"
                "真实查新依据或新颖性结论依据。"
            )
        markdown = render_disclosure_markdown(
            sections,
            search_limitation=prior_search_limitation(prior),
            fixture_notice=fixture_notice,
        )
        path = store.path / "artifacts" / "disclosure.md"
        path.write_text(markdown, encoding="utf-8")
        state["disclosure_render"] = {
            "path": path.relative_to(store.path).as_posix(),
            "renderer": "deterministic_structured_sections_v1",
            "sha256": sha256_file(path),
            "markdown_fence_count": markdown.count("```"),
        }
        store.complete_stage(state, stage)

    def _stage_basic_disclosure_check(
        self,
        store: RunStore,
        state: dict[str, Any],
    ) -> None:
        stage = "basic_disclosure_content_check"
        store.begin_stage(state, stage)
        sections = DisclosureSections.model_validate(
            read_json(store.path / "artifacts" / "disclosure_sections.json")
        )
        materials = (store.path / "parsed" / "materials.md").read_text(
            encoding="utf-8"
        )
        prior = read_json(store.path / "prior_art" / "prior_art.json")
        report = basic_disclosure_content_check(
            sections,
            source_materials=materials,
            prior_art=prior,
        )
        recovery = {
            "attempted": False,
            "status": "not_needed",
            "changed_fields": [],
        }
        rules = {str(issue.get("rule")) for issue in report["issues"]}
        if (
            not report["passed"]
            and self._flow_first(state)
            and rules == {"unsupported_quantitative_fact"}
        ):
            write_json(
                store.path
                / "artifacts"
                / "basic_disclosure_content_check_initial.json",
                report,
            )
            recovered, changed_fields = sanitize_disclosure_quantitative_facts(
                sections,
                source_materials=materials,
            )
            recovery = {
                "attempted": True,
                "status": "failed",
                "changed_fields": changed_fields,
            }
            if changed_fields:
                sections_path = (
                    store.path / "artifacts" / "disclosure_sections.json"
                )
                write_json(sections_path, recovered.model_dump())
                state["disclosure_sections"]["sha256"] = sha256_file(
                    sections_path
                )
                fixture_notice = None
                if state.get("search_mode") != "real_cnipa":
                    fixture_notice = (
                        "本次运行使用固定 Fixture 演示数据；不得将其作为真实现有技术、"
                        "真实查新依据或新颖性结论依据。"
                    )
                markdown = render_disclosure_markdown(
                    recovered,
                    search_limitation=prior_search_limitation(prior),
                    fixture_notice=fixture_notice,
                )
                markdown_path = store.path / "artifacts" / "disclosure.md"
                markdown_path.write_text(markdown, encoding="utf-8")
                state["disclosure_render"] = {
                    "path": markdown_path.relative_to(store.path).as_posix(),
                    "renderer": "deterministic_structured_sections_v1",
                    "sha256": sha256_file(markdown_path),
                    "markdown_fence_count": markdown.count("```"),
                }
                report = basic_disclosure_content_check(
                    recovered,
                    source_materials=materials,
                    prior_art=prior,
                )
                recovery["status"] = (
                    "passed" if report["passed"] else "failed"
                )
                self._record_best_effort(
                    store,
                    state,
                    stage=stage,
                    reason="unsupported_quantitative_fact",
                    fallback="确定性移除材料中无依据的定量值并重新执行基础检查",
                )
        report["recovery"] = recovery
        path = store.path / "artifacts" / "basic_disclosure_content_check.json"
        write_json(path, report)
        if not report["passed"]:
            rules = ", ".join(
                str(issue["rule"]) for issue in report["issues"]
            )
            raise ParseError(
                f"basic disclosure content check failed: {rules}"
            )
        state["quality_gate"] = {
            "status": "passed",
            "self_check_round_count": 2 if recovery["attempted"] else 1,
            "rounds": [],
            "final_quality_status": "passed",
            "unresolved_issues": [],
            "warnings": report["warnings"],
        }
        state["warnings"] = [
            *(state.get("warnings") or []),
            *report["warnings"],
        ]
        store.complete_stage(state, stage)

    def _stage_disclosure_evidence_review(
        self,
        store: RunStore,
        state: dict[str, Any],
    ) -> None:
        stage = "build_disclosure_evidence_review"
        store.begin_stage(state, stage)
        review = write_disclosure_evidence_review(
            run_path=store.path,
            input_sha256=str(state["input_snapshot"]["sha256"]),
            disclosure_sections=read_json(
                store.path / "artifacts" / "disclosure_sections.json"
            ),
        )
        json_path = (
            store.path / "artifacts" / "disclosure_evidence_review.json"
        )
        markdown_path = (
            store.path / "artifacts" / "disclosure_evidence_review.md"
        )
        state["disclosure_evidence_review"] = {
            "json_path": json_path.relative_to(store.path).as_posix(),
            "markdown_path": markdown_path.relative_to(store.path).as_posix(),
            "schema_version": review["schema_version"],
            "section_count": review["section_count"],
            "missing_candidate_locator_count": review[
                "missing_candidate_locator_count"
            ],
            "release_status": review["release_status"],
        }
        store.complete_stage(state, stage)

    def _stage_claim_plan(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "build_claim_plan"
        store.begin_stage(state, stage)
        difference = read_json(store.path / "artifacts" / "difference_analysis.json")
        disclosure = (store.path / "artifacts" / "disclosure.md").read_text(encoding="utf-8")
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        selected = self._selected_point(store, state)
        user = f"""TASK:CLAIM_PLAN
Return a JSON object with title, recommended_claim_types, independent_claims,
dependent_feature_groups, excluded_or_background_features, and warnings.
Use only feature_id values from DIFFERENCE ANALYSIS.
Each independent_claims item must contain claim_type, technical_subject, essential_features;
each essential feature must contain feature_id, text, reason.
Each dependent_feature_groups item must contain parent_claim_type and features;
each feature must contain feature_id, text, reason.
recommended_claim_types must list exactly the claim types implemented by independent_claims:
do not recommend a type without an independent plan and do not omit an independently planned type.
Dependent groups may add a new high-level feature_id or further limit an independent feature by
parameter, sequence, component relation, or implementation detail under the same feature_id.
Do not turn operations merely listed together in CASE MATERIALS into parallel, concurrent,
synchronous, atomic, ordered, short-circuit, or uniquely authoritative behavior unless the
materials explicitly state that relationship.
Every dependent limitation must be explicitly stated in CASE MATERIALS. Do not add illustrative
items, normalization modes, execution order, short-circuit behavior, parameters, state mappings,
or thresholds unless CASE MATERIALS states them. Omit an ungrounded dependent group instead of
inventing a plausible refinement.
Treat DIFFERENCE ANALYSIS and DISCLOSURE as derived summaries, not authority for new technical
detail. A scope-changing modifier, relationship, algorithm, or behavior may appear only when
CASE MATERIALS directly supports it; do not strengthen "threshold" into "dynamic threshold" or
"combine" into "weighted combine" unless the source says so.
Do not mechanically generate every claim type. Include every candidate distinguishing feature
in an independent or dependent plan. Do not create new technical facts or legal conclusions.
If analysis_status is no_comparable_prior_art or search_incomplete_no_comparable_prior_art,
base the plan on target_features and
claim_drafting_candidates only. They are protection candidates from this solution, not validated
distinguishing features. Preserve the search limitation in warnings.
SELECTED POINT:
{json.dumps(selected, ensure_ascii=False)}
DIFFERENCE ANALYSIS:
{json.dumps(difference, ensure_ascii=False)[:100000]}
DISCLOSURE:
{disclosure[:180000]}
CASE MATERIALS:
{materials[:120000]}
"""
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._project_prompt("claim_plan.md"),
                user_prompt=user,
                temperature=0,
            )
            plan = normalize_claim_plan(
                result.data,
                search_mode=str(state["search_mode"]),
                difference_analysis=difference,
            )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            plan = _best_effort_claim_plan(
                difference=difference,
                title=_compact_text(
                    selected.get("title"),
                    "基于案件材料的专利技术方案",
                ),
                search_mode=str(state["search_mode"]),
            )
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="生成仅含一类独立权利要求的最小 Claim Plan",
            )
        path = store.path / "artifacts" / "claim_plan.json"
        write_json(path, plan)
        state["claim_plan"] = {
            "path": path.relative_to(store.path).as_posix(),
            "schema_version": plan["schema_version"],
            "recommended_claim_types": plan["recommended_claim_types"],
            "independent_claim_count": len(plan["independent_claims"]),
        }
        store.complete_stage(state, stage)

    def _stage_claims(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        stage = "draft_claims"
        store.begin_stage(state, stage)
        plan = read_json(store.path / "artifacts" / "claim_plan.json")
        difference = read_json(store.path / "artifacts" / "difference_analysis.json")
        disclosure = (store.path / "artifacts" / "disclosure.md").read_text(encoding="utf-8")
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        user = f"""TASK:CLAIM_DRAFTING
Return JSON {{"claims": [...]}}. Each item must contain claim_id, claim_number,
claim_type, depends_on, text, feature_ids.
Follow CLAIM PLAN exactly. Use only feature_id values present in CLAIM PLAN and DIFFERENCE ANALYSIS.
Keep feature_id values only in the feature_ids metadata array. Never write internal identifiers
such as TF-001 or TF-002 into claim text; spell out the technical feature itself instead.
Do not turn operations merely listed together in CASE MATERIALS into parallel, concurrent,
synchronous, atomic, ordered, short-circuit, or uniquely authoritative behavior unless the
materials explicitly state that relationship.
Number claims continuously from 1. Independent claims have no dependency. A dependent claim
must reference an earlier claim; depends_on must be a JSON array of integers. Prefer adding a
planned feature_id not inherited from its ancestor chain. When the dependent claim instead narrows
an inherited high-level feature by parameter, sequence, component relation, or implementation
detail, it may reuse that feature_id, but its text must state the added limitation clearly.
The claim numbers written in each dependent claim must exactly match depends_on. Keep the protected
subject consistent with claim_type. Introduce each technical object with a clear name, then reuse
the same term with an explicit antecedent such as "所述"; avoid vague, exemplary, preference, or
approximation language such as "上述", "例如", "优选", "大约", "适当", and "必要时".
Every added limitation must be explicitly stated in CASE MATERIALS. Do not add illustrative items,
normalization modes, execution order, short-circuit behavior, parameters, state mappings, or
thresholds merely because they appear plausible. Omit a dependent claim rather than inventing an
ungrounded refinement.
Treat CLAIM PLAN, DIFFERENCE ANALYSIS, and DISCLOSURE as derived summaries, not authority for new
technical detail. A scope-changing modifier, relationship, algorithm, or behavior may appear only
when CASE MATERIALS directly supports it; do not strengthen "threshold" into "dynamic threshold"
or "combine" into "weighted combine" unless the source says so.
Do not include publication numbers, URLs, Fixture identifiers, measured performance results,
professional-review notices, or legal conclusions in claim text.
CLAIM PLAN:
{json.dumps(plan, ensure_ascii=False)[:100000]}
DIFFERENCE ANALYSIS:
{json.dumps(difference, ensure_ascii=False)[:100000]}
DISCLOSURE:
{disclosure[:180000]}
CASE MATERIALS:
{materials[:120000]}
"""
        search_status = str(
            difference.get("analysis_scope", {}).get("search_status")
            or state.get("search_status")
            or ""
        )
        try:
            result = self._model(fake).complete_json(
                system_prompt=self._project_prompt("claim_drafting.md"),
                user_prompt=user,
                temperature=0,
            )
            claims = normalize_claims(
                result.data,
                search_mode=str(state["search_mode"]),
                search_status=search_status or None,
                search_limitation=(
                    PARTIAL_SEARCH_LIMITATION
                    if search_status in {"partial_with_records", "partial_no_records"}
                    else None
                ),
            )
            if (
                is_no_comparable_analysis(difference)
                and contains_prohibited_prior_art_conclusion(claims)
            ):
                raise ParseError(
                    "claims contain an unsupported prior-art conclusion while no comparable record exists"
                )
        except Exception as exc:
            if not self._flow_first(state):
                raise
            claims = _best_effort_claims(
                plan=plan,
                search_mode=str(state["search_mode"]),
                search_status=search_status,
            )
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason=type(exc).__name__,
                fallback="根据 Claim Plan 确定性生成最小结构化权利要求",
            )
        json_path = store.path / "artifacts" / "claims.json"
        markdown_path = store.path / "artifacts" / "claims.md"
        write_json(json_path, claims)
        markdown_path.write_text(render_claims_markdown(claims, plan["title"]), encoding="utf-8")
        state["claims"] = {
            "json_path": json_path.relative_to(store.path).as_posix(),
            "markdown_path": markdown_path.relative_to(store.path).as_posix(),
            "schema_version": claims["schema_version"],
            "claim_count": len(claims["claims"]),
            "claim_types": list(dict.fromkeys(row["claim_type"] for row in claims["claims"])),
        }
        store.complete_stage(state, stage)

    def _stage_validate_claims(self, store: RunStore, state: dict[str, Any]) -> None:
        stage = "validate_claims"
        store.begin_stage(state, stage)
        claims = read_json(store.path / "artifacts" / "claims.json")
        plan = read_json(store.path / "artifacts" / "claim_plan.json")
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        report = validate_claims(
            claims,
            plan,
            source_materials=materials,
        )
        if not report["passed"] and self._flow_first(state):
            initial_path = (
                store.path / "artifacts" / "claim_validation_initial.json"
            )
            write_json(initial_path, report)
            repaired_claims = _best_effort_claims(
                plan=plan,
                search_mode=str(state["search_mode"]),
                search_status=str(state.get("search_status") or ""),
            )
            write_json(store.path / "artifacts" / "claims.json", repaired_claims)
            (store.path / "artifacts" / "claims.md").write_text(
                render_claims_markdown(repaired_claims, plan["title"]),
                encoding="utf-8",
            )
            report = validate_claims(
                repaired_claims,
                plan,
                source_materials=materials,
            )
            state["claims"] = {
                "json_path": "artifacts/claims.json",
                "markdown_path": "artifacts/claims.md",
                "schema_version": repaired_claims["schema_version"],
                "claim_count": len(repaired_claims["claims"]),
                "claim_types": list(
                    dict.fromkeys(
                        row["claim_type"] for row in repaired_claims["claims"]
                    )
                ),
            }
            self._record_best_effort(
                store,
                state,
                stage=stage,
                reason="initial_claim_validation_failed",
                fallback="按 Claim Plan 重建权利要求并重新执行确定性校验",
            )
        path = store.path / "artifacts" / "claim_validation.json"
        write_json(path, report)
        state["claim_validation"] = {
            "path": path.relative_to(store.path).as_posix(),
            "schema_version": report["schema_version"],
            "passed": report["passed"],
            "issue_count": len(report["issues"]),
            "warning_count": len(report["warnings"]),
            "professional_quality_status": report[
                "professional_quality"
            ]["status"],
        }
        if not report["passed"]:
            store.save(state)
            raise ParseError("claim validation failed; inspect artifacts/claim_validation.json")
        store.complete_stage(state, stage)

    def _stage_claim_evidence_review(
        self,
        store: RunStore,
        state: dict[str, Any],
    ) -> None:
        stage = "build_claim_evidence_review"
        store.begin_stage(state, stage)
        review = write_claim_evidence_review(
            run_path=store.path,
            input_sha256=str(state["input_snapshot"]["sha256"]),
            claims=read_json(store.path / "artifacts" / "claims.json"),
            claim_plan=read_json(store.path / "artifacts" / "claim_plan.json"),
            difference_analysis=read_json(
                store.path / "artifacts" / "difference_analysis.json"
            ),
        )
        json_path = store.path / "artifacts" / "claim_evidence_review.json"
        markdown_path = store.path / "artifacts" / "claim_evidence_review.md"
        state["claim_evidence_review"] = {
            "json_path": json_path.relative_to(store.path).as_posix(),
            "markdown_path": markdown_path.relative_to(store.path).as_posix(),
            "schema_version": review["schema_version"],
            "feature_count": review["feature_count"],
            "missing_candidate_locator_count": review[
                "missing_candidate_locator_count"
            ],
            "release_status": review["release_status"],
        }
        state["release_readiness"] = {
            "status": "human_claim_evidence_review_required",
            "human_disclosure_evidence_review_required": True,
            "human_claim_evidence_review_required": True,
            "automatic_quality_gate_passed": (
                state.get("quality_gate", {}).get("final_quality_status")
                == "passed"
                and state.get("claim_validation", {}).get("passed") is True
            ),
        }
        warning = (
            "交底书章节与 Claim–Evidence 人工材料支撑复核尚未完成；"
            "自动 Gate 通过不得单独作为可提交结论。"
        )
        state["warnings"] = list(
            dict.fromkeys([*(state.get("warnings") or []), warning])
        )
        store.complete_stage(state, stage)

    def _legacy_stage_self_check(self, store: RunStore, state: dict[str, Any], fake: bool) -> None:
        """Legacy Markdown-only compatibility code; new Runs never call this method."""
        stage = "self_check"
        store.begin_stage(state, stage)
        draft = (store.path / "artifacts" / "disclosure_draft.md").read_text(encoding="utf-8")
        materials = (store.path / "parsed" / "materials.md").read_text(encoding="utf-8")
        prior = read_json(store.path / "prior_art" / "prior_art.json")

        def run_check(markdown: str, round_number: int) -> tuple[dict[str, Any], str]:
            user = f"""TASK:SELF_CHECK
Return JSON with passed (boolean), issues (array), revised_markdown (full corrected Markdown or empty string if unchanged).
Do not add a self-check chapter. Do not invent prior-art metadata. Unsupported quantitative experimental facts must be removed,
rewritten qualitatively, or explicitly labeled "示例参数，未经实验验证".
This is an explicitly authorized Fixture demo when the draft says 演示数据. Correct labeling is required and is not itself a defect;
do not demand real Fixture URLs or real patent numbers. Mermaid source is expected before export and is not itself a defect.
Judge only unresolved technical, factual, source-support, and disclosure-contract issues in this phase.
DRAFT:
{markdown[:180000]}
"""
            comprehensive_prompt = self._prompt("disclosure_self_check.md")
            final_gate_prompt = """You are the final reliability gate for the local patent-drafting baseline, not a legal-quality optimizer.
Return only JSON with passed (boolean), issues (array), and revised_markdown (always empty).
Fail only for an unresolved major defect in one of these categories:
1. unsupported quantitative experiment/effect facts presented as established facts;
2. invented source metadata or Fixture/demo records presented as real prior art;
3. a zero-result search used to claim no prior art, novelty, or another negative legal/factual conclusion;
4. missing required disclosure sections or an internally contradictory technical flow that makes implementation unusable;
5. confidential/internal workflow content exposed as substantive patent facts.
Do not fail for optional stylistic refinements, alternative formula design preferences, parameter tuning suggestions,
Mermaid still being source before exporter execution, correctly labeled demo limitations, or matters deferred beyond phase 1.
Do not demand real URLs or patent numbers for explicitly labeled Fixture demo records.
The deterministic quantitative checker runs separately, so do not speculate about numeric support when the draft labels a value
as an unverified example. If none of the five blocking categories remains, set passed=true and issues=[].
"""
            result = self._model(fake).complete_json(
                system_prompt=final_gate_prompt if round_number > 1 else comprehensive_prompt,
                user_prompt=user,
                temperature=0,
            )
            if not isinstance(result.data.get("passed"), bool) or not isinstance(result.data.get("issues"), list):
                raise ParseError("self-check contract is incomplete")
            quantitative = check_quantitative_facts(markdown, materials)
            issues = list(result.data["issues"]) + [
                {"type": "unsupported_quantitative_fact", **issue} for issue in quantitative
            ]
            report = {
                "round": round_number,
                "model_passed": result.data["passed"],
                "quantitative_fact_check_passed": not quantitative,
                "passed": bool(result.data["passed"] and not quantitative),
                "issues": issues,
            }
            revised = result.data.get("revised_markdown")
            return report, revised if isinstance(revised, str) else ""

        rounds: list[dict[str, Any]] = []
        first, revised = run_check(draft, 1)
        rounds.append(first)
        revision_performed = False
        if not first["passed"]:
            if revised.strip():
                draft = normalize_disclosure_markdown(revised)
            else:
                revision_prompt = f"""TASK:REVISE_DISCLOSURE
Return JSON with revised_markdown containing the complete corrected Markdown.
Resolve the listed issues. Remove unsupported quantitative experimental facts or rewrite them qualitatively.
Do not add a self-check chapter and do not invent facts.
This is an authorized Fixture demo: keep a concise 演示数据 limitation, but do not expose Fixture identifiers, example URLs,
internal field names, or claim the records are official prior art. Mermaid source is expected before export.
Keep the solution close to CASE MATERIALS and remove invented thresholds, learning rules, benchmark results, and unnecessary formulas.
If a formula remains, preserve only the dimensionally consistent r_i, q_i, c_i, M_i contract supplied in the drafting task,
including the explicit M_i/theta, w_i/B, and c_i hard conditions. Do not add adaptive thresholds or online weight updates.
ISSUES:
{json.dumps(first['issues'], ensure_ascii=False)[:30000]}
DRAFT:
{draft[:180000]}
"""
                revision = self._model(fake).complete_json(
                    system_prompt=self._prompt("disclosure_self_check.md"),
                    user_prompt=revision_prompt,
                    temperature=0,
                )
                draft = normalize_disclosure_markdown(revision.data.get("revised_markdown"))
            draft = sanitize_unsupported_quantitative_facts(draft, materials)
            difference = read_json(store.path / "artifacts" / "difference_analysis.json")
            if (
                is_no_comparable_analysis(difference)
                and contains_prohibited_prior_art_conclusion(draft)
            ):
                raise ParseError(
                    "revised disclosure contains an unsupported prior-art conclusion while no comparable record exists"
                )
            revision_performed = True
            second, _ignored_revision = run_check(draft, 2)
            rounds.append(second)
        final = rounds[-1]
        warnings = list(first["issues"]) if len(rounds) > 1 and final["passed"] else []
        draft = ensure_markdown_search_limitation(draft, prior)
        (store.path / "artifacts" / "disclosure_checked.md").write_text(draft.rstrip() + "\n", encoding="utf-8")
        check_report = {
            "artifact_metadata": {
                "search_mode": prior.get("search_mode"),
                "search_status": prior.get("search_status"),
                "search_limitation": prior_search_limitation(prior),
            },
            "passed": final["passed"],
            "self_check_round_count": len(rounds),
            "revision_count": 1 if revision_performed else 0,
            "rounds": rounds,
            "final_quality_status": "passed" if final["passed"] else "failed",
            "unresolved_issues": [] if final["passed"] else final["issues"],
        }
        write_json(store.path / "artifacts" / "self_check.json", check_report)
        write_json(
            store.path / "artifacts" / "quantitative_fact_check.json",
            {
                "schema_version": "quantitative_fact_check_v1",
                "artifact_metadata": {
                    "search_mode": prior.get("search_mode"),
                    "search_status": prior.get("search_status"),
                    "search_limitation": prior_search_limitation(prior),
                },
                "passed": final["quantitative_fact_check_passed"],
                "issues": [
                    issue
                    for issue in final["issues"]
                    if isinstance(issue, dict) and issue.get("type") == "unsupported_quantitative_fact"
                ],
            },
        )
        state["quality_gate"] = {
            "status": "passed" if final["passed"] else "failed",
            "self_check_round_count": len(rounds),
            "rounds": rounds,
            "final_quality_status": "passed" if final["passed"] else "failed",
            "unresolved_issues": [] if final["passed"] else final["issues"],
            "warnings": warnings,
        }
        state["warnings"] = [*(state.get("warnings") or []), *warnings]
        store.complete_stage(state, stage)

    def _stage_export(self, store: RunStore, state: dict[str, Any]) -> None:
        stage = "export_results"
        store.begin_stage(state, stage)
        checked = store.path / "artifacts" / "disclosure.md"
        final_md = checked
        final_docx = store.path / "artifacts" / "disclosure.docx"
        quality_passed = state.get("quality_gate", {}).get("final_quality_status") == "passed"
        fixture_based = state.get("search_mode", "").startswith(("fixture_", "fake"))
        if not quality_passed:
            classification = ["draft", "quality_warning"]
        elif fixture_based:
            classification = ["demo", "fixture_based"]
        else:
            classification = ["final"]
        if state.get("warnings") and "quality_warning" not in classification:
            classification.append("quality_warning")
        if state.get("best_effort", {}).get("used"):
            if "best_effort" not in classification:
                classification.append("best_effort")
            if "quality_warning" not in classification:
                classification.append("quality_warning")
        docx_requested = os.environ.get("PATENT_AGENT_SKIP_DOCX") != "1"
        docx_generated = False
        render_check_status = "not_run"
        docx_font = resolve_docx_font(self.config.docx_font)
        warnings: list[str] = []
        export_log = ""
        if os.environ.get("PATENT_AGENT_SKIP_DOCX") == "1":
            if self.config.docx_required:
                raise ExportError("DOCX is required but export was disabled by PATENT_AGENT_SKIP_DOCX")
        else:
            export_env = os.environ.copy()
            # Reuse the Chromium installed for the vendored CNIPA Playwright tool
            # instead of requiring a second browser download for Puppeteer.
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as playwright:
                    executable = Path(playwright.chromium.executable_path)
                    if executable.is_file():
                        export_env.setdefault("PUPPETEER_EXECUTABLE_PATH", str(executable))
            except Exception:
                pass
            export_env["PATENT_AGENT_DOCX_FONT"] = self.config.docx_font
            export_log, docx_status = export_markdown_and_docx(
                vendor_root=self.vendor_root,
                checked_md=checked,
                final_md=final_md,
                final_docx=final_docx,
                environment=export_env,
            )
            (store.path / "logs" / "document_export.log").write_text(export_log[-20000:], encoding="utf-8")
            docx_generated = bool(docx_status == "passed" and final_docx.is_file())
            if not docx_generated:
                warnings.append("DOCX generation failed; Markdown remains available")
                if "quality_warning" not in classification:
                    classification.append("quality_warning")
                if self.config.docx_required:
                    raise ExportError("DOCX is required but generation failed")
        artifacts = {
            "classification": classification,
            "workflow_mode": state.get("workflow_mode", "strict"),
            "best_effort": state.get(
                "best_effort",
                {"used": False, "events": []},
            ),
            "search": {
                "mode": state.get("search_mode"),
                "status": state.get("search_status"),
                "limitations": [
                    search_limitation_for(str(state.get("search_status") or ""))
                ],
            },
            "difference_analysis": {
                "path": "artifacts/difference_analysis.json",
                "sha256": sha256_file(store.path / "artifacts" / "difference_analysis.json"),
            },
            "disclosure_preview": {
                "path": "artifacts/disclosure_preview.json",
                "sha256": sha256_file(store.path / "artifacts" / "disclosure_preview.json"),
            },
            "disclosure_sections": {
                "path": "artifacts/disclosure_sections.json",
                "sha256": sha256_file(
                    store.path / "artifacts" / "disclosure_sections.json"
                ),
                "primary_business_artifact": True,
            },
            "disclosure_sections_initial_response": {
                "path": "artifacts/disclosure_sections_initial_response.json",
                "sha256": sha256_file(
                    store.path
                    / "artifacts"
                    / "disclosure_sections_initial_response.json"
                ),
            },
            "basic_disclosure_content_check": {
                "path": "artifacts/basic_disclosure_content_check.json",
                "sha256": sha256_file(
                    store.path
                    / "artifacts"
                    / "basic_disclosure_content_check.json"
                ),
            },
            "disclosure_evidence_review_json": {
                "path": "artifacts/disclosure_evidence_review.json",
                "sha256": sha256_file(
                    store.path
                    / "artifacts"
                    / "disclosure_evidence_review.json"
                ),
                "release_status": state.get(
                    "disclosure_evidence_review",
                    {},
                ).get("release_status"),
            },
            "disclosure_evidence_review_markdown": {
                "path": "artifacts/disclosure_evidence_review.md",
                "sha256": sha256_file(
                    store.path
                    / "artifacts"
                    / "disclosure_evidence_review.md"
                ),
            },
            "claim_plan": {
                "path": "artifacts/claim_plan.json",
                "sha256": sha256_file(store.path / "artifacts" / "claim_plan.json"),
            },
            "claims_json": {
                "path": "artifacts/claims.json",
                "sha256": sha256_file(store.path / "artifacts" / "claims.json"),
            },
            "claims_markdown": {
                "path": "artifacts/claims.md",
                "sha256": sha256_file(store.path / "artifacts" / "claims.md"),
            },
            "claim_validation": {
                "path": "artifacts/claim_validation.json",
                "sha256": sha256_file(store.path / "artifacts" / "claim_validation.json"),
            },
            "claim_evidence_review_json": {
                "path": "artifacts/claim_evidence_review.json",
                "sha256": sha256_file(
                    store.path / "artifacts" / "claim_evidence_review.json"
                ),
                "release_status": state.get(
                    "claim_evidence_review",
                    {},
                ).get("release_status"),
            },
            "claim_evidence_review_markdown": {
                "path": "artifacts/claim_evidence_review.md",
                "sha256": sha256_file(
                    store.path / "artifacts" / "claim_evidence_review.md"
                ),
            },
            "markdown": {
                "path": final_md.relative_to(store.path).as_posix(),
                "sha256": sha256_file(final_md),
                "artifact_status": classification,
                "derived_from": "artifacts/disclosure_sections.json",
            },
            "docx": {
                "docx_requested": docx_requested,
                "docx_required": self.config.docx_required,
                "docx_generated": docx_generated,
                "configured_font": self.config.docx_font,
                "configured_font_available": docx_font.configured_font_available,
                "effective_font": docx_font.effective_font,
                "font_fallback_used": docx_font.fallback_used,
                "render_check_status": render_check_status,
                "visual_qa_status": "not_run",
                "path": final_docx.relative_to(store.path).as_posix() if docx_generated else None,
                "sha256": sha256_file(final_docx) if docx_generated else None,
            },
            "warnings": warnings,
        }
        if state.get("external_evidence_reuse"):
            artifacts["external_evidence_reuse"] = state[
                "external_evidence_reuse"
            ]
        if state.get("rework_artifacts"):
            artifacts["rework_artifacts"] = state["rework_artifacts"]
        recovery_response = (
            store.path
            / "artifacts"
            / "disclosure_sections_recovery_response.json"
        )
        if recovery_response.is_file():
            artifacts["disclosure_sections_recovery_response"] = {
                "path": recovery_response.relative_to(store.path).as_posix(),
                "sha256": sha256_file(recovery_response),
            }
        initial_claim_validation = (
            store.path / "artifacts" / "claim_validation_initial.json"
        )
        if initial_claim_validation.is_file():
            artifacts["claim_validation_initial"] = {
                "path": initial_claim_validation.relative_to(store.path).as_posix(),
                "sha256": sha256_file(initial_claim_validation),
            }
        write_json(store.path / "artifacts" / "manifest.json", artifacts)
        state["artifacts"] = artifacts
        store.complete_stage(state, stage)

    def _write_phase_result(self, store: RunStore, state: dict[str, Any]) -> None:
        lines = [
            "# PATENT_DRAFTING_AGENT_CORE_WORKFLOW_COMPLETION_V0_2 phase result",
            "",
            f"- Run ID: `{store.run_id}`",
            f"- Status: `{state.get('status')}`",
            f"- Current stage: `{state.get('current_stage')}`",
            f"- Provider mode: `{state.get('provider_mode')}`",
            f"- Workflow mode: `{state.get('workflow_mode', 'strict')}`",
            f"- Best-effort used: `{bool(state.get('best_effort', {}).get('used'))}`",
            f"- Search mode: `{state.get('search_mode')}`",
            f"- Search status: `{state.get('search_status')}`",
            f"- Qwen external check: `{state.get('external_checks', {}).get('qwen')}`",
            f"- CNIPA external check: `{state.get('external_checks', {}).get('cnipa')}`",
            f"- Final quality status: `{state.get('quality_gate', {}).get('final_quality_status')}`",
            f"- Self-check rounds: `{state.get('quality_gate', {}).get('self_check_round_count')}`",
            f"- Difference analysis: `{state.get('difference_analysis') is not None}`",
            f"- Disclosure generation mode: `{state.get('disclosure_generation_mode', 'legacy_markdown')}`",
            f"- Structured section recovery: `{state.get('section_recovery', {}).get('status', 'legacy_run')}`",
            f"- Disclosure Markdown renderer: `{state.get('disclosure_render', {}).get('renderer', 'legacy_markdown')}`",
            f"- Claim plan: `{state.get('claim_plan') is not None}`",
            f"- Claims draft count: `{state.get('claims', {}).get('claim_count') if state.get('claims') else 0}`",
            f"- Claim types: `{', '.join(state.get('claims', {}).get('claim_types', [])) if state.get('claims') else ''}`",
            f"- Claim validation passed: `{state.get('claim_validation', {}).get('passed') if state.get('claim_validation') else False}`",
            f"- Claim–Evidence review: `{(state.get('claim_evidence_review') or {}).get('release_status', 'not_run')}`",
            f"- Release readiness: `{(state.get('release_readiness') or {}).get('status', 'not_evaluated')}`",
            "- Claims notice: `AI-assisted draft; professional patent review required`",
            "- Parser scope: Markdown/TXT direct, DOCX/PPTX via vendored tools, text PDF via pypdf.",
            "",
            "## Artifacts",
            "",
            f"```json\n{json.dumps(state.get('artifacts', {}), ensure_ascii=False, indent=2)}\n```",
            "",
            "## Non-blocking warnings",
            "",
            f"```json\n{json.dumps(state.get('warnings', []), ensure_ascii=False, indent=2)}\n```",
        ]
        if state.get("error"):
            lines.extend(["", "## Error", "", f"- Type: `{state['error']['type']}`", f"- Message: {state['error']['message']}"])
        (store.path / "phase_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def status_payload(config: AppConfig, run_id: str) -> dict[str, Any]:
    state = RunStore(config, run_id).load()
    return {
        "run_id": state["run_id"],
        "status": state["status"],
        "current_stage": state["current_stage"],
        "completed_stages": state["completed_stages"],
        "stage_timings": state.get("stage_timings", []),
        "pending_action": state.get("pending_action"),
        "provider_mode": state.get("provider_mode"),
        "workflow_mode": state.get("workflow_mode", "strict"),
        "best_effort": state.get(
            "best_effort",
            {"used": False, "events": []},
        ),
        "search_mode": state.get("search_mode"),
        "search_status": state.get("search_status"),
        "external_checks": state.get("external_checks"),
        "error": state.get("error"),
        "artifacts": state.get("artifacts"),
        "quality_gate": state.get("quality_gate"),
        "difference_analysis": state.get("difference_analysis"),
        "disclosure_generation_mode": state.get(
            "disclosure_generation_mode",
            "legacy_markdown",
        ),
        "disclosure_sections": state.get("disclosure_sections"),
        "disclosure_render": state.get("disclosure_render"),
        "section_recovery": state.get(
            "section_recovery",
            {
                "attempted": False,
                "requested_fields": [],
                "recovered_fields": [],
                "status": "legacy_run",
            },
        ),
        "markdown_normalization": state.get("markdown_normalization"),
        "disclosure_section_recovery": state.get("disclosure_section_recovery"),
        "claim_plan": state.get("claim_plan"),
        "claims": state.get("claims"),
        "claim_validation": state.get("claim_validation"),
        "disclosure_evidence_review": state.get(
            "disclosure_evidence_review"
        ),
        "claim_evidence_review": state.get("claim_evidence_review"),
        "release_readiness": state.get("release_readiness"),
        "external_evidence_reuse": state.get("external_evidence_reuse"),
        "rework_artifacts": state.get("rework_artifacts"),
        "parent_run_id": state.get("parent_run_id"),
        "parse_summary": state.get("parse_summary"),
        "warnings": state.get("warnings"),
    }


def status_summary_payload(
    config: AppConfig,
    run_id: str,
) -> dict[str, Any]:
    state = status_payload(config, run_id)
    timings = [
        row
        for row in state.get("stage_timings") or []
        if isinstance(row, dict)
    ]
    measured = [
        row
        for row in timings
        if isinstance(row.get("elapsed_seconds"), (int, float))
        and row.get("outcome") in {"completed", "failed"}
    ]
    total_elapsed = round(
        sum(float(row["elapsed_seconds"]) for row in measured),
        3,
    )
    slowest = (
        max(measured, key=lambda row: float(row["elapsed_seconds"]))
        if measured
        else None
    )
    claim_validation = state.get("claim_validation") or {}
    claims = state.get("claims") or {}
    artifacts = state.get("artifacts") or {}
    docx = artifacts.get("docx") or {}
    release = state.get("release_readiness") or {}
    status = str(state.get("status") or "")
    pending = state.get("pending_action")
    if pending == "PATENT_POINT_SELECTION":
        next_action = "submit_patent_point_selection"
    elif status == "failed":
        next_action = "inspect_error_and_resume"
    elif status in {
        "completed",
        "completed_with_warnings",
        "demo_completed_with_fixture",
    } and release.get("status") in {
        "human_claim_evidence_review_required",
        "human_review_required",
    }:
        next_action = "complete_human_evidence_review"
    else:
        next_action = None
    key_artifacts: dict[str, str] = {}
    for key in (
        "disclosure_sections",
        "claim_plan",
        "claims_json",
        "claim_validation",
        "markdown",
        "docx",
    ):
        value = artifacts.get(key)
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            key_artifacts[key] = value["path"]
    warnings = list(state.get("warnings") or [])
    return {
        "run_id": state["run_id"],
        "status": status,
        "current_stage": state.get("current_stage"),
        "next_action": next_action,
        "workflow_mode": state.get("workflow_mode"),
        "provider_mode": state.get("provider_mode"),
        "total_elapsed_seconds": total_elapsed,
        "slowest_stage": (
            {
                "stage": slowest.get("stage"),
                "attempt": slowest.get("attempt"),
                "elapsed_seconds": slowest.get("elapsed_seconds"),
                "outcome": slowest.get("outcome"),
            }
            if slowest is not None
            else None
        ),
        "search": {
            "mode": state.get("search_mode"),
            "status": state.get("search_status"),
            "external_check": (
                state.get("external_checks") or {}
            ).get("cnipa"),
        },
        "best_effort_used": bool(
            (state.get("best_effort") or {}).get("used")
        ),
        "claims": {
            "count": claims.get("claim_count", 0),
            "types": claims.get("claim_types", []),
            "validation_passed": claim_validation.get("passed"),
            "issue_count": claim_validation.get("issue_count"),
            "warning_count": claim_validation.get("warning_count"),
            "professional_quality_status": claim_validation.get(
                "professional_quality_status"
            ),
        },
        "review": {
            "disclosure": (
                state.get("disclosure_evidence_review") or {}
            ).get("release_status"),
            "claims": (
                state.get("claim_evidence_review") or {}
            ).get("release_status"),
            "release_readiness": release.get("status"),
        },
        "docx": {
            "generated": docx.get("docx_generated"),
            "render_check_status": docx.get("render_check_status"),
            "visual_qa_status": docx.get("visual_qa_status"),
        },
        "warning_count": len(warnings),
        "warnings": warnings,
        "error": state.get("error"),
        "key_artifacts": key_artifacts,
    }
