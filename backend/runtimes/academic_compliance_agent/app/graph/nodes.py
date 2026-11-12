from __future__ import annotations

import uuid
from typing import Any, Dict

from academic_compliance_agent.app.graph.state import ComplianceState
from academic_compliance_agent.app.tools.citation_verifier import CitationVerifierTool
from academic_compliance_agent.app.tools.document_parser import DocumentParserTool
from academic_compliance_agent.app.tools.figure_table_consistency_checker import FigureTableConsistencyTool
from academic_compliance_agent.app.tools.format_submission_checker import FormatSubmissionCheckerTool
from academic_compliance_agent.app.tools.llm_module_reviewer import LLMModuleReviewTool
from academic_compliance_agent.app.tools.memory_manager import read_long_term_memory, write_long_term_memory
from academic_compliance_agent.app.tools.paper_norm_checker import PaperNormCheckerTool
from academic_compliance_agent.app.tools.report_writer import ReportWriterTool
from academic_compliance_agent.app.tools.risk_aggregator import RiskAggregatorTool
from academic_compliance_agent.app.tools.rule_retriever import RuleRetrieverTool
from academic_compliance_agent.app.tools.suggestion_generator import SuggestionGeneratorTool


def _append_log(state: ComplianceState, node: str, message: str) -> list[dict[str, Any]]:
    logs = list(state.get("logs", []))
    logs.append({"node": node, "message": message})
    return logs


def _review_with_llm(
    state: ComplianceState,
    *,
    module: str,
    module_name: str,
    risks: list[dict[str, Any]],
):
    return LLMModuleReviewTool().run(
        module=module,
        module_name=module_name,
        parsed_document=state.get("parsed_document", {}),
        rules=state.get("retrieved_rules", []),
        rule_risks=risks,
    )


def intake_node(state: ComplianceState, runtime: Any = None) -> Dict[str, Any]:
    task_id = state.get("task_id") or f"TASK-{uuid.uuid4().hex[:8].upper()}"
    memory = read_long_term_memory(state, runtime)
    short_term_memory = {
        "enabled": bool(state.get("compliance_summary") or state.get("structured_output")),
        "previous_task_id": state.get("task_id", ""),
        "previous_compliance_summary": state.get("compliance_summary", {}),
        "previous_risk_summary": state.get("structured_output", {}).get("summary", {}),
    }
    return {
        "task_id": task_id,
        "user_id": memory.get("user_id"),
        "task_type": state.get("task_type", "paper_precheck"),
        "target_rule_set": state.get("target_rule_set", "default"),
        "check_results": {},
        "memory": memory,
        "short_term_memory": short_term_memory,
        "logs": _append_log(state, "intake_node", "Task initialized."),
    }


def parse_document_node(state: ComplianceState) -> Dict[str, Any]:
    parser = DocumentParserTool()
    if state.get("input_text"):
        parsed = parser.parse_text(state["input_text"])
    else:
        files = state.get("files", [])
        if not files:
            raise ValueError("No input text or manuscript file provided.")
        parsed = parser.parse_file(files[0]["path"])
    return {
        "raw_text": parsed.get("raw_text", ""),
        "parsed_document": parsed,
        "figures": parsed.get("figures", []),
        "tables": parsed.get("tables", []),
        "references": parsed.get("references", []),
        "citations": parsed.get("citations", []),
        "logs": _append_log(state, "parse_document_node", "Document parsed."),
    }


def load_rules_node(state: ComplianceState) -> Dict[str, Any]:
    retriever = RuleRetrieverTool()
    rules = retriever.run(
        task_type=state.get("task_type", "paper_precheck"),
        target_rule_set=state.get("target_rule_set", "default"),
    )
    return {
        "retrieved_rules": rules,
        "logs": _append_log(state, "load_rules_node", f"Loaded {len(rules)} academic compliance rules."),
    }


retrieve_rules_node = load_rules_node


def paper_norm_check_node(state: ComplianceState) -> Dict[str, Any]:
    risks = PaperNormCheckerTool().run(state.get("parsed_document", {}), state.get("retrieved_rules", []))
    risks, check_result = _review_with_llm(state, module="paper_norm", module_name="学术论文规范检查", risks=risks)
    return {
        "paper_norm_results": risks,
        "paper_norm_check_result": check_result,
    }


def citation_check_node(state: ComplianceState) -> Dict[str, Any]:
    risks = CitationVerifierTool().run(state.get("parsed_document", {}), state.get("retrieved_rules", []))
    risks, check_result = _review_with_llm(state, module="citation", module_name="引用与参考文献核验", risks=risks)
    return {
        "citation_results": risks,
        "citation_check_result": check_result,
    }


def figure_table_check_node(state: ComplianceState) -> Dict[str, Any]:
    risks = FigureTableConsistencyTool().run(state.get("parsed_document", {}), state.get("retrieved_rules", []))
    risks, check_result = _review_with_llm(state, module="figure_table", module_name="图表一致性检查", risks=risks)
    return {
        "figure_table_results": risks,
        "figure_table_check_result": check_result,
    }


def format_submission_check_node(state: ComplianceState) -> Dict[str, Any]:
    risks = FormatSubmissionCheckerTool().run(
        state.get("parsed_document", {}),
        state.get("files", []),
        state.get("retrieved_rules", []),
    )
    risks, check_result = _review_with_llm(state, module="format_submission", module_name="格式与投稿规范检查", risks=risks)
    return {
        "format_submission_results": risks,
        "format_submission_check_result": check_result,
    }


def risk_aggregate_node(state: ComplianceState) -> Dict[str, Any]:
    check_results = {
        "paper_norm": state.get("paper_norm_results", []),
        "citation": state.get("citation_results", []),
        "figure_table": state.get("figure_table_results", []),
        "format_submission": state.get("format_submission_results", []),
    }
    module_check_results = {
        "paper_norm": state.get("paper_norm_check_result", {}),
        "citation": state.get("citation_check_result", {}),
        "figure_table": state.get("figure_table_check_result", {}),
        "format_submission": state.get("format_submission_check_result", {}),
    }
    aggregated = RiskAggregatorTool().run(check_results)
    return {
        "check_results": check_results,
        "module_check_results": module_check_results,
        "risks": aggregated["risks"],
        "structured_output": {"summary": aggregated["summary"], "module_check_results": module_check_results},
        "logs": _append_log(state, "risk_aggregate_node", f"Aggregated {len(aggregated['risks'])} risks."),
    }


def suggestion_node(state: ComplianceState) -> Dict[str, Any]:
    compliance_summary = SuggestionGeneratorTool().run(
        module_check_results=state.get("module_check_results", {}),
        risks=state.get("risks", []),
        risk_summary=state.get("structured_output", {}).get("summary", {}),
    )
    suggestions = compliance_summary.get("revision_suggestions", [])
    structured_output = dict(state.get("structured_output", {}))
    structured_output["compliance_summary"] = compliance_summary
    return {
        "suggestions": suggestions,
        "compliance_summary": compliance_summary,
        "structured_output": structured_output,
        "logs": _append_log(state, "suggestion_node", f"Generated {len(suggestions)} suggestions."),
    }


def report_node(state: ComplianceState, runtime: Any = None) -> Dict[str, Any]:
    summary = state.get("structured_output", {}).get("summary", {})
    output = ReportWriterTool().run(state, summary)
    next_state = {**state, **output}
    write_long_term_memory(next_state, runtime)
    return {
        **output,
        "logs": _append_log(state, "report_node", "Report generated."),
    }
