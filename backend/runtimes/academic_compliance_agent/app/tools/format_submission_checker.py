from __future__ import annotations

from typing import Any, Dict, List

from academic_compliance_agent.app.tools.common import first_rule, make_location, make_risk


class FormatSubmissionCheckerTool:
    """Check formatting and submission-oriented requirements."""

    module = "format_submission"

    def run(
        self,
        parsed_document: Dict[str, Any],
        files: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        risks: List[Dict[str, Any]] = []
        abstract = parsed_document.get("abstract", "")
        keywords = parsed_document.get("keywords", [])
        references = parsed_document.get("references", [])
        figures = parsed_document.get("figures", [])
        tables = parsed_document.get("tables", [])

        if abstract and len(abstract) > 500:
            rule = first_rule(rules, self.module, "FORMAT_001")
            risks.append(
                make_risk(
                    risk_type="ABSTRACT_TOO_LONG",
                    module=self.module,
                    severity="中",
                    title="摘要长度可能超出规范",
                    evidence=f"当前摘要长度约为 {len(abstract)} 个字符。",
                    suggestion="请根据目标学校或期刊要求压缩摘要，保留研究目的、方法、结果和结论。",
                    rule=rule,
                    location=make_location("摘要", abstract[:120]),
                    confidence=0.76,
                )
            )

        if keywords and not (3 <= len(keywords) <= 8):
            rule = first_rule(rules, self.module, "FORMAT_002")
            risks.append(
                make_risk(
                    risk_type="KEYWORD_COUNT_OUT_OF_RANGE",
                    module=self.module,
                    severity="低",
                    title="关键词数量可能不符合规范",
                    evidence=f"当前关键词数量为 {len(keywords)}。",
                    suggestion="建议将关键词数量控制在 3-8 个，并优先使用规范学术术语。",
                    rule=rule,
                    confidence=0.7,
                )
            )

        if not references:
            rule = first_rule(rules, self.module, "FORMAT_003")
            risks.append(
                make_risk(
                    risk_type="REFERENCE_SECTION_REQUIRED",
                    module=self.module,
                    severity="高",
                    title="参考文献章节缺失",
                    evidence="未解析到参考文献章节。",
                    suggestion="请补充参考文献章节，并按目标规范统一格式。",
                    rule=rule,
                    confidence=0.84,
                )
            )

        for reference in references:
            text = reference.get("text", "")
            if text and not any(char.isdigit() for char in text):
                rule = first_rule(rules, self.module, "FORMAT_004")
                risks.append(
                    make_risk(
                        risk_type="REFERENCE_YEAR_MISSING",
                        module=self.module,
                        severity="低",
                        title="参考文献可能缺少年份信息",
                        evidence=f"参考文献未发现年份数字：{text[:120]}",
                        suggestion="请补充或核对参考文献的出版年份。",
                        rule=rule,
                        location=make_location("参考文献", text[:120]),
                        confidence=0.65,
                    )
                )

        if figures or tables:
            captions = [item.get("caption", "") for item in figures + tables]
            if not all(captions):
                rule = first_rule(rules, self.module, "FORMAT_005")
                risks.append(
                    make_risk(
                        risk_type="FIGURE_TABLE_CAPTION_INCOMPLETE",
                        module=self.module,
                        severity="中",
                        title="图表标题不完整",
                        evidence="部分图表未解析到完整标题。",
                        suggestion="请检查所有图表是否具有清晰标题、编号、单位和必要说明。",
                        rule=rule,
                        confidence=0.78,
                    )
                )

        if any(" " in item.get("path", "") for item in files):
            rule = first_rule(rules, self.module, "FORMAT_006")
            risks.append(
                make_risk(
                    risk_type="FILE_NAME_CONTAINS_SPACES",
                    module=self.module,
                    severity="低",
                    title="文件命名可能不符合投稿规范",
                    evidence="上传文件路径或文件名中包含空格。",
                    suggestion="建议按目标系统要求重命名文件，避免空格、特殊符号或过长文件名。",
                    rule=rule,
                    confidence=0.64,
                )
            )

        return risks
