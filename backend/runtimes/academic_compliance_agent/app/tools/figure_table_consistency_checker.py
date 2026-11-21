from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from academic_compliance_agent.app.tools.common import first_rule, make_location, make_risk


class FigureTableConsistencyTool:
    """Check consistency of figures, tables, captions, and in-text mentions."""

    module = "figure_table"

    def run(self, parsed_document: Dict[str, Any], rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        text = parsed_document.get("body_text", "")
        figures = parsed_document.get("figures", [])
        tables = parsed_document.get("tables", [])
        risks: List[Dict[str, Any]] = []

        risks.extend(self._check_kind("图", "figure", figures, text, rules))
        risks.extend(self._check_kind("表", "table", tables, text, rules))
        return risks

    def _check_kind(
        self,
        cn_name: str,
        kind: str,
        items: List[Dict[str, Any]],
        text: str,
        rules: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        risks: List[Dict[str, Any]] = []
        caption_numbers: Set[int] = {int(item["number"]) for item in items if item.get("number") is not None}
        if kind == "figure":
            mention_numbers = {int(item) for item in re.findall(r"(?:图|Figure|Fig\.)\s*([0-9]+)", text, re.IGNORECASE)}
        else:
            mention_numbers = {int(item) for item in re.findall(r"(?:表|Table)\s*([0-9]+)", text, re.IGNORECASE)}

        for number in sorted(mention_numbers - caption_numbers):
            rule = first_rule(rules, self.module, "FIG_TABLE_001")
            risks.append(
                make_risk(
                    risk_type=f"{kind.upper()}_MENTION_WITHOUT_CAPTION",
                    module=self.module,
                    severity="中",
                    title=f"正文提到{cn_name}{number}但未找到对应{cn_name}题",
                    evidence=f"正文出现“{cn_name}{number}”，但未解析到对应图表标题。",
                    suggestion=f"请补充{cn_name}{number}及其标题，或修改正文中的图表编号。",
                    rule=rule,
                    location=make_location("正文", f"{cn_name}{number}"),
                    confidence=0.84,
                )
            )

        for number in sorted(caption_numbers - mention_numbers):
            rule = first_rule(rules, self.module, "FIG_TABLE_002")
            risks.append(
                make_risk(
                    risk_type=f"{kind.upper()}_NOT_MENTIONED",
                    module=self.module,
                    severity="中",
                    title=f"{cn_name}{number}未在正文中引用",
                    evidence=f"检测到{cn_name}{number}，但正文未找到对应引用。",
                    suggestion=f"请在正文首次讨论该结果处引用{cn_name}{number}，或确认该图表是否应删除。",
                    rule=rule,
                    location=make_location(f"{cn_name}题", f"{cn_name}{number}"),
                    confidence=0.82,
                )
            )

        expected = set(range(1, max(caption_numbers) + 1)) if caption_numbers else set()
        missing_numbers = sorted(expected - caption_numbers)
        if missing_numbers:
            rule = first_rule(rules, self.module, "FIG_TABLE_003")
            risks.append(
                make_risk(
                    risk_type=f"{kind.upper()}_NUMBER_SEQUENCE_GAP",
                    module=self.module,
                    severity="中",
                    title=f"{cn_name}编号不连续",
                    evidence=f"{cn_name}编号缺少：{missing_numbers}",
                    suggestion=f"请检查{cn_name}编号顺序，保证编号连续且与正文引用一致。",
                    rule=rule,
                    confidence=0.86,
                )
            )

        for item in items:
            caption = item.get("caption", "")
            if len(caption) < 4:
                rule = first_rule(rules, self.module, "FIG_TABLE_004")
                risks.append(
                    make_risk(
                        risk_type=f"{kind.upper()}_CAPTION_TOO_SHORT",
                        module=self.module,
                        severity="低",
                        title=f"{cn_name}{item.get('number')}标题说明过短",
                        evidence=f"{cn_name}{item.get('number')}的标题为：{caption or '(空)'}",
                        suggestion="建议补充图表标题，说明对象、指标、单位或实验条件。",
                        rule=rule,
                        location=make_location(f"{cn_name}题", caption),
                        confidence=0.72,
                    )
                )
        return risks
