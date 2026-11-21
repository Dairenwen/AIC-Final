from __future__ import annotations

import re
from typing import Any, Dict, List

from academic_compliance_agent.app.tools.common import first_rule, make_location, make_risk


class PaperNormCheckerTool:
    """Check manuscript structure and academic writing conventions."""

    module = "paper_norm"

    def run(self, parsed_document: Dict[str, Any], rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        risks: List[Dict[str, Any]] = []
        sections = parsed_document.get("sections", [])
        section_titles = " ".join(section.get("title", "") for section in sections)
        abstract = parsed_document.get("abstract", "")
        text = parsed_document.get("body_text", "")

        if not parsed_document.get("title"):
            rule = first_rule(rules, self.module, "PAPER_NORM_001")
            risks.append(
                make_risk(
                    risk_type="TITLE_MISSING",
                    module=self.module,
                    severity="高",
                    title="论文标题缺失",
                    evidence="未能从论文开头解析出明确标题。",
                    suggestion="请补充能够概括研究对象、方法或核心问题的论文标题。",
                    rule=rule,
                    confidence=0.84,
                )
            )

        if not abstract:
            rule = first_rule(rules, self.module, "PAPER_NORM_002")
            risks.append(
                make_risk(
                    risk_type="ABSTRACT_MISSING",
                    module=self.module,
                    severity="高",
                    title="摘要缺失",
                    evidence="未发现摘要或 Abstract 章节。",
                    suggestion="请补充摘要，概括研究目的、方法、主要结果和结论。",
                    rule=rule,
                    confidence=0.88,
                )
            )
        else:
            required_abstract_clues = ["目的", "方法", "结果", "结论"]
            missing = [item for item in required_abstract_clues if item not in abstract]
            if len(missing) >= 3 and len(abstract) < 180:
                rule = first_rule(rules, self.module, "PAPER_NORM_002")
                risks.append(
                    make_risk(
                        risk_type="ABSTRACT_INCOMPLETE",
                        module=self.module,
                        severity="中",
                        title="摘要信息不完整",
                        evidence=f"摘要较短，且缺少 {', '.join(missing)} 等要素。",
                        suggestion="建议在摘要中补充研究目的、方法、核心结果和结论。",
                        rule=rule,
                        location=make_location("摘要", abstract[:120]),
                        confidence=0.72,
                    )
                )

        if not parsed_document.get("keywords"):
            rule = first_rule(rules, self.module, "PAPER_NORM_003")
            risks.append(
                make_risk(
                    risk_type="KEYWORDS_MISSING",
                    module=self.module,
                    severity="中",
                    title="关键词缺失",
                    evidence="未发现关键词或 Keywords 字段。",
                    suggestion="请补充 3-5 个能够反映研究主题、方法和应用领域的关键词。",
                    rule=rule,
                    confidence=0.82,
                )
            )

        required_sections = {
            "引言": ["引言", "绪论", "Introduction"],
            "方法": ["方法", "研究方法", "材料与方法", "Methods", "Methodology"],
            "结果": ["结果", "实验", "Results", "Experiments"],
            "结论": ["结论", "Conclusion"],
        }
        for label, aliases in required_sections.items():
            if not any(alias.lower() in section_titles.lower() for alias in aliases):
                rule = first_rule(rules, self.module, "PAPER_NORM_004")
                risks.append(
                    make_risk(
                        risk_type="PAPER_STRUCTURE_INCOMPLETE",
                        module=self.module,
                        severity="中",
                        title=f"{label}部分缺失或不明确",
                        evidence=f"未在章节标题中发现明确的“{label}”相关部分。",
                        suggestion=f"建议补充或明确“{label}”章节，使论文结构更加完整。",
                        rule=rule,
                        confidence=0.75,
                    )
                )

        overclaim_patterns = ["首次", "完全解决", "彻底解决", "绝对", "100%", "显著优于所有", "无任何"]
        for pattern in overclaim_patterns:
            if pattern in text:
                rule = first_rule(rules, self.module, "PAPER_NORM_005")
                risks.append(
                    make_risk(
                        risk_type="OVERCLAIMING_EXPRESSION",
                        module=self.module,
                        severity="低",
                        title="可能存在过度绝对化表述",
                        evidence=f"正文中出现“{pattern}”等强断言表述。",
                        suggestion="建议核查该表述是否有充分实验或引用支撑，必要时改为更审慎的学术表达。",
                        rule=rule,
                        location=make_location("全文", pattern),
                        confidence=0.66,
                    )
                )
                break

        if re.search(r"(学术诚信|原创性声明)", text) is None:
            rule = first_rule(rules, self.module, "PAPER_NORM_006")
            risks.append(
                make_risk(
                    risk_type="ACADEMIC_INTEGRITY_STATEMENT_MISSING",
                    module=self.module,
                    severity="低",
                    title="学术诚信声明缺失",
                    evidence="未发现学术诚信、原创性或相关声明。",
                    suggestion="如学校或投稿场景要求，请补充学术诚信或原创性声明。",
                    rule=rule,
                    confidence=0.7,
                )
            )

        return risks
