from __future__ import annotations

import json
import re
from typing import Any

from patent_agent.errors import ParseError
from patent_agent.quality import check_quantitative_facts


DISCLOSURE_STATUSES = {"disclosed", "partially_disclosed", "not_found", "uncertain"}
CLAIM_ROLES = {"independent", "dependent", "background_only", "exclude"}
CLAIM_TYPES = {"method", "system", "device", "storage_medium", "program_product"}
LEGAL_CONCLUSION = re.compile(r"不存在现有技术|具备新颖性|具有创造性|必然授权|授权概率|确保授权|不侵权")
NO_COMPARABLE_PRIOR_ART_CONCLUSION = re.compile(
    r"现有技术未披露|现有方案没有涉及|现有技术中不存在|不存在现有技术|"
    r"区别于现有技术|具备新颖性|具有创造性|首次提出|尚无相关方案|"
    r"必然授权|授权概率|确保授权|不侵权"
)
PUBLICATION_OR_LINK = re.compile(
    r"(?:https?://|fixture://|FIXTURE-[A-Z0-9-]+|(?<![A-Za-z0-9])CN\d{6,}[A-Z]\d?(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
INTERNAL_FEATURE_ID = re.compile(
    r"(?<![A-Za-z0-9])TF-\d{3,}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
CLAIM_REFERENCE = re.compile(
    r"根据权利要求(?P<references>[^，。；]{1,60}?)所述的?"
    r"(?P<subject>计算机可读存储介质|存储介质|计算机程序产品|"
    r"程序产品|方法|系统|装置|设备)"
)
AMBIGUOUS_CLAIM_LANGUAGE = re.compile(
    r"本发明|上述|例如|举例|优选|大约|约为|适当|必要时|等等"
)
UNCLEAR_ANTECEDENT = re.compile(
    r"该(?:方法|系统|装置|设备|介质|程序产品|模块|单元|对象|"
    r"数据|参数|步骤|结果|状态|请求|响应|信号)"
)
CLAIM_SUBJECT_TERMS = {
    "method": {"方法"},
    "system": {"系统"},
    "device": {"装置", "设备"},
    "storage_medium": {"存储介质", "计算机可读存储介质"},
    "program_product": {"程序产品", "计算机程序产品"},
}
PARTIAL_SEARCH_STATUSES = {"partial_with_records", "partial_no_records"}
NO_COMPARABLE_ANALYSIS_STATUSES = {
    "no_comparable_prior_art",
    "search_incomplete_no_comparable_prior_art",
}
PARTIAL_SEARCH_LIMITATION = (
    "本次专利检索仅部分完成，部分查询因外部超时或工具错误未获得结果。"
    "现有检索结果仅用于辅助形成文书草案，不能据此形成完整现有技术检索、"
    "新颖性、创造性或授权结论。"
)


def is_no_comparable_analysis(value: dict[str, Any]) -> bool:
    return value.get("analysis_status") in NO_COMPARABLE_ANALYSIS_STATUSES


def contains_prohibited_prior_art_conclusion(value: Any) -> bool:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return bool(NO_COMPARABLE_PRIOR_ART_CONCLUSION.search(text))


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParseError(f"{field} must be a non-empty string")
    return value.strip()


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ParseError(f"{field} must be a list")
    return value


def _text_units(text: str) -> set[str]:
    compact = "".join(
        re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text)
    ).lower()
    if len(compact) < 2:
        return {compact} if compact else set()
    return {
        compact[index : index + 2]
        for index in range(len(compact) - 1)
    }


def _text_overlap_score(query: str, candidate: str) -> float:
    query_units = _text_units(query)
    if not query_units:
        return 0.0
    return len(query_units & _text_units(candidate)) / len(query_units)


def _claim_reference_numbers(value: str) -> set[int]:
    numbers = {
        int(number)
        for number in re.findall(r"\d+", value)
        if int(number) > 0
    }
    for start_text, end_text in re.findall(
        r"(\d+)\s*(?:至|到|[-—~～])\s*(\d+)",
        value,
    ):
        start = int(start_text)
        end = int(end_text)
        if 0 < start <= end and end - start <= 100:
            numbers.update(range(start, end + 1))
    return numbers


def _plan_feature_texts(
    claim_plan: dict[str, Any],
) -> dict[str, list[str]]:
    feature_texts: dict[str, list[str]] = {}
    for independent in claim_plan["independent_claims"]:
        for feature in independent["essential_features"]:
            feature_texts.setdefault(feature["feature_id"], []).append(
                feature["text"]
            )
    for group in claim_plan["dependent_feature_groups"]:
        for feature in group["features"]:
            feature_texts.setdefault(feature["feature_id"], []).append(
                feature["text"]
            )
    return {
        feature_id: list(dict.fromkeys(texts))
        for feature_id, texts in feature_texts.items()
    }


def normalize_difference_analysis(
    value: Any,
    *,
    selected_patent_point_id: str,
    search_mode: str,
    prior_art_records: list[dict[str, Any]],
    search_status: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParseError("difference analysis must be an object")
    raw_features = _list(value.get("target_features"), "target_features")
    if not raw_features:
        raise ParseError("difference analysis requires at least one target feature")

    features: list[dict[str, str]] = []
    feature_id_map: dict[str, str] = {}
    for index, raw in enumerate(raw_features, 1):
        if not isinstance(raw, dict):
            raise ParseError("target feature must be an object")
        stable_id = f"TF-{index:03d}"
        raw_id = str(raw.get("feature_id") or stable_id)
        feature_id_map[raw_id] = stable_id
        feature_id_map[stable_id] = stable_id
        features.append(
            {
                "feature_id": stable_id,
                "feature_text": _nonempty_text(raw.get("feature_text"), "target feature text"),
                "source_summary": _nonempty_text(raw.get("source_summary"), "target feature source summary"),
            }
        )

    records_by_publication = {
        str(record["publication_number"]): record
        for record in prior_art_records
        if record.get("publication_number")
    }
    no_comparable_prior_art = not records_by_publication
    if no_comparable_prior_art and contains_prohibited_prior_art_conclusion(features):
        raise ParseError("target features contain an unsupported prior-art conclusion")
    allowed_publications = set(records_by_publication)
    comparisons: list[dict[str, Any]] = []
    removed_unknown_publications: list[str] = []
    for raw in _list(value.get("comparisons", []), "comparisons"):
        if not isinstance(raw, dict):
            raise ParseError("difference comparison must be an object")
        if no_comparable_prior_art:
            continue
        publication = _nonempty_text(raw.get("publication_number"), "comparison publication_number")
        if publication not in allowed_publications:
            removed_unknown_publications.append(publication)
            continue
        feature_id = feature_id_map.get(str(raw.get("feature_id")))
        if not feature_id:
            raise ParseError("difference comparison references an unknown feature_id")
        status = str(raw.get("disclosure_status") or "")
        role = str(raw.get("recommended_claim_role") or "")
        if status not in DISCLOSURE_STATUSES:
            raise ParseError("difference comparison has an invalid disclosure_status")
        if role not in CLAIM_ROLES:
            raise ParseError("difference comparison has an invalid recommended_claim_role")
        analysis = _nonempty_text(raw.get("analysis"), "difference comparison analysis")
        if LEGAL_CONCLUSION.search(analysis):
            raise ParseError("difference analysis contains a prohibited legal conclusion")
        comparisons.append(
            {
                "feature_id": feature_id,
                "publication_number": publication,
                "prior_art_title": _nonempty_text(
                    records_by_publication[publication].get("title"),
                    "returned prior-art title",
                ),
                "disclosure_status": status,
                "analysis": analysis,
                "recommended_claim_role": role,
            }
        )
    if prior_art_records and not comparisons:
        raise ParseError("difference analysis contains no comparison to a returned prior-art record")
    compared_feature_ids = {row["feature_id"] for row in comparisons}
    missing_comparisons = {row["feature_id"] for row in features} - compared_feature_ids
    if prior_art_records and missing_comparisons:
        raise ParseError(
            "difference analysis omits target features from comparisons: "
            + ", ".join(sorted(missing_comparisons))
        )

    distinguishing: list[dict[str, str]] = []
    if not no_comparable_prior_art:
        for raw in _list(value.get("candidate_distinguishing_features"), "candidate_distinguishing_features"):
            if not isinstance(raw, dict):
                raise ParseError("candidate distinguishing feature must be an object")
            feature_id = feature_id_map.get(str(raw.get("feature_id")))
            if not feature_id:
                raise ParseError("candidate distinguishing feature references an unknown feature_id")
            role = str(raw.get("recommended_claim_role") or "")
            if role not in {"independent", "dependent"}:
                raise ParseError("candidate distinguishing feature must be recommended for an independent or dependent claim")
            reason = _nonempty_text(raw.get("reason"), "candidate distinguishing feature reason")
            if LEGAL_CONCLUSION.search(reason):
                raise ParseError("difference analysis contains a prohibited legal conclusion")
            distinguishing.append({"feature_id": feature_id, "reason": reason, "recommended_claim_role": role})
    if not no_comparable_prior_art and not distinguishing:
        raise ParseError("difference analysis requires at least one candidate distinguishing feature")

    limitations = (
        []
        if no_comparable_prior_art
        else [str(item).strip() for item in _list(value.get("limitations", []), "limitations") if str(item).strip()]
    )
    if removed_unknown_publications:
        limitations.append(
            "已移除模型生成但未出现在当前 CNIPA 检索记录中的公开号："
            + "、".join(sorted(set(removed_unknown_publications)))
        )
    fixture_based = search_mode != "real_cnipa"
    search_status = search_status or (
        "complete_with_records" if records_by_publication else "complete_zero_results"
    )
    if no_comparable_prior_art:
        if search_status in {"partial_no_records", "failed"}:
            analysis_status = "search_incomplete_no_comparable_prior_art"
            notice = (
                (
                    f"{PARTIAL_SEARCH_LIMITATION}"
                    if search_status == "partial_no_records"
                    else "本次真实专利检索未获得正常完成证据。"
                )
                + "本次没有可比较记录，不能据此判断相关技术特征是否已被现有技术披露。"
            )
        else:
            analysis_status = "no_comparable_prior_art"
            notice = "本次查询未获得可比较记录，不能据此判断相关技术特征是否已被现有技术披露。"
        limitations.append(notice)
        limitations.append("zero_results 仅表示对应查询未命中，不能形成现有技术披露状态或法律结论。")
    else:
        analysis_status = "comparison_completed"
        if search_status in PARTIAL_SEARCH_STATUSES:
            notice = PARTIAL_SEARCH_LIMITATION
            limitations.append(PARTIAL_SEARCH_LIMITATION)
            if fixture_based:
                limitations.append(
                    "该分析同时使用了显式授权的 Fixture 演示数据，不构成真实现有技术检索结论。"
                )
        else:
            notice = (
                "该分析仅用于演示 Agent 流程，不构成真实现有技术检索结论。"
                if fixture_based
                else "该分析仅基于本次返回的 CNIPA 记录，不构成新颖性、创造性、授权或法律结论。"
            )
        limitations.append(
            "zero_results 仅表示对应查询未命中，不表示现有技术不存在，也不支持新颖性或创造性结论。"
        )
    if any(LEGAL_CONCLUSION.search(text) for text in limitations):
        raise ParseError("difference analysis limitations contain a prohibited legal conclusion")
    return {
        "schema_version": "difference_analysis_v1",
        "analysis_status": analysis_status,
        "analysis_scope": {
            "selected_patent_point_id": selected_patent_point_id,
            "search_mode": search_mode,
            "search_status": search_status,
            "fixture_based": fixture_based,
            "comparable_record_count": len(records_by_publication),
            "notice": notice,
        },
        "target_features": features,
        "comparisons": comparisons,
        "candidate_distinguishing_features": distinguishing,
        "claim_drafting_candidates": [
            {
                "feature_id": row["feature_id"],
                "feature_text": row["feature_text"],
                "basis": "来自本方案材料的保护候选，未作现有技术区别判断。",
            }
            for row in features
        ]
        if no_comparable_prior_art
        else [],
        "limitations": list(dict.fromkeys(limitations)),
    }


def normalize_claim_plan(
    value: Any,
    *,
    search_mode: str,
    difference_analysis: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParseError("claim plan must be an object")
    if LEGAL_CONCLUSION.search(json.dumps(value, ensure_ascii=False)):
        raise ParseError("claim plan contains a prohibited legal conclusion")
    no_comparable_prior_art = is_no_comparable_analysis(difference_analysis)
    if no_comparable_prior_art and contains_prohibited_prior_art_conclusion(value):
        raise ParseError("claim plan contains an unsupported prior-art conclusion")
    feature_ids = {row["feature_id"] for row in difference_analysis["target_features"]}
    distinguishing_ids = {
        row["feature_id"] for row in difference_analysis["candidate_distinguishing_features"]
    }
    recommended_input = list(
        dict.fromkeys(
            str(item)
            for item in _list(
                value.get("recommended_claim_types"),
                "recommended_claim_types",
            )
        )
    )
    if not recommended_input or any(
        item not in CLAIM_TYPES
        for item in recommended_input
    ):
        raise ParseError("claim plan has no valid recommended claim type")

    independent_claims: list[dict[str, Any]] = []
    for raw in _list(value.get("independent_claims"), "independent_claims"):
        if not isinstance(raw, dict):
            raise ParseError("independent claim plan must be an object")
        claim_type = str(raw.get("claim_type") or "")
        if claim_type not in CLAIM_TYPES:
            raise ParseError("independent claim plan uses an invalid claim type")
        essentials: list[dict[str, str]] = []
        for feature in _list(raw.get("essential_features"), "essential_features"):
            if not isinstance(feature, dict):
                raise ParseError("essential feature must be an object")
            feature_id = str(feature.get("feature_id") or "")
            if feature_id not in feature_ids:
                raise ParseError("claim plan references an unknown feature_id")
            essentials.append(
                {
                    "feature_id": feature_id,
                    "text": _nonempty_text(feature.get("text"), "essential feature text"),
                    "reason": _nonempty_text(feature.get("reason"), "essential feature reason"),
                }
            )
        if not essentials:
            raise ParseError("independent claim plan requires essential features")
        independent_claims.append(
            {
                "claim_type": claim_type,
                "technical_subject": _nonempty_text(raw.get("technical_subject"), "technical subject"),
                "essential_features": essentials,
            }
        )
    if not independent_claims:
        raise ParseError("claim plan requires at least one independent claim")
    recommended = list(
        dict.fromkeys(row["claim_type"] for row in independent_claims)
    )
    removed_recommended_types = [
        item
        for item in recommended_input
        if item not in recommended
    ]
    added_recommended_types = [
        item
        for item in recommended
        if item not in recommended_input
    ]
    normalizations: list[dict[str, Any]] = []
    if removed_recommended_types or added_recommended_types:
        normalizations.append(
            {
                "rule": "recommended_types_match_independent_plan",
                "removed": removed_recommended_types,
                "added": added_recommended_types,
            }
        )

    dependent_groups: list[dict[str, Any]] = []
    for raw in _list(value.get("dependent_feature_groups", []), "dependent_feature_groups"):
        if not isinstance(raw, dict):
            raise ParseError("dependent feature group must be an object")
        parent = str(raw.get("parent_claim_type") or "")
        if parent not in recommended:
            raise ParseError("dependent feature group uses an unrecommended parent claim type")
        group_features: list[dict[str, str]] = []
        for feature in _list(raw.get("features"), "dependent group features"):
            if not isinstance(feature, dict):
                raise ParseError("dependent feature must be an object")
            feature_id = str(feature.get("feature_id") or "")
            if feature_id not in feature_ids:
                raise ParseError("dependent feature group references an unknown feature_id")
            group_features.append(
                {
                    "feature_id": feature_id,
                    "text": _nonempty_text(feature.get("text"), "dependent feature text"),
                    "reason": _nonempty_text(feature.get("reason"), "dependent feature reason"),
                }
            )
        if group_features:
            dependent_groups.append({"parent_claim_type": parent, "features": group_features})

    planned_ids = {
        feature["feature_id"]
        for row in independent_claims
        for feature in row["essential_features"]
    } | {
        feature["feature_id"]
        for row in dependent_groups
        for feature in row["features"]
    }
    missing_distinguishing = distinguishing_ids - planned_ids
    if missing_distinguishing:
        raise ParseError(
            "claim plan omits candidate distinguishing features: "
            + ", ".join(sorted(missing_distinguishing))
        )
    excluded = [
        str(item).strip()
        for item in _list(value.get("excluded_or_background_features", []), "excluded_or_background_features")
        if str(item).strip()
    ]
    warnings = [str(item).strip() for item in _list(value.get("warnings", []), "warnings") if str(item).strip()]
    if removed_recommended_types or added_recommended_types:
        warnings.append(
            "recommended_claim_types 已按实际独立权利要求规划规范化；"
            f"移除={removed_recommended_types or []}，"
            f"补入={added_recommended_types or []}。"
        )
    if no_comparable_prior_art:
        warnings.append("本次查询未获得可比较记录；本规划仅基于本方案核心技术手段和选定专利点。")
    search_status = str(
        difference_analysis.get("analysis_scope", {}).get("search_status") or ""
    )
    search_limitation = (
        PARTIAL_SEARCH_LIMITATION if search_status in PARTIAL_SEARCH_STATUSES else None
    )
    if search_limitation:
        warnings.append(search_limitation)
    return {
        "schema_version": "claim_plan_v1",
        "artifact_metadata": {
            "search_mode": search_mode,
            "search_status": search_status or None,
            "search_limitation": search_limitation,
            "fixture_based": search_mode != "real_cnipa",
            "analysis_status": difference_analysis.get("analysis_status", "comparison_completed"),
            "normalizations": normalizations,
            "notice": "AI 辅助生成的权利要求规划，需由专利专业人员审核，不构成法律意见。",
        },
        "title": _nonempty_text(value.get("title"), "claim plan title"),
        "recommended_claim_types": recommended,
        "independent_claims": independent_claims,
        "dependent_feature_groups": dependent_groups,
        "excluded_or_background_features": excluded,
        "warnings": list(dict.fromkeys(warnings)),
    }


def normalize_claims(
    value: Any,
    *,
    search_mode: str,
    search_status: str | None = None,
    search_limitation: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParseError("claims payload must be an object")
    claims: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(value.get("claims"), "claims"), 1):
        if not isinstance(raw, dict):
            raise ParseError("claim must be an object")
        number = raw.get("claim_number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise ParseError("claim_number must be an integer")
        depends_on = raw.get("depends_on", [])
        if isinstance(depends_on, int) and not isinstance(depends_on, bool):
            depends_on = [depends_on]
            normalizations.append(
                {
                    "rule": "scalar_dependency_to_list",
                    "claim_number": number,
                }
            )
        if not isinstance(depends_on, list) or any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in depends_on
        ):
            raise ParseError("depends_on must be an integer list")
        feature_ids = raw.get("feature_ids")
        if not isinstance(feature_ids, list) or any(not isinstance(item, str) for item in feature_ids):
            raise ParseError("feature_ids must be a string list")
        claims.append(
            {
                "claim_id": f"CL-{index:03d}",
                "claim_number": number,
                "claim_type": _nonempty_text(raw.get("claim_type"), "claim_type"),
                "depends_on": depends_on,
                "text": str(raw.get("text") or "").strip(),
                "feature_ids": list(dict.fromkeys(feature_ids)),
            }
        )
    if not claims:
        raise ParseError("claims payload requires at least one claim")
    return {
        "schema_version": "claims_v1",
        "artifact_metadata": {
            "search_mode": search_mode,
            "search_status": search_status,
            "search_limitation": search_limitation,
            "fixture_based": search_mode != "real_cnipa",
            "normalizations": normalizations,
            "notice": "AI 辅助生成的权利要求草案，需由专利专业人员审核。",
        },
        "claims": claims,
    }


def validate_claims(
    claims_payload: dict[str, Any],
    claim_plan: dict[str, Any],
    *,
    source_materials: str,
) -> dict[str, Any]:
    claims = claims_payload["claims"]
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    numbers = [row["claim_number"] for row in claims]
    if numbers != list(range(1, len(claims) + 1)):
        issues.append({"rule": "continuous_numbering", "message": "权利要求编号必须从 1 连续递增。"})
    by_number = {row["claim_number"]: row for row in claims}
    allowed_claim_types = {
        f"{kind}_{claim_type}"
        for claim_type in claim_plan["recommended_claim_types"]
        for kind in ("independent", "dependent")
    }
    known_feature_ids = {
        feature["feature_id"]
        for row in claim_plan["independent_claims"]
        for feature in row["essential_features"]
    } | {
        feature["feature_id"]
        for row in claim_plan["dependent_feature_groups"]
        for feature in row["features"]
    }
    plan_feature_texts = _plan_feature_texts(claim_plan)
    if len(by_number) != len(claims):
        issues.append({"rule": "unique_numbering", "message": "权利要求编号不得重复。"})

    independent = [row for row in claims if row["claim_type"].startswith("independent_")]
    if not independent:
        issues.append({"rule": "independent_required", "message": "至少需要一项独立权利要求。"})
    for row in claims:
        number = row["claim_number"]
        dependent = row["claim_type"].startswith("dependent_")
        text_number = re.match(r"^\s*(\d+)\s*[.．、]", row["text"])
        if (
            text_number is not None
            and int(text_number.group(1)) != number
        ):
            issues.append(
                {
                    "rule": "text_number_matches_metadata",
                    "claim_number": number,
                    "message": (
                        "权利要求正文显式起始编号与 claim_number 元数据不一致。"
                    ),
                }
            )
        if row["claim_type"] not in allowed_claim_types:
            issues.append({"rule": "planned_claim_type", "claim_number": number, "message": "权利要求类型不在 Claim Plan 中。"})
        unknown_features = set(row["feature_ids"]) - known_feature_ids
        if unknown_features:
            issues.append(
                {
                    "rule": "known_feature_ids",
                    "claim_number": number,
                    "message": "权利要求引用了 Claim Plan 中不存在的 feature_id。",
                    "feature_ids": sorted(unknown_features),
                }
            )
        if not row["text"]:
            issues.append({"rule": "nonempty_text", "claim_number": number, "message": "权利要求文本不得为空。"})
        if row["claim_type"].startswith("independent_") and row["depends_on"]:
            issues.append({"rule": "independent_no_dependency", "claim_number": number, "message": "独立权利要求不得依赖其他权利要求。"})
        if dependent and not row["depends_on"]:
            issues.append({"rule": "dependent_requires_parent", "claim_number": number, "message": "从属权利要求必须引用在先权利要求。"})
        for parent in row["depends_on"]:
            if parent not in by_number:
                issues.append({"rule": "dependency_exists", "claim_number": number, "message": f"引用的权利要求 {parent} 不存在。"})
            elif parent >= number:
                issues.append({"rule": "dependency_precedes_claim", "claim_number": number, "message": f"引用编号 {parent} 必须小于从属权利要求编号。"})
        if PUBLICATION_OR_LINK.search(row["text"]):
            issues.append({"rule": "no_publication_or_link", "claim_number": number, "message": "权利要求正文不得包含公开号、Fixture 标识或检索链接。"})
        if INTERNAL_FEATURE_ID.search(row["text"]):
            issues.append(
                {
                    "rule": "no_internal_feature_id_in_text",
                    "claim_number": number,
                    "message": "权利要求正文不得包含 Claim Plan 内部 feature_id；技术特征应直接写入正文。",
                }
            )
        if LEGAL_CONCLUSION.search(row["text"]):
            issues.append({"rule": "no_legal_conclusion", "claim_number": number, "message": "权利要求正文不得包含法律结论。"})
        claim_kind, _, claim_subject = row["claim_type"].partition("_")
        allowed_subject_terms = CLAIM_SUBJECT_TERMS.get(
            claim_subject,
            set(),
        )
        if claim_kind == "independent":
            if CLAIM_REFERENCE.search(row["text"]):
                issues.append(
                    {
                        "rule": "independent_no_reference_text",
                        "claim_number": number,
                        "message": "独立权利要求正文不得引用其他权利要求。",
                    }
                )
            subject_prefix = row["text"].split("其特征在于", 1)[0]
            if (
                allowed_subject_terms
                and not any(
                    term in subject_prefix
                    for term in allowed_subject_terms
                )
            ):
                issues.append(
                    {
                        "rule": "claim_subject_consistency",
                        "claim_number": number,
                        "message": (
                            "权利要求正文的保护主题与 claim_type 不一致。"
                        ),
                    }
                )
        elif dependent:
            reference = CLAIM_REFERENCE.search(row["text"])
            if reference is None:
                issues.append(
                    {
                        "rule": "reference_matches_dependency",
                        "claim_number": number,
                        "message": (
                            "从属权利要求正文缺少可识别的在先权利要求引用。"
                        ),
                    }
                )
            else:
                referenced_numbers = _claim_reference_numbers(
                    reference.group("references")
                )
                if referenced_numbers != set(row["depends_on"]):
                    issues.append(
                        {
                            "rule": "reference_matches_dependency",
                            "claim_number": number,
                            "message": (
                                "从属权利要求正文引用编号与 depends_on "
                                "元数据不一致。"
                            ),
                            "text_references": sorted(referenced_numbers),
                            "depends_on": sorted(row["depends_on"]),
                        }
                    )
                if (
                    allowed_subject_terms
                    and reference.group("subject")
                    not in allowed_subject_terms
                ):
                    issues.append(
                        {
                            "rule": "claim_subject_consistency",
                            "claim_number": number,
                            "message": (
                                "从属权利要求正文的保护主题与 claim_type "
                                "不一致。"
                            ),
                        }
                    )
                added_text = row["text"][reference.end() :]
                added_units = _text_units(
                    re.sub(
                        r"^[，,；;。\s]*(?:其特征在于|其中)?[，,；;。\s]*",
                        "",
                        added_text,
                    )
                )
                if len(added_units) < 4:
                    issues.append(
                        {
                            "rule": "dependent_limitation_present",
                            "claim_number": number,
                            "message": (
                                "从属权利要求没有清楚写出相对引用项的新增限定。"
                            ),
                        }
                    )
        ambiguous_terms = sorted(
            set(AMBIGUOUS_CLAIM_LANGUAGE.findall(row["text"]))
        )
        if ambiguous_terms:
            warnings.append(
                {
                    "rule": "clarity_ambiguous_language",
                    "claim_number": number,
                    "message": (
                        "权利要求包含可能影响清楚性的相对、示例或偏好用语，"
                        "需人工确认是否删除或改为确定限定。"
                    ),
                    "terms": ambiguous_terms,
                }
            )
        unclear_antecedents = sorted(
            set(UNCLEAR_ANTECEDENT.findall(row["text"]))
        )
        if unclear_antecedents:
            warnings.append(
                {
                    "rule": "antecedent_basis_review",
                    "claim_number": number,
                    "message": (
                        "权利要求使用“该…”引用，需人工确认其在先基础唯一且"
                        "明确；必要时改用稳定术语和“所述”引用。"
                    ),
                    "terms": unclear_antecedents,
                }
            )
        for feature_id in row["feature_ids"]:
            planned_texts = plan_feature_texts.get(feature_id, [])
            if planned_texts and max(
                _text_overlap_score(text, row["text"])
                for text in planned_texts
            ) < 0.08:
                warnings.append(
                    {
                        "rule": "feature_text_alignment_review",
                        "claim_number": number,
                        "feature_id": feature_id,
                        "message": (
                            "该 feature_id 与权利要求正文的词面对应较弱，"
                            "需人工确认元数据和正文是否指向同一技术特征。"
                        ),
                    }
                )

    def has_cycle(start: int, current: int, visiting: set[int]) -> bool:
        if current in visiting:
            return True
        row = by_number.get(current)
        if not row:
            return False
        next_visiting = visiting | {current}
        return any(parent == start or has_cycle(start, parent, next_visiting) for parent in row["depends_on"])

    for number in by_number:
        if has_cycle(number, number, set()):
            issues.append({"rule": "no_dependency_cycle", "claim_number": number, "message": "权利要求依赖关系不得形成循环。"})

    def ancestor_features(number: int, seen: set[int] | None = None) -> set[str]:
        seen = set(seen or ())
        if number in seen or number not in by_number:
            return set()
        seen.add(number)
        row = by_number[number]
        features = set(row["feature_ids"])
        for parent in row["depends_on"]:
            features.update(ancestor_features(parent, seen))
        return features

    for row in claims:
        if not row["claim_type"].startswith("dependent_"):
            continue
        inherited = set()
        for parent in row["depends_on"]:
            inherited.update(ancestor_features(parent))
        if not (set(row["feature_ids"]) - inherited):
            warnings.append(
                {
                    "rule": "dependent_adds_feature",
                    "claim_number": row["claim_number"],
                    "message": (
                        "该从属权利要求未标注相对引用链新增的高层 feature_id；"
                        "可能是在细化已有特征，需人工核对其限定是否真实增加。"
                    ),
                }
            )

    for planned in claim_plan["independent_claims"]:
        claim_type = planned["claim_type"]
        required = {feature["feature_id"] for feature in planned["essential_features"]}
        matching = [
            row for row in independent
            if row["claim_type"] == f"independent_{claim_type}"
        ]
        if not matching:
            issues.append({"rule": "planned_independent_exists", "message": f"缺少规划的 {claim_type} 独立权利要求。"})
        elif not any(required.issubset(set(row["feature_ids"])) for row in matching):
            issues.append(
                {
                    "rule": "planned_essential_features",
                    "message": f"{claim_type} 独立权利要求未覆盖 Claim Plan 的全部必要技术特征。",
                }
            )

    for group in claim_plan["dependent_feature_groups"]:
        claim_type = group["parent_claim_type"]
        required = {feature["feature_id"] for feature in group["features"]}
        covered = {
            feature_id
            for row in claims
            if row["claim_type"] == f"dependent_{claim_type}"
            for feature_id in row["feature_ids"]
        }
        if not required.issubset(covered):
            issues.append(
                {
                    "rule": "planned_dependent_features",
                    "message": f"{claim_type} 从属权利要求未覆盖 Claim Plan 中的全部从属技术特征。",
                }
            )

    combined_claim_text = "\n".join(row["text"] for row in claims)
    quantitative_issues = check_quantitative_facts(combined_claim_text, source_materials)
    for issue in quantitative_issues:
        issues.append(
            {
                "rule": "no_unsupported_measured_result",
                "message": "权利要求正文包含无来源的定量技术事实。",
                "detail": issue,
            }
        )
    professional_blocking_rules = {
        "text_number_matches_metadata",
        "independent_no_reference_text",
        "reference_matches_dependency",
        "claim_subject_consistency",
        "dependent_limitation_present",
    }
    professional_advisory_rules = {
        "dependent_adds_feature",
        "clarity_ambiguous_language",
        "feature_text_alignment_review",
        "antecedent_basis_review",
    }
    professional_issue_count = sum(
        issue.get("rule") in professional_blocking_rules
        for issue in issues
    )
    professional_warning_count = sum(
        warning.get("rule") in professional_advisory_rules
        for warning in warnings
    )
    return {
        "schema_version": "claim_validation_v2",
        "artifact_metadata": {
            "search_mode": claims_payload.get("artifact_metadata", {}).get("search_mode"),
            "search_status": claims_payload.get("artifact_metadata", {}).get("search_status"),
            "search_limitation": claims_payload.get("artifact_metadata", {}).get("search_limitation"),
        },
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "professional_quality": {
            "status": (
                "failed"
                if professional_issue_count
                else (
                    "passed_with_advisories"
                    if professional_warning_count
                    else "passed"
                )
            ),
            "blocking_issue_count": professional_issue_count,
            "advisory_warning_count": professional_warning_count,
            "automatic_legal_conclusion": False,
            "dimensions_checked": [
                "reference_metadata_consistency",
                "text_number_metadata_consistency",
                "claim_subject_consistency",
                "dependent_limitation_presence",
                "clarity_language_advisory",
                "antecedent_basis_advisory",
                "feature_text_alignment_advisory",
            ],
        },
        "rules_checked": [
            "continuous_numbering",
            "independent_no_dependency",
            "dependency_exists",
            "no_dependency_cycle",
            "dependency_precedes_claim",
            "nonempty_text",
            "dependent_adds_feature",
            "planned_essential_features",
            "planned_dependent_features",
            "planned_claim_type",
            "known_feature_ids",
            "no_publication_or_link",
            "no_internal_feature_id_in_text",
            "no_legal_conclusion",
            "no_unsupported_measured_result",
            "text_number_matches_metadata",
            "independent_no_reference_text",
            "reference_matches_dependency",
            "claim_subject_consistency",
            "dependent_limitation_present",
            "clarity_ambiguous_language",
            "antecedent_basis_review",
            "feature_text_alignment_review",
        ],
    }


def render_claims_markdown(claims_payload: dict[str, Any], title: str) -> str:
    notice = claims_payload["artifact_metadata"]["notice"]
    lines = [
        "# 权利要求草案",
        "",
        f"**案件名称**：{title}",
        "",
        f"> {notice}",
        "",
    ]
    if claims_payload["artifact_metadata"]["fixture_based"]:
        lines.extend(
            [
                "> 本次运行使用 Fixture 演示检索数据；该状态仅用于验证流程，未作为权利要求正文中的专利事实或法律结论。",
                "",
            ]
        )
    search_limitation = claims_payload["artifact_metadata"].get("search_limitation")
    if search_limitation:
        lines.extend([f"> 检索限制：{search_limitation}", ""])
    for row in claims_payload["claims"]:
        body = row["text"].strip()
        if re.match(r"^\d+\s*[.．、]", body):
            lines.append(body)
        else:
            lines.append(f"{row['claim_number']}. {body}")
    return "\n\n".join(lines).rstrip() + "\n"
