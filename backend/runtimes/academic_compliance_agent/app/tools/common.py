from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


SEVERITY_LEVELS = ["极高", "高", "中", "低", "极低"]
LEGACY_SEVERITY_MAP = {
    "P0": "极高",
    "P1": "高",
    "P2": "中",
    "P3": "低",
    "P4": "极低",
}
SEVERITY_ORDER = {
    "极高": 0,
    "高": 1,
    "中": 2,
    "低": 3,
    "极低": 4,
    **{legacy: index for legacy, index in {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}.items()},
}
REVISION_PRIORITY = {
    "极高": "极高",
    "高": "高",
    "中": "中",
    "低": "低",
    "极低": "极低",
    **{legacy: chinese for legacy, chinese in LEGACY_SEVERITY_MAP.items()},
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_severity(severity: str | None, default: str = "中") -> str:
    value = (severity or default).strip()
    value = LEGACY_SEVERITY_MAP.get(value.upper(), value)
    return value if value in SEVERITY_LEVELS else default


def module_rules(rules: Iterable[Dict[str, Any]], module: str) -> List[Dict[str, Any]]:
    return [rule for rule in rules if rule.get("module") == module]


def first_rule(rules: Iterable[Dict[str, Any]], module: str, rule_id: Optional[str] = None) -> Dict[str, Any]:
    filtered = module_rules(rules, module)
    if rule_id:
        for rule in filtered:
            if rule.get("rule_id") == rule_id:
                return rule
    return filtered[0] if filtered else {}


def make_location(section: str = "", quote: str = "", paragraph_index: Optional[int] = None) -> Dict[str, Any]:
    location: Dict[str, Any] = {"section": section or "全文"}
    if paragraph_index is not None:
        location["paragraph_index"] = paragraph_index
    if quote:
        location["quote"] = quote[:160]
    return location


def make_risk(
    *,
    risk_type: str,
    module: str,
    severity: str,
    title: str,
    evidence: str,
    suggestion: str,
    rule: Optional[Dict[str, Any]] = None,
    location: Optional[Dict[str, Any]] = None,
    confidence: float = 0.8,
) -> Dict[str, Any]:
    basis = []
    if rule:
        basis.append(
            {
                "rule_id": rule.get("rule_id", ""),
                "name": rule.get("name", ""),
                "content": rule.get("description", rule.get("suggestion", "")),
            }
        )
    return {
        "risk_id": "",
        "type": risk_type,
        "module": module,
        "severity": normalize_severity(severity),
        "title": title,
        "location": location or make_location(),
        "evidence": [{"source": "manuscript", "content": evidence}],
        "rule_basis": basis,
        "suggestion": suggestion,
        "confidence": round(float(confidence), 2),
        "status": "open",
    }


def unique_preserve_order(items: Iterable[Any]) -> List[Any]:
    seen = set()
    result = []
    for item in items:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
