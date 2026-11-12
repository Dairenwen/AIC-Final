from __future__ import annotations

from typing import Any, Dict, List, Tuple

from academic_compliance_agent.app.services.llm import OpenAICompatibleLLMClient, llm_document_view
from academic_compliance_agent.app.tools.common import first_rule, make_location, make_risk, normalize_severity


class LLMModuleReviewTool:
    """Generate one module-level LLM-assisted compliance check result."""

    def __init__(self) -> None:
        self.client = OpenAICompatibleLLMClient()

    def run(
        self,
        *,
        module: str,
        module_name: str,
        parsed_document: Dict[str, Any],
        rules: List[Dict[str, Any]],
        rule_risks: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        llm_result = self._run_llm(module, module_name, parsed_document, rules, rule_risks)
        if not llm_result:
            module_result = self._fallback_module_result(module, module_name, rule_risks)
            return rule_risks, module_result

        llm_risks = self._normalize_llm_risks(module, llm_result.get("risks", []), rules)
        combined_risks = rule_risks + llm_risks
        module_result = {
            "module": module,
            "module_name": module_name,
            "score": self._score(llm_result.get("score"), combined_risks),
            "strengths": self._string_list(llm_result.get("strengths")) or self._fallback_strengths(module_name, combined_risks),
            "risks": [
                {
                    "severity": risk.get("severity"),
                    "title": risk.get("title"),
                    "evidence": risk.get("evidence", [{}])[0].get("content", ""),
                    "suggestion": risk.get("suggestion", ""),
                }
                for risk in combined_risks[:12]
            ],
            "suggestions": self._string_list(llm_result.get("suggestions")) or self._fallback_suggestions(combined_risks),
            "summary": str(llm_result.get("summary", "")).strip() or self._fallback_summary(module_name, combined_risks),
            "generated_by": "llm",
        }
        return combined_risks, module_result

    def _run_llm(
        self,
        module: str,
        module_name: str,
        parsed_document: Dict[str, Any],
        rules: List[Dict[str, Any]],
        rule_risks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self.client.is_available():
            return {}
        system_prompt = (
            "你是严谨的学术合规性校验专家。请对一个论文合规模块进行大模型辅助检查。"
            "必须只基于给定论文结构、规则和规则检测结果，不要臆造。"
            "输出 JSON 对象："
            "{\"score\":0,\"strengths\":[\"...\"],\"risks\":[{\"type\":\"...\",\"severity\":\"极高|高|中|低|极低\","
            "\"title\":\"...\",\"section\":\"...\",\"quote\":\"...\",\"evidence\":\"...\",\"suggestion\":\"...\","
            "\"confidence\":0.0}],\"suggestions\":[\"...\"],\"summary\":\"...\"}。"
            "score 为该模块百分制合规分，0 到 100；severity 只能使用中文五档。"
        )
        payload = {
            "module": module_name,
            "document": llm_document_view(parsed_document),
            "rules": [
                {
                    "rule_id": rule.get("rule_id"),
                    "name": rule.get("name"),
                    "severity": rule.get("severity"),
                    "description": rule.get("description"),
                    "suggestion": rule.get("suggestion"),
                }
                for rule in rules
                if rule.get("module") == module
            ],
            "rule_risks": [
                {
                    "type": risk.get("type"),
                    "severity": risk.get("severity"),
                    "title": risk.get("title"),
                    "location": risk.get("location"),
                    "evidence": risk.get("evidence"),
                    "suggestion": risk.get("suggestion"),
                }
                for risk in rule_risks
            ],
        }
        return self.client.chat_json(system_prompt, payload)

    def _normalize_llm_risks(
        self,
        module: str,
        risks: Any,
        rules: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not isinstance(risks, list):
            return []
        default_rule = first_rule(rules, module)
        normalized: List[Dict[str, Any]] = []
        for item in risks[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            evidence = str(item.get("evidence", "")).strip()
            suggestion = str(item.get("suggestion", "")).strip()
            if not title or not evidence or not suggestion:
                continue
            confidence = item.get("confidence", 0.68)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.68
            normalized.append(
                make_risk(
                    risk_type=str(item.get("type", f"LLM_{module.upper()}_RISK")).strip() or f"LLM_{module.upper()}_RISK",
                    module=module,
                    severity=normalize_severity(str(item.get("severity", "中")), "中"),
                    title=title,
                    evidence=f"大模型辅助检查：{evidence}",
                    suggestion=suggestion,
                    rule=default_rule,
                    location=make_location(str(item.get("section", "全文")), str(item.get("quote", ""))),
                    confidence=confidence,
                )
            )
        return normalized

    def _fallback_module_result(
        self,
        module: str,
        module_name: str,
        risks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "module": module,
            "module_name": module_name,
            "score": self._score(None, risks),
            "strengths": self._fallback_strengths(module_name, risks),
            "risks": [
                {
                    "severity": risk.get("severity"),
                    "title": risk.get("title"),
                    "evidence": risk.get("evidence", [{}])[0].get("content", ""),
                    "suggestion": risk.get("suggestion", ""),
                }
                for risk in risks[:12]
            ],
            "suggestions": self._fallback_suggestions(risks),
            "summary": self._fallback_summary(module_name, risks),
            "generated_by": "rule_fallback",
        }

    def _score(self, raw_score: Any, risks: List[Dict[str, Any]]) -> int:
        try:
            score = int(round(float(raw_score)))
            return max(0, min(100, score))
        except (TypeError, ValueError):
            pass
        penalty = {"极高": 25, "高": 15, "中": 8, "低": 3, "极低": 1}
        score = 100
        for risk in risks:
            score -= penalty.get(normalize_severity(risk.get("severity"), "低"), 3)
        return max(0, min(100, score))

    def _fallback_strengths(self, module_name: str, risks: List[Dict[str, Any]]) -> List[str]:
        if not risks:
            return [f"{module_name}未发现明显合规风险。"]
        return [f"{module_name}已完成规则化定位，风险项具有明确证据和可执行修改方向。"]

    def _fallback_suggestions(self, risks: List[Dict[str, Any]]) -> List[str]:
        suggestions = []
        for risk in risks[:5]:
            suggestion = str(risk.get("suggestion", "")).strip()
            if suggestion and suggestion not in suggestions:
                suggestions.append(suggestion)
        return suggestions or ["保持现有规范表达，并在提交前按目标学校或期刊要求复核。"]

    def _fallback_summary(self, module_name: str, risks: List[Dict[str, Any]]) -> str:
        if not risks:
            return f"{module_name}未发现明显问题。"
        return f"{module_name}发现 {len(risks)} 项需要关注的问题，建议按风险等级由高到低修改。"

    def _string_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value[:8]:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
