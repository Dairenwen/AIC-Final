from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from config.constants import DEFAULT_REPORT_PAPERS
from src.llm import build_bailian_chat_model
from src.schemas import LiteratureRetriever, LiteratureRuntimeContext
from src.tools import (
    ArxivSearchTool,
    GoogleScholarSearchTool,
    LocalKnowledgeSearchTool,
    PersonalKnowledgeSearchTool,
)

from .literature_nodes import LiteratureNodes
from .state import LiteratureAgentState


def build_literature_graph(
    *,
    chat_model: Any | None = None,
    local_retriever: LiteratureRetriever | None = None,
    personal_retriever: LiteratureRetriever | None = None,
    arxiv_tool: Any | None = None,
    scholar_tool: Any | None = None,
    checkpointer: Any | None = None,
    top_n: int = DEFAULT_REPORT_PAPERS,
    current_year: int | None = None,
    output_path: str = "output/annual_publication_fishbone.png",
    output_title: str = "年度文献发表脉络",
    allow_report_fallback: bool = False,
):
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    nodes = LiteratureNodes(
        chat_model or build_bailian_chat_model(),
        local_retriever=local_retriever or LocalKnowledgeSearchTool(),
        personal_retriever=personal_retriever or PersonalKnowledgeSearchTool(),
        arxiv_tool=arxiv_tool or ArxivSearchTool(),
        scholar_tool=scholar_tool or GoogleScholarSearchTool(),
        top_n=top_n,
        current_year=current_year,
        output_path=output_path,
        output_title=output_title,
        allow_report_fallback=allow_report_fallback,
    )
    graph = StateGraph(LiteratureAgentState, context_schema=LiteratureRuntimeContext)
    graph.add_node("rewrite_query", nodes.rewrite_query)
    graph.add_node("retrieve_local", nodes.retrieve_local)
    graph.add_node("retrieve_personal", nodes.retrieve_personal)
    graph.add_node("retrieve_google_scholar", nodes.retrieve_scholar)
    graph.add_node("retrieve_arxiv", nodes.retrieve_arxiv)
    graph.add_node("aggregate_and_rank", nodes.aggregate_and_rank)
    graph.add_node("generate_report", nodes.generate_report)
    graph.add_node("format_literature_list", nodes.format_literature_list)
    graph.add_node("generate_annual_fishbone", nodes.generate_annual_fishbone)
    graph.set_entry_point("rewrite_query")
    retrieval_nodes = [
        "retrieve_local",
        "retrieve_personal",
        "retrieve_google_scholar",
        "retrieve_arxiv",
    ]
    for node_name in retrieval_nodes:
        graph.add_edge("rewrite_query", node_name)
    graph.add_edge(retrieval_nodes, "aggregate_and_rank")
    graph.add_edge("aggregate_and_rank", "generate_report")
    graph.add_edge("generate_report", "format_literature_list")
    graph.add_edge("format_literature_list", "generate_annual_fishbone")
    graph.add_edge("generate_annual_fishbone", END)
    return graph.compile(checkpointer=checkpointer)
