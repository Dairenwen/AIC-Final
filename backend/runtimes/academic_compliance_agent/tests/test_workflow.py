from __future__ import annotations

import unittest
import os
from pathlib import Path

os.environ["COMPLIANCE_AGENT_USE_LLM"] = "false"

from academic_compliance_agent.app.graph.workflow import run_compliance_workflow


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class ComplianceWorkflowTest(unittest.TestCase):
    def test_workflow_generates_report_with_four_check_modules(self) -> None:
        sample = PACKAGE_ROOT / "samples" / "sample_reference_issue.md"
        result = run_compliance_workflow(
            {
                "task_type": "paper_precheck",
                "files": [{"file_type": "manuscript", "path": str(sample)}],
            }
        )

        self.assertEqual(
            set(result["check_results"]),
            {"paper_norm", "citation", "figure_table", "format_submission"},
        )
        self.assertIn("学术合规性得分", result["final_report"])
        self.assertIn("学术合规性的优秀点", result["final_report"])
        self.assertIn("学术合规性的修改建议", result["final_report"])
        self.assertIn("compliance_score", result["compliance_summary"])
        self.assertNotIn("human_review", result)

    def test_reference_issue_is_detected(self) -> None:
        sample = PACKAGE_ROOT / "samples" / "sample_reference_issue.md"
        result = run_compliance_workflow(
            {
                "task_type": "paper_precheck",
                "files": [{"file_type": "manuscript", "path": str(sample)}],
            }
        )

        risk_types = {risk["type"] for risk in result["risks"]}
        self.assertIn("CITED_REFERENCE_MISSING", risk_types)
        self.assertIn("SUSPICIOUS_REFERENCE_PLACEHOLDER", risk_types)
        self.assertGreaterEqual(result["structured_output"]["summary"]["risk_count"], 2)


if __name__ == "__main__":
    unittest.main()
