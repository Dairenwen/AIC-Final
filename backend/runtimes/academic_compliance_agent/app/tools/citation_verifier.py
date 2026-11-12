from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from academic_compliance_agent.app.tools.common import first_rule, make_location, make_risk


class CitationVerifierTool:
    """Verify consistency between in-text citations and references."""

    module = "citation"

    def run(self, parsed_document: Dict[str, Any], rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        risks: List[Dict[str, Any]] = []
        citations = parsed_document.get("citations", [])
        references = parsed_document.get("references", [])
        citation_numbers: Set[int] = {item["number"] for item in citations if "number" in item}
        reference_numbers: Set[int] = {item["number"] for item in references if item.get("number") is not None}

        if citation_numbers and not references:
            rule = first_rule(rules, self.module, "CITATION_001")
            risks.append(
                make_risk(
                    risk_type="REFERENCES_SECTION_MISSING",
                    module=self.module,
                    severity="高",
                    title="存在正文引用但缺少参考文献列表",
                    evidence=f"检测到正文引用编号 {sorted(citation_numbers)}，但未解析到参考文献列表。",
                    suggestion="请补充参考文献章节，并保证正文引用编号与参考文献编号一致。",
                    rule=rule,
                    confidence=0.88,
                )
            )

        if references and not citation_numbers and not any(item.get("style") == "author_year" for item in citations):
            rule = first_rule(rules, self.module, "CITATION_002")
            risks.append(
                make_risk(
                    risk_type="NO_IN_TEXT_CITATIONS",
                    module=self.module,
                    severity="中",
                    title="参考文献未在正文中形成可识别引用",
                    evidence="检测到参考文献列表，但未发现正文引用标记。",
                    suggestion="请检查正文是否按目标格式引用了参考文献。",
                    rule=rule,
                    confidence=0.78,
                )
            )

        for number in sorted(citation_numbers - reference_numbers):
            rule = first_rule(rules, self.module, "CITATION_003")
            risks.append(
                make_risk(
                    risk_type="CITED_REFERENCE_MISSING",
                    module=self.module,
                    severity="高",
                    title="正文引用缺少对应参考文献",
                    evidence=f"正文引用了 [{number}]，但参考文献列表中未找到对应条目。",
                    suggestion=f"请补充编号为 [{number}] 的参考文献，或修改正文引用编号。",
                    rule=rule,
                    location=make_location("正文引用", f"[{number}]"),
                    confidence=0.92,
                )
            )

        for number in sorted(reference_numbers - citation_numbers):
            rule = first_rule(rules, self.module, "CITATION_004")
            risks.append(
                make_risk(
                    risk_type="REFERENCE_NOT_CITED",
                    module=self.module,
                    severity="中",
                    title="参考文献未在正文引用",
                    evidence=f"参考文献列表中的第 {number} 条未在正文中找到对应引用。",
                    suggestion=f"请确认第 {number} 条文献是否需要保留；如保留，应在相关论述处添加引用。",
                    rule=rule,
                    location=make_location("参考文献", f"[{number}]"),
                    confidence=0.86,
                )
            )

        seen_text = set()
        for reference in references:
            text = reference.get("text", "")
            normalized = re.sub(r"\s+", " ", text.lower()).strip()
            if normalized in seen_text:
                rule = first_rule(rules, self.module, "CITATION_005")
                risks.append(
                    make_risk(
                        risk_type="DUPLICATE_REFERENCE",
                        module=self.module,
                        severity="中",
                        title="参考文献重复著录",
                        evidence=f"检测到重复参考文献：{text[:120]}",
                        suggestion="请删除重复著录或合并为一条规范参考文献。",
                        rule=rule,
                        location=make_location("参考文献", text[:120]),
                        confidence=0.88,
                    )
                )
            seen_text.add(normalized)

            if re.search(r"(待补充|TODO|xxx|未知|unknown)", text, re.IGNORECASE):
                rule = first_rule(rules, self.module, "CITATION_006")
                risks.append(
                    make_risk(
                        risk_type="SUSPICIOUS_REFERENCE_PLACEHOLDER",
                        module=self.module,
                        severity="极高",
                        title="参考文献信息疑似占位或不完整",
                        evidence=f"参考文献中出现占位内容：{text[:120]}",
                        suggestion="请核验该参考文献真实性，并补全作者、题名、来源、年份、DOI 等字段。",
                        rule=rule,
                        location=make_location("参考文献", text[:120]),
                        confidence=0.9,
                    )
                )

        return risks
