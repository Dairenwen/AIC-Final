from __future__ import annotations

from typing import Any, Dict, List

from academic_compliance_agent.app.services.llm import OpenAICompatibleLLMClient


class SuggestionGeneratorTool:
    """Summarize four module-level check results into final compliance advice."""

    def run(
        self,
        *,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        llm_result = self._run_llm(module_check_results, risks, risk_summary)
        if llm_result:
            return llm_result
        return self._fallback_summary(module_check_results, risks, risk_summary)

    def _run_llm(
        self,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        client = OpenAICompatibleLLMClient()
        if not client.is_available():
            return {}
        system_prompt = (
            "你是学术合规性校验总结专家。请综合四个检查节点的大模型检查结构，生成最终修改建议总结。"
            "只能依据 module_check_results 中的四个检查结构，不要新增检查结构中没有依据的事实，不要做最终学术不端定性。"
            "输出 JSON 对象："
            "{\"compliance_score\":0,\"excellent_points\":[\"...\"],\"revision_suggestions\":[\"...\"],"
            "\"summary\":\"...\"}。"
            "compliance_score 必须是百分制整数，0 到 100；excellent_points 写学术合规性的优秀点；"
            "revision_suggestions 写后续修改建议，要求具体可执行。"
        )
        payload = {
            "module_check_results": module_check_results,
        }
        data = client.chat_json(system_prompt, payload)
        return self._normalize_summary(data, module_check_results, risks, risk_summary)

    def _normalize_summary(
        self,
        data: Dict[str, Any],
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not data:
            return {}
        return {
            "compliance_score": self._score(data.get("compliance_score"), module_check_results, risks),
            "excellent_points": self._string_list(data.get("excellent_points"))
            or self._fallback_excellent_points(module_check_results),
            "revision_suggestions": self._string_list(data.get("revision_suggestions"))
            or self._fallback_revision_suggestions(module_check_results, risks),
            "summary": str(data.get("summary", "")).strip()
            or self._fallback_summary_text(module_check_results, risks, risk_summary),
            "generated_by": "llm",
        }

    def _fallback_summary(
        self,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "compliance_score": self._score(None, module_check_results, risks),
            "excellent_points": self._fallback_excellent_points(module_check_results),
            "revision_suggestions": self._fallback_revision_suggestions(module_check_results, risks),
            "summary": self._fallback_summary_text(module_check_results, risks, risk_summary),
            "generated_by": "rule_fallback",
        }

    def _score(
        self,
        raw_score: Any,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
    ) -> int:
        try:
            score = int(round(float(raw_score)))
            return max(0, min(100, score))
        except (TypeError, ValueError):
            pass
        module_scores = []
        for result in module_check_results.values():
            try:
                module_scores.append(float(result.get("score")))
            except (TypeError, ValueError):
                continue
        if module_scores:
            return max(0, min(100, int(round(sum(module_scores) / len(module_scores)))))
        penalty = {"极高": 25, "高": 15, "中": 8, "低": 3, "极低": 1}
        score = 100
        for risk in risks:
            score -= penalty.get(str(risk.get("severity", "低")), 3)
        return max(0, min(100, score))

    def _fallback_excellent_points(self, module_check_results: Dict[str, Dict[str, Any]]) -> List[str]:
        points = []
        for result in module_check_results.values():
            for item in result.get("strengths", [])[:2]:
                text = str(item).strip()
                if text and text not in points:
                    points.append(text)
        return points[:6] or ["论文已完成基础结构解析，主要合规问题可以被定位到具体模块和修改方向。"]

    def _fallback_revision_suggestions(
        self,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
    ) -> List[str]:
        suggestions = []
        for result in module_check_results.values():
            for item in result.get("suggestions", [])[:3]:
                text = str(item).strip()
                if text and text not in suggestions:
                    suggestions.append(text)
        for risk in risks[:8]:
            text = str(risk.get("suggestion", "")).strip()
            if text and text not in suggestions:
                suggestions.append(text)
        return suggestions[:10] or ["提交前请再次核对论文结构、引用、图表和格式是否符合目标规范。"]

    def _fallback_summary_text(
        self,
        module_check_results: Dict[str, Dict[str, Any]],
        risks: List[Dict[str, Any]],
        risk_summary: Dict[str, Any],
    ) -> str:
        score = self._score(None, module_check_results, risks)
        risk_count = risk_summary.get("risk_count", len(risks))
        return f"论文当前学术合规性得分为 {score} 分，共发现 {risk_count} 项待关注问题，建议优先处理高等级风险。"

    def _string_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value[:10]:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
