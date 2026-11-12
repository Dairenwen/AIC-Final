from __future__ import annotations

import re
from typing import Any


QUANTIFIED_VALUE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:\d+(?:\.\d+)?(?:\s*[—–~-]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:%|％|倍|个/秒|次/秒|毫秒|微秒|纳秒|秒|分钟|小时|天|Hz|kHz|MHz|GHz|"
    r"B|KB|MB|GB|TB|字节|次|个|层|周期|P/E\s*cycle(?:s)?))",
    re.IGNORECASE,
)
BARE_NUMERIC_VALUE = re.compile(
    r"(?<![A-Za-z0-9_.-])\d+(?:\.\d+)?(?![A-Za-z0-9_.-])"
)
EFFECT_CUE = re.compile(r"实测|实验表明|测试结果|测试表明|提升|提高|降低|下降|达到|准确率|吞吐量|延迟|容量|频率|时长")
EXPLICIT_DEMO_MARKER = re.compile(r"示例(?:参数|数值)?[^。\n]{0,40}(?:未经实验验证|仅用于说明|不作为.*限定)")


def _sentences(markdown: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？；])|\n+", markdown) if part.strip()]


def check_quantitative_facts(markdown: str, source_materials: str) -> list[dict[str, Any]]:
    """Flag unsupported quantitative technical statements without treating headings/formula indices as facts."""
    issues: list[dict[str, Any]] = []
    normalized_source = re.sub(r"\s+", "", source_materials)
    for statement in _sentences(markdown):
        quantified_matches = list(QUANTIFIED_VALUE.finditer(statement))
        values = [match.group(0).strip() for match in quantified_matches]
        occupied = [match.span() for match in quantified_matches]
        for match in BARE_NUMERIC_VALUE.finditer(statement):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            prefix = statement[max(0, match.start() - 6) : match.start()]
            if prefix.endswith("权利要求"):
                continue
            if re.match(r"^\s*\d+[.、)]\s*", statement) and match.start() < 4:
                continue
            values.append(match.group(0))
        if not values:
            continue
        if statement.startswith(("#", "```", "|---")):
            continue
        supported = all(re.sub(r"\s+", "", value) in normalized_source for value in values)
        marked_demo = bool(EXPLICIT_DEMO_MARKER.search(statement))
        has_effect_cue = bool(EFFECT_CUE.search(statement))
        if supported:
            status = "source_supported"
        elif marked_demo and not re.search(r"实测|实验表明|测试结果|测试表明", statement):
            status = "marked_unverified_demo"
        else:
            status = "unsupported"
        if status == "unsupported":
            issues.append(
                {
                    "statement": statement[:500],
                    "numeric_values": values,
                    "source_status": status,
                    "source_refs": [],
                    "severity": "major",
                    "effect_language": has_effect_cue,
                }
            )
    return issues


def sanitize_unsupported_quantitative_facts(markdown: str, source_materials: str) -> str:
    """Replace unsupported values with qualitative wording before the one allowed re-check."""
    issues = check_quantitative_facts(markdown, source_materials)
    for issue in issues:
        statement = issue["statement"]
        replacement = statement
        for value in issue["numeric_values"]:
            replacement = replacement.replace(value, "未限定的参数值")
        replacement = re.sub(r"实测|实验表明|测试结果(?:表明)?|测试表明", "预期", replacement)
        replacement = re.sub(r"(?:提升|提高|降低|下降|达到)\s*未限定的参数值", "产生相应的定性改善", replacement)
        if statement in markdown:
            markdown = markdown.replace(statement, replacement, 1)
    return markdown
