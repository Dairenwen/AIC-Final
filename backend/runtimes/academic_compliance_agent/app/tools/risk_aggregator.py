from __future__ import annotations

from typing import Any, Dict, List, Tuple

from academic_compliance_agent.app.tools.common import SEVERITY_LEVELS, SEVERITY_ORDER, normalize_severity


class RiskAggregatorTool:
    """Aggregate, deduplicate, and sort risks from all modules."""

    def run(self, check_results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        all_risks: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str, str]] = set()
        for module, risks in check_results.items():
            for risk in risks:
                key = (
                    risk.get("type", ""),
                    risk.get("location", {}).get("section", ""),
                    risk.get("evidence", [{}])[0].get("content", ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                all_risks.append(risk)

        all_risks.sort(
            key=lambda item: (
                SEVERITY_ORDER.get(normalize_severity(item.get("severity"), "低"), 3),
                -float(item.get("confidence", 0.0)),
            )
        )
        for index, risk in enumerate(all_risks, start=1):
            risk["risk_id"] = f"RISK-{index:04d}"
            risk["severity"] = normalize_severity(risk.get("severity"), "低")

        counts = {severity: 0 for severity in SEVERITY_LEVELS}
        module_counts: Dict[str, int] = {}
        for risk in all_risks:
            severity = normalize_severity(risk.get("severity"), "低")
            counts[severity] = counts.get(severity, 0) + 1
            module = risk.get("module", "unknown")
            module_counts[module] = module_counts.get(module, 0) + 1

        overall = "极低"
        if all_risks:
            overall = min(
                (normalize_severity(risk.get("severity"), "低") for risk in all_risks),
                key=lambda item: SEVERITY_ORDER.get(item, 4),
            )

        return {
            "risks": all_risks,
            "summary": {
                "overall_level": overall,
                "risk_count": len(all_risks),
                "severity_counts": counts,
                "module_counts": module_counts,
                "high_priority_revision_count": counts.get("极高", 0) + counts.get("高", 0),
            },
        }
