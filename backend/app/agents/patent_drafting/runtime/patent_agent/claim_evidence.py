from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from patent_agent.utils import read_json, sha256_file, write_json


_SEARCH_CHAR = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
DISCLOSURE_REVIEW_SECTIONS = (
    ("technical_problem", "要解决的技术问题"),
    ("technical_solution", "技术方案"),
    ("beneficial_effects", "有益效果"),
    ("embodiments", "具体实施方式"),
)


def _search_units(text: str) -> set[str]:
    compact = "".join(_SEARCH_CHAR.findall(text)).lower()
    if not compact:
        return set()
    if len(compact) == 1:
        return {compact}
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _candidate_score(query: str, paragraph: str) -> float:
    query_units = _search_units(query)
    if not query_units:
        return 0.0
    paragraph_units = _search_units(paragraph)
    if not paragraph_units:
        return 0.0
    return len(query_units & paragraph_units) / len(query_units)


def _source_paragraphs(run_path: Path) -> list[dict[str, Any]]:
    manifest = read_json(run_path / "parsed" / "manifest.json")
    paragraphs: list[dict[str, Any]] = []
    for row in manifest:
        output_path = row.get("output_path")
        if row.get("parse_status") != "parsed" or not output_path:
            continue
        parsed_path = run_path / str(output_path)
        source_path = str(row["source_path"])
        source_sha256 = str(row["sha256"])
        lines = parsed_path.read_text(encoding="utf-8").splitlines()
        start: int | None = None
        buffer: list[str] = []
        for line_number, line in enumerate([*lines, ""], 1):
            if line.strip():
                if start is None:
                    start = line_number
                buffer.append(line.strip())
                continue
            if start is None:
                continue
            text = " ".join(buffer)
            paragraph_identity = (
                f"{source_path}\n{source_sha256}\n{start}\n"
                f"{line_number - 1}\n{text}"
            )
            paragraphs.append(
                {
                    "paragraph_id": (
                        "P-"
                        + hashlib.sha256(
                            paragraph_identity.encode("utf-8")
                        ).hexdigest()[:16]
                    ),
                    "source_path": source_path,
                    "source_sha256": source_sha256,
                    "parsed_output_path": str(output_path),
                    "parsed_line_start": start,
                    "parsed_line_end": line_number - 1,
                    "text": text,
                }
            )
            start = None
            buffer = []
    return paragraphs


