from __future__ import annotations

import operator
from typing import Annotated
from typing import Any, TypedDict

from langgraph.channels import UntrackedValue

from src.schemas import AcademicPaper, ConversationContext, LiteratureReport, QueryPlan, RetrievalBatch, RetrievalError


class ToolAgentState(TypedDict, total=False):
    tool_name: str
    tool_input: dict[str, Any]
    result: Any
    error: str | None


class LiteratureAgentState(TypedDict, total=False):
    user_text: str
    recent_turns: list[ConversationContext]
    query_plan: QueryPlan
    local_retrieval_batches: Annotated[list[RetrievalBatch], UntrackedValue(list[RetrievalBatch])]
    personal_retrieval_batches: Annotated[list[RetrievalBatch], UntrackedValue(list[RetrievalBatch])]
    scholar_retrieval_batches: Annotated[list[RetrievalBatch], UntrackedValue(list[RetrievalBatch])]
    arxiv_retrieval_batches: Annotated[list[RetrievalBatch], UntrackedValue(list[RetrievalBatch])]
    retrieval_batches: Annotated[list[RetrievalBatch], UntrackedValue(list[RetrievalBatch])]
    all_ranked_papers: Annotated[list[AcademicPaper], UntrackedValue(list[AcademicPaper])]
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[RetrievalError], operator.add]
    ranked_papers: list[AcademicPaper]
    report: LiteratureReport
    output_path: str
    output_title: str
    stream_delay_seconds: float
    literature_list: list[dict[str, Any]]
    list_total: int
    fishbone_result: dict[str, Any]
