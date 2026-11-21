from __future__ import annotations

from ..models import InnovationState
from ..tools import CrossDomainSearchTool, InnovationMethodTool
from .base_agent import BaseAgent


class IdeaGenerationAgent(BaseAgent):
    name = "IdeaGenerationAgent"

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "idea_generation"
        cross_domain_hints = CrossDomainSearchTool().search(state.research_domain, state.research_trends)
        candidates = InnovationMethodTool().generate(
            state.research_domain,
            state.research_trends,
            state.research_gaps,
            state.seed_ideas,
            cross_domain_hints,
            state.request.top_k,
            state.request.normalized_mode(),
        )
        state.candidate_innovations = candidates
        self.trace(
            state,
            "完成候选创新点发散生成。",
            candidates=len(candidates),
            method_types=sorted({item.get("method_type") for item in candidates}),
        )
        return state
