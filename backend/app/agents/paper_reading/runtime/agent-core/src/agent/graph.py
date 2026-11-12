from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from llm.gateway import ModelGateway
from tools.ports import ArtifactStorePort, KnowledgeBasePort

from .nodes import ReadingNodes, ReadingWorkflowNodes
from .state import ReadingState


WORKFLOW_NODE_ORDER = (
    "validate_request",
    "load_paper",
    "load_chunks",
    "validate_source",
    "prepare_context",
    "analyze_paper",
    "bind_evidence",
    "validate_result",
    "persist_artifact",
    "complete",
)

AGENT_FLOW_NODE_ORDER = (
    "validate_request",
    "validate_source",
    "prepare_context",
    "analyze_paper",
    "bind_evidence",
    "validate_result",
    "complete",
)


def build_reading_graph(
    knowledge_base: KnowledgeBasePort,
    model_gateway: ModelGateway,
    artifact_store: ArtifactStorePort,
):
    nodes = ReadingNodes(knowledge_base, model_gateway, artifact_store)
    graph = StateGraph(ReadingState)
    for name in WORKFLOW_NODE_ORDER:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, WORKFLOW_NODE_ORDER[0])
    for source, target in zip(WORKFLOW_NODE_ORDER, WORKFLOW_NODE_ORDER[1:]):
        graph.add_edge(source, target)
    graph.add_edge(WORKFLOW_NODE_ORDER[-1], END)
    return graph.compile()


def build_agent_flow(model_gateway: ModelGateway):
    """Build the primary persistence-free Agent workflow."""

    nodes = ReadingWorkflowNodes(model_gateway)
    graph = StateGraph(ReadingState)
    for name in AGENT_FLOW_NODE_ORDER:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, AGENT_FLOW_NODE_ORDER[0])
    for source, target in zip(AGENT_FLOW_NODE_ORDER, AGENT_FLOW_NODE_ORDER[1:]):
        graph.add_edge(source, target)
    graph.add_edge(AGENT_FLOW_NODE_ORDER[-1], END)
    return graph.compile()
