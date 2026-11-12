from __future__ import annotations

from ..models import InnovationState
from ..tools import TrendAnalysisTool
from .base_agent import BaseAgent


class TrendAnalysisAgent(BaseAgent):
    name = "TrendAnalysisAgent"

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "trend_analysis"
        terms = state.metadata.get("literature_search", {}).get("top_hit_terms") or state.request.keywords
        state.research_trends = TrendAnalysisTool().analyze(state.literature_corpus, terms, limit=8)
        self.trace(state, "完成研究趋势统计。", trends=len(state.research_trends))
        return state
