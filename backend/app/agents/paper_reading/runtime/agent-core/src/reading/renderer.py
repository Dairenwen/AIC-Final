from __future__ import annotations

from schemas.models import ReadingResult


SECTION_LABELS = (
    ("研究问题", "research_questions"),
    ("方法结构", "method_structure"),
    ("公式与图表", "key_equations_and_figures"),
    ("实验结果", "experiment_findings"),
    ("创新点", "innovations"),
    ("局限性", "limitations"),
)


def render_reading_markdown(result: ReadingResult) -> str:
    claims = {claim.claim_id: claim for claim in result.claims}
    evidence = {item.evidence_id: item for item in result.evidence}
    info = result.basic_information
    lines = [
        f"# {info.title}",
        "",
        f"- 作者：{', '.join(info.authors)}",
        f"- 年份：{info.year if info.year is not None else '未知'}",
        "",
    ]
    source_labels = {
        "AUTHOR_STATED": "作者自述",
        "EVIDENCE_DERIVED": "证据归纳",
        "AGENT_INFERRED": "Agent 推断",
        "CROSS_PAPER_ASSESSED": "跨论文判断",
    }

    for label, field_name in SECTION_LABELS:
        lines.extend((f"## {label}", ""))
        claim_ids = getattr(result, field_name)
        if not claim_ids:
            lines.extend(("暂无可靠结论。", ""))
            continue
        for claim_id in claim_ids:
            claim = claims[claim_id]
            references = []
            for evidence_id in claim.evidence_ids:
                item = evidence[evidence_id]
                section = " / ".join(item.section_path)
                references.append(f"p.{item.page_number} {section} ({item.object_id})")
            lines.append(f"- **{source_labels[claim.claim_source]}**：{claim.content}")
            lines.append(f"  - 依据：{'; '.join(references)}")
        lines.append("")

    if result.warnings:
        lines.extend(("## 提示", ""))
        for warning in result.warnings:
            lines.append(f"- `{warning.warning_code}`：{warning.message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
