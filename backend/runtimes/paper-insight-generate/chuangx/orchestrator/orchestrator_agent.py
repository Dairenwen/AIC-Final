from __future__ import annotations

from typing import Any

from ..agents import (
    GapIdentificationAgent,
    IdeaGenerationAgent,
    InnovationEvaluationAgent,
    LiteratureIntelligenceAgent,
    RefinementAgent,
    TrendAnalysisAgent,
)
from ..models import InnovationRequest, InnovationState, utc_now_iso


class InnovationOrchestrator:
    """Main stateful workflow for the chuangx innovation mining agent."""

    def __init__(self) -> None:
        self.literature_agent = LiteratureIntelligenceAgent()
        self.trend_agent = TrendAnalysisAgent()
        self.gap_agent = GapIdentificationAgent()
        self.idea_agent = IdeaGenerationAgent()
        self.evaluation_agent = InnovationEvaluationAgent()
        self.refinement_agent = RefinementAgent()

    def run(self, request: InnovationRequest) -> dict[str, Any]:
        state = InnovationState.from_request(request)
        mode = request.normalized_mode()

        self.literature_agent.run(state)
        if mode in {"full", "expand"}:
            self.trend_agent.run(state)
            self.gap_agent.run(state)
        else:
            state.research_trends = []
            state.research_gaps = [
                {
                    "id": "GAP-SEED-001",
                    "title": "Seed ideas evaluation",
                    "description": "evaluate 模式下以用户输入的种子想法为候选池，只做证据检索、评估和精炼。",
                    "gap_type": "seed_evaluation",
                    "confidence": 0.5,
                    "evidence_refs": [doc.id for doc in state.literature_corpus[:3]],
                    "related_keywords": request.keywords,
                }
            ]

        self.idea_agent.run(state)
        self.evaluation_agent.run(state)

        # A light quality-control loop: add one extra iteration when no candidate survived.
        if not state.evaluated_innovations and state.iteration_count < 1:
            state.iteration_count += 1
            state.feedback.append("首轮未生成可评估候选，已触发一次兜底发散。")
            if not state.research_gaps:
                self.gap_agent.run(state)
            self.idea_agent.run(state)
            self.evaluation_agent.run(state)

        self.refinement_agent.run(state)
        return self.to_response(state)

    def to_response(self, state: InnovationState) -> dict[str, Any]:
        docs = [doc.evidence_dict(snippet_chars=180) for doc in state.literature_corpus]
        return {
            "research_domain": state.research_domain,
            "mode": state.request.normalized_mode(),
            "research_trends": state.research_trends,
            "research_gaps": state.research_gaps,
            "innovations": state.refined_proposals,
            "candidate_innovations": state.candidate_innovations,
            "evaluated_innovations": state.evaluated_innovations,
            "refined_proposals": state.refined_proposals,
            "knowledge_graph_summary": state.knowledge_graph.get("summary", ""),
            "knowledge_graph": state.knowledge_graph,
            "citation_network_summary": state.citation_network.get("summary", ""),
            "evidence_map": state.evidence_map,
            "feedback": state.feedback,
            "workflow_trace": state.workflow_trace,
            "literature_corpus": docs,
            "metadata": {
                **state.metadata,
                "completed_at": utc_now_iso(),
                "document_count": len(state.literature_corpus),
                "trend_count": len(state.research_trends),
                "gap_count": len(state.research_gaps),
                "candidate_count": len(state.candidate_innovations),
                "proposal_count": len(state.refined_proposals),
                "output_contract": {
                    "for_wengao": "innovations[*].downstream_wengao_inputs",
                    "score_formula": "0.35*Novelty + 0.25*Feasibility + 0.30*Impact - 0.10*Risk",
                },
            },
        }