def _candidate_evidence(
    query: str,
    paragraphs: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    ranked = sorted(
        (
            (_candidate_score(query, paragraph["text"]), paragraph)
            for paragraph in paragraphs
        ),
        key=lambda item: (
            -item[0],
            item[1]["source_path"],
            item[1]["parsed_line_start"],
        ),
    )
    candidates: list[dict[str, Any]] = []
    for score, paragraph in ranked:
        if score <= 0:
            continue
        candidates.append(
            {
                "candidate_only": True,
                "paragraph_id": paragraph["paragraph_id"],
                "source_path": paragraph["source_path"],
                "source_sha256": paragraph["source_sha256"],
                "parsed_output_path": paragraph["parsed_output_path"],
                "parsed_line_start": paragraph["parsed_line_start"],
                "parsed_line_end": paragraph["parsed_line_end"],
                "excerpt": paragraph["text"][:800],
                "lexical_match_score": round(score, 4),
            }
        )
        if len(candidates) == limit:
            break
    return candidates


def build_disclosure_evidence_review(
    *,
    run_path: Path,
    input_sha256: str,
    disclosure_sections: dict[str, Any],
) -> dict[str, Any]:
    paragraphs = _source_paragraphs(run_path)
    entries: list[dict[str, Any]] = []
    missing_locator_count = 0
    for section_key, section_title in DISCLOSURE_REVIEW_SECTIONS:
        section_text = str(disclosure_sections.get(section_key) or "").strip()
        candidates = _candidate_evidence(section_text, paragraphs)
        if not candidates:
            missing_locator_count += 1
        entries.append(
            {
                "section_key": section_key,
                "section_title": section_title,
                "section_text_sha256": hashlib.sha256(
                    section_text.encode("utf-8")
                ).hexdigest(),
                "candidate_evidence": candidates,
                "review_verdict": "REVIEW_REQUIRED",
                "review_note": "",
            }
        )
    disclosure_path = run_path / "artifacts" / "disclosure_sections.json"
    return {
        "schema_version": "disclosure_evidence_review_v1",
        "input_sha256": input_sha256,
        "disclosure_sections_sha256": sha256_file(disclosure_path),
        "release_status": "human_review_required",
        "automatic_support_decision": False,
        "candidate_locator_method": (
            "deterministic_character_bigram_overlap_v1"
        ),
        "candidate_locator_notice": (
            "候选摘录仅用于缩短人工定位时间，词面匹配分数不代表语义支撑结论。"
            "必须由人工核对章节表述是否完整、准确且未超出原始材料。"
        ),
        "section_count": len(entries),
        "missing_candidate_locator_count": missing_locator_count,
        "entries": entries,
    }


def render_disclosure_evidence_review_markdown(
    review: dict[str, Any],
) -> str:
    lines = [
        "# 交底书章节—材料定位审核表",
        "",
        "> 候选摘录只用于定位，不代表自动判定章节已经获得材料支撑。",
        "",
        f"- 输入快照 SHA-256：`{review['input_sha256']}`",
        (
            "- 结构化交底书 SHA-256："
            f"`{review['disclosure_sections_sha256']}`"
        ),
        f"- 核心章节数：`{review['section_count']}`",
        (
            "- 未定位到候选摘录："
            f"`{review['missing_candidate_locator_count']}`"
        ),
        f"- 当前状态：`{review['release_status']}`",
        "",
    ]
    for entry in review["entries"]:
        lines.extend(
            [
                f"## {entry['section_title']}",
                "",
                f"- 字段：`{entry['section_key']}`",
                f"- 章节文本 SHA-256：`{entry['section_text_sha256']}`",
                "- 人工结论：`REVIEW_REQUIRED`",
                "- 人工备注：",
                "",
                "### 候选材料位置",
                "",
            ]
        )
        if not entry["candidate_evidence"]:
            lines.extend(
                ["- 未定位到候选摘录，需人工直接检查原材料。", ""]
            )
            continue
        for candidate in entry["candidate_evidence"]:
            lines.extend(
                [
                    (
                        f"- 段落 `{candidate['paragraph_id']}`；源文件 "
                        f"`{candidate['source_path']}`；解析文本 "
                        f"`{candidate['parsed_output_path']}:"
                        f"{candidate['parsed_line_start']}`；词面匹配 "
                        f"`{candidate['lexical_match_score']}`；源文件 "
                        f"SHA-256 `{candidate['source_sha256']}`"
                    ),
                    f"  - {candidate['excerpt']}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_disclosure_evidence_review(
    *,
    run_path: Path,
    input_sha256: str,
    disclosure_sections: dict[str, Any],
) -> dict[str, Any]:
    review = build_disclosure_evidence_review(
        run_path=run_path,
        input_sha256=input_sha256,
        disclosure_sections=disclosure_sections,
    )
    json_path = run_path / "artifacts" / "disclosure_evidence_review.json"
    markdown_path = (
        run_path / "artifacts" / "disclosure_evidence_review.md"
    )
    write_json(json_path, review)
    markdown_path.write_text(
        render_disclosure_evidence_review_markdown(review),
        encoding="utf-8",
    )
    return review


def _feature_plan_texts(claim_plan: dict[str, Any]) -> dict[str, list[str]]:
    texts: dict[str, list[str]] = {}
    for independent in claim_plan["independent_claims"]:
        for feature in independent["essential_features"]:
            texts.setdefault(feature["feature_id"], []).append(feature["text"])
    for group in claim_plan["dependent_feature_groups"]:
        for feature in group["features"]:
            texts.setdefault(feature["feature_id"], []).append(feature["text"])
    return {
        feature_id: list(dict.fromkeys(values))
        for feature_id, values in texts.items()
    }


def build_claim_evidence_review(
    *,
    run_path: Path,
    input_sha256: str,
    claims: dict[str, Any],
    claim_plan: dict[str, Any],
    difference_analysis: dict[str, Any],
) -> dict[str, Any]:
    paragraphs = _source_paragraphs(run_path)
    plan_texts = _feature_plan_texts(claim_plan)
    difference_features = {
        row["feature_id"]: row
        for row in difference_analysis["target_features"]
    }
    feature_ids = list(
        dict.fromkeys(
            feature_id
            for claim in claims["claims"]
            for feature_id in claim["feature_ids"]
        )
    )
    entries: list[dict[str, Any]] = []
    missing_locator_count = 0
    for feature_id in feature_ids:
        feature = difference_features.get(feature_id, {})
        claim_rows = [
            claim
            for claim in claims["claims"]
            if feature_id in claim["feature_ids"]
        ]
        query_parts = [
            str(feature.get("feature_text") or ""),
            *plan_texts.get(feature_id, []),
        ]
        query = " ".join(part for part in query_parts if part)
        candidates = _candidate_evidence(query, paragraphs)
        if not candidates:
            missing_locator_count += 1
        entries.append(
            {
                "feature_id": feature_id,
                "claim_numbers": [row["claim_number"] for row in claim_rows],
                "claim_texts": [row["text"] for row in claim_rows],
                "claim_plan_texts": plan_texts.get(feature_id, []),
                "difference_feature_text": feature.get("feature_text"),
                "model_source_summary": feature.get("source_summary"),
                "candidate_evidence": candidates,
                "review_verdict": "REVIEW_REQUIRED",
                "review_note": "",
            }
        )
    claims_path = run_path / "artifacts" / "claims.json"
    return {
        "schema_version": "claim_evidence_review_v1",
        "input_sha256": input_sha256,
        "claims_sha256": sha256_file(claims_path),
        "release_status": "human_review_required",
        "automatic_support_decision": False,
        "candidate_locator_method": "deterministic_character_bigram_overlap_v1",
        "candidate_locator_notice": (
            "候选摘录仅用于缩短人工定位时间，词面匹配分数不代表语义支撑结论。"
            "必须由人工逐项判定 SUPPORTED、PARTIAL 或 UNSUPPORTED。"
        ),
        "feature_count": len(entries),
        "missing_candidate_locator_count": missing_locator_count,
        "entries": entries,
    }


def render_claim_evidence_review_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Claim–Evidence 人工审核表",
        "",
        "> 候选摘录只用于定位，不代表自动判定材料已经支撑。正式放行前必须逐项人工复核。",
        "",
        f"- 输入快照 SHA-256：`{review['input_sha256']}`",
        f"- Claims SHA-256：`{review['claims_sha256']}`",
        f"- 特征数：`{review['feature_count']}`",
        f"- 未定位到候选摘录：`{review['missing_candidate_locator_count']}`",
        f"- 当前状态：`{review['release_status']}`",
        "",
    ]
    for entry in review["entries"]:
        numbers = "、".join(str(number) for number in entry["claim_numbers"])
        lines.extend(
            [
                f"## {entry['feature_id']}",
                "",
                f"- 涉及权利要求：{numbers}",
                f"- 差异特征：{entry.get('difference_feature_text') or '未提供'}",
                f"- Claim Plan 表述：{'；'.join(entry['claim_plan_texts']) or '未提供'}",
                "- 人工结论：`REVIEW_REQUIRED`",
                "- 人工备注：",
                "",
                "### 候选材料位置",
                "",
            ]
        )
        if not entry["candidate_evidence"]:
            lines.extend(["- 未定位到候选摘录，需人工直接检查原材料。", ""])
            continue
        for candidate in entry["candidate_evidence"]:
            lines.extend(
                [
                    (
                        f"- 段落 `{candidate['paragraph_id']}`；源文件 "
                        f"`{candidate['source_path']}`；解析文本 "
                        f"`{candidate['parsed_output_path']}:"
                        f"{candidate['parsed_line_start']}`；"
                        f"词面匹配 `{candidate['lexical_match_score']}`，"
                        f"源文件 SHA-256 `{candidate['source_sha256']}`"
                    ),
                    f"  - {candidate['excerpt']}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_claim_evidence_review(
    *,
    run_path: Path,
    input_sha256: str,
    claims: dict[str, Any],
    claim_plan: dict[str, Any],
    difference_analysis: dict[str, Any],
) -> dict[str, Any]:
    review = build_claim_evidence_review(
        run_path=run_path,
        input_sha256=input_sha256,
        claims=claims,
        claim_plan=claim_plan,
        difference_analysis=difference_analysis,
    )
    json_path = run_path / "artifacts" / "claim_evidence_review.json"
    markdown_path = run_path / "artifacts" / "claim_evidence_review.md"
    write_json(json_path, review)
    markdown_path.write_text(
        render_claim_evidence_review_markdown(review),
        encoding="utf-8",
    )
    return review
