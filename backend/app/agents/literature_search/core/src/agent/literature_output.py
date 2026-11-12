from __future__ import annotations

from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from src.tools import AnnualPublicationFishboneTool, LiteratureListTool


class LiteratureOutputState(TypedDict, total=False):
    papers: list[dict[str, Any]]
    max_items: int | None
    output_path: str
    title: str
    stream_delay_seconds: float
    literature_list: list[dict[str, Any]]
    list_total: int
    fishbone_result: dict[str, Any]


class LiteratureOutputNodes:
    def __init__(self) -> None:
        self.list_tool = LiteratureListTool()
        self.fishbone_tool = AnnualPublicationFishboneTool()

    def format_list(self, state: LiteratureOutputState) -> LiteratureOutputState:
        result = self.list_tool.invoke(
            {"papers": state.get("papers", []), "max_items": state.get("max_items")}
        )
        return {"literature_list": result["literature_list"], "list_total": result["total"]}

    def stream_fishbone(self, state: LiteratureOutputState) -> LiteratureOutputState:
        writer = get_stream_writer()
        final_event: dict[str, Any] = {}
        for event in self.fishbone_tool.stream(
            {
                "literature_list": state.get("literature_list", []),
                "output_path": state.get("output_path", "output/annual_publication_fishbone.png"),
                "title": state.get("title", "年度文献发表脉络"),
                "stream_delay_seconds": state.get("stream_delay_seconds", 0.0),
            }
        ):
            writer(event)
            final_event = event
        return {"fishbone_result": final_event}


def build_literature_output_graph():
    nodes = LiteratureOutputNodes()
    graph = StateGraph(LiteratureOutputState)
    graph.add_node("format_literature_list", nodes.format_list)
    graph.add_node("stream_annual_fishbone", nodes.stream_fishbone)
    graph.set_entry_point("format_literature_list")
    graph.add_edge("format_literature_list", "stream_annual_fishbone")
    graph.add_edge("stream_annual_fishbone", END)
    return graph.compile()
