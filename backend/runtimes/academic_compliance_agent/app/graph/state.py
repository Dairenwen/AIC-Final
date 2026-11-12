from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict


@dataclass
class ComplianceContext:
    user_id: str = "default_user"


class ComplianceState(TypedDict, total=False):
    task_id: str
    user_id: Optional[str]
    task_type: str
    target_rule_set: Optional[str]
    files: List[Dict[str, Any]]
    input_text: str

    memory: Dict[str, Any]
    short_term_memory: Dict[str, Any]
    raw_text: str
    parsed_document: Dict[str, Any]
    figures: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]
    references: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]

    retrieved_rules: List[Dict[str, Any]]
    check_results: Dict[str, Any]
    module_check_results: Dict[str, Any]
    paper_norm_results: List[Dict[str, Any]]
    paper_norm_check_result: Dict[str, Any]
    citation_results: List[Dict[str, Any]]
    citation_check_result: Dict[str, Any]
    figure_table_results: List[Dict[str, Any]]
    figure_table_check_result: Dict[str, Any]
    format_submission_results: List[Dict[str, Any]]
    format_submission_check_result: Dict[str, Any]
    risks: List[Dict[str, Any]]
    suggestions: List[Dict[str, Any]]
    compliance_summary: Dict[str, Any]

    final_report: str
    structured_output: Dict[str, Any]
    logs: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]
