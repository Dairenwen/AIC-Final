from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from academic_compliance_agent.app.services.llm import OpenAICompatibleLLMClient
from academic_compliance_agent.app.tools.common import SEVERITY_LEVELS


MODULE_NAMES = {
    "paper_norm": "学术论文规范检查",
    "citation": "引用与参考文献核验",
    "figure_table": "图表一致性检查",
    "format_submission": "格式与投稿规范检查",
}


class ReportWriterTool:
    """Render a Markdown report and a structured output object."""

    def run(self, state: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
        risks = state.get("risks", [])
        suggestions = state.get("suggestions", [])
        compliance_summary = state.get("compliance_summary", {})
        module_check_results = state.get("module_check_results", {})
        memory = state.get("memory", {})
        short_term_memory = state.get("short_term_memory", {})
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report = self._render_markdown(
            state,
            risks,
            suggestions,
            summary,
            now,
            compliance_summary,
            module_check_results,
        )
        structured = {
            "task_id": state.get("task_id", ""),
            "task_type": state.get("task_type", ""),
            "created_at": now,
            "summary": summary,
            "compliance_summary": compliance_summary,
            "module_check_results": module_check_results,
            "memory": memory,
            "short_term_memory": short_term_memory,
            "risks": risks,
            "suggestions": suggestions,
            "rule_versions": sorted({rule.get("rule_version", "") for rule in state.get("retrieved_rules", []) if rule.get("rule_version")}),
            "model_versions": [self._model_version()],
        }
        return {"final_report": report, "structured_output": structured}

    def _model_version(self) -> str:
        client = OpenAICompatibleLLMClient()
        if client.is_available():
            return f"openai_compatible:{client.model}"
        return "rule_based_fallback"

    def _render_markdown(
        self,
        state: Dict[str, Any],
        risks: List[Dict[str, Any]],
        suggestions: List[Any],
        summary: Dict[str, Any],
        now: str,
        compliance_summary: Dict[str, Any],
        module_check_results: Dict[str, Dict[str, Any]],
    ) -> str:
        lines: List[str] = []
        lines.append("# 学术合规性校验报告")
        lines.append("")
        lines.append("## 一、校验概况")
        lines.append("")
        lines.append(f"- 任务 ID：{state.get('task_id', '')}")
        lines.append(f"- 用户 ID：{state.get('user_id', '')}")
        lines.append(f"- 会话 ID：{state.get('thread_id', '')}")
        lines.append(f"- 任务类型：{state.get('task_type', '')}")
        lines.append(f"- 检测时间：{now}")
        lines.append(f"- 短期记忆：{'已恢复上一轮状态' if state.get('short_term_memory', {}).get('enabled') else '未恢复历史状态'}")
        lines.append(f"- 长期记忆：{'已启用' if state.get('memory', {}).get('enabled') else '未启用'}")
        lines.append(f"- 学术合规性得分：{compliance_summary.get('compliance_score', 0)} / 100")
        lines.append(f"- 总体修改优先级：{summary.get('overall_level', '极低')}")
        lines.append(f"- 风险总数：{summary.get('risk_count', 0)}")
        lines.append("")
        lines.append("## 二、学术合规性总结")
        lines.append("")
        if compliance_summary.get("summary"):
            lines.append(str(compliance_summary.get("summary")))
            lines.append("")
        lines.append("### 学术合规性的优秀点")
        lines.append("")
        for point in compliance_summary.get("excellent_points", []) or ["暂无明显优秀点。"]:
            lines.append(f"- {point}")
        lines.append("")
        lines.append("### 学术合规性的修改建议")
        lines.append("")
        for suggestion in suggestions or ["暂无需要修改的风险项。"]:
            if isinstance(suggestion, dict):
                text = suggestion.get("action") or suggestion.get("suggestion") or str(suggestion)
            else:
                text = str(suggestion)
            lines.append(f"- {text}")
        lines.append("")
        lines.append("## 三、风险概览")
        lines.append("")
        lines.append("| 风险等级 | 数量 |")
        lines.append("| --- | ---: |")
        for severity in SEVERITY_LEVELS:
            lines.append(f"| {severity} | {summary.get('severity_counts', {}).get(severity, 0)} |")
        lines.append("")
        lines.append("## 四、四类检查节点结果")
        lines.append("")
        for module, name in MODULE_NAMES.items():
            module_result = module_check_results.get(module, {})
            module_risks = [risk for risk in risks if risk.get("module") == module]
            lines.append(f"### {name}")
            lines.append("")
            if module_result:
                lines.append(f"- 模块得分：{module_result.get('score', 0)} / 100")
                if module_result.get("summary"):
                    lines.append(f"- 模块总结：{module_result.get('summary')}")
                lines.append("")
            if not module_risks:
                lines.append("- 未发现明显风险。")
                lines.append("")
                continue
            for risk in module_risks:
                evidence = risk.get("evidence", [{}])[0].get("content", "")
                location = risk.get("location", {})
                lines.append(f"- **{risk.get('risk_id')} {risk.get('title')}**")
                lines.append(f"  - 修改优先级：{risk.get('severity')}")
                lines.append(f"  - 原文位置：{location.get('section', '全文')}")
                lines.append(f"  - 检测证据：{evidence}")
                lines.append(f"  - 修改建议：{risk.get('suggestion')}")
                lines.append("")
        lines.append("## 五、附录：规则与检测记录")
        lines.append("")
        lines.append("- 本报告由规则检测与大模型辅助检查共同生成，仅用于提交前自检和修改参考。")
        lines.append("- Agent 不对论文作最终学术定性。")
        return "\n".join(lines)
