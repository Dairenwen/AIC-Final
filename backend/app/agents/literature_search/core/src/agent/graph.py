from __future__ import annotations

from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph

from src.tools import (
    AnnualPublicationFishboneTool,
    ArxivSearchTool,
    FishboneDiagramTool,
    GoogleScholarSearchTool,
    LiteratureListTool,
)

from .nodes import ToolNodes
from .state import ToolAgentState


def build_tool_graph(tools: list[BaseTool] | None = None):
    registered_tools = tools or [
        ArxivSearchTool(),
        GoogleScholarSearchTool(),
        LiteratureListTool(),
        AnnualPublicationFishboneTool(),
        FishboneDiagramTool(),
    ]
    nodes = ToolNodes(registered_tools)
    graph = StateGraph(ToolAgentState)
    graph.add_node("invoke_tool", nodes.invoke_tool)
    graph.set_entry_point("invoke_tool")
    graph.add_edge("invoke_tool", END)
    return graph.compile()
