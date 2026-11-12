from __future__ import annotations

from langgraph.graph import END, StateGraph

from academic_figure_agent.llm import BailianFigurePlanner
from academic_figure_agent.schemas import FigureRequest, FigureResult
from config.settings import Settings

from .nodes import FigureNodes, route_after_inspection
from .state import FigureAgentState


def build_figure_graph(
    planner: BailianFigurePlanner | None = None,
    settings: Settings | None = None,
):
    nodes = FigureNodes(planner=planner, settings=settings)
    graph = StateGraph(FigureAgentState)
    graph.add_node("prepare", nodes.prepare)
    graph.add_node("plan", nodes.plan)
    graph.add_node("generate_code", nodes.generate_code)
    graph.add_node("render", nodes.render)
    graph.add_node("inspect", nodes.inspect)
    graph.add_node("revise", nodes.revise)
    graph.add_node("finalize", nodes.finalize)
    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "plan")
    graph.add_edge("plan", "generate_code")
    graph.add_edge("generate_code", "render")
    graph.add_edge("render", "inspect")
    graph.add_conditional_edges(
        "inspect",
        route_after_inspection,
        {"revise": "revise", "finalize": "finalize"},
    )
    graph.add_edge("revise", "generate_code")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_figure_agent(
    request: FigureRequest,
    planner: BailianFigurePlanner | None = None,
    settings: Settings | None = None,
) -> FigureResult:
    graph = build_figure_graph(planner=planner, settings=settings)
    state = graph.invoke({"request": request, "warnings": []})
    return FigureResult(
        request=request,
        spec=state["spec"],
        dataset=state["dataset"],
        captions=state["captions"],
        quality_report=state["quality_report"],
        artifacts=state["artifacts"],
        warnings=state.get("warnings", []),
    )
