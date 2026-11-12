from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml


class RuleRetrieverTool:
    """Load local YAML rules for the target check."""

    def __init__(self, rules_path: str | None = None) -> None:
        if rules_path is None:
            rules_path = str(Path(__file__).resolve().parents[1] / "rules" / "default_rules.yaml")
        self.rules_path = Path(rules_path)

    def run(self, task_type: str = "paper_precheck", target_rule_set: str = "default") -> List[Dict[str, Any]]:
        content = self.rules_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        rules = data.get("rules", [])
        return [
            {
                **rule,
                "task_type": task_type,
                "target_rule_set": target_rule_set,
                "rule_version": data.get("version", "local"),
            }
            for rule in rules
        ]

