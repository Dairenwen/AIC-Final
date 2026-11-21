from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import StructuredTool

from academic_compliance_agent.app.tools.citation_verifier import CitationVerifierTool
from academic_compliance_agent.app.tools.figure_table_consistency_checker import FigureTableConsistencyTool
from academic_compliance_agent.app.tools.format_submission_checker import FormatSubmissionCheckerTool
from academic_compliance_agent.app.tools.paper_norm_checker import PaperNormCheckerTool


def build_langchain_tools() -> List[StructuredTool]:
    """Expose checkers as LangChain tools for future agent/tool-call integration."""
    paper_norm = PaperNormCheckerTool()
    citation = CitationVerifierTool()
    figure_table = FigureTableConsistencyTool()
    format_submission = FormatSubmissionCheckerTool()

    return [
        StructuredTool.from_function(
            func=paper_norm.run,
            name="paper_norm_checker",
            description="Check academic paper structure, expression, and manuscript norms.",
        ),
        StructuredTool.from_function(
            func=citation.run,
            name="citation_verifier",
            description="Check consistency between in-text citations and references.",
        ),
        StructuredTool.from_function(
            func=figure_table.run,
            name="figure_table_consistency_checker",
            description="Check figure/table numbering, captions, and in-text mentions.",
        ),
        StructuredTool.from_function(
            func=format_submission.run,
            name="format_submission_checker",
            description="Check formatting and submission requirements.",
        ),
    ]
