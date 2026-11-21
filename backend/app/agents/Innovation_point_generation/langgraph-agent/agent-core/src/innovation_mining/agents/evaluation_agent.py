from __future__ import annotations

from ..models import InnovationState
from ..tools import FeasibilityAssessTool, ImpactEstimationTool, NoveltyDetectionTool
from ..utils import clamp
from .base_agent import BaseAgent


class InnovationEvaluationAgent(BaseAgent):
    name = "InnovationEvaluationAgent"

    DEFAULT_WEIGHTS = {"novelty": 0.35, "feasibility": 0.25, "impact": 0.30, "risk": 0.10}

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "innovation_evaluation"
        weights = dict(self.DEFAULT_WEIGHTS)
        weights.update(state.constraints.get("score_weights", {}) if isinstance(state.constraints, dict) else {})

        novelty_tool = NoveltyDetectionTool()
        feasibility_tool = FeasibilityAssessTool()
        impact_tool = ImpactEstimationTool()
        evaluated = []
        for candidate in state.candidate_innovations:
            novelty, novelty_detail = novelty_tool.score(candidate, state.literature_corpus)
            feasibility, feasibility_detail = feasibility_tool.score(candidate, state.literature_corpus)
            impact, risk, impact_detail = impact_tool.score(candidate, state.research_trends, state.research_gaps)
            overall = (
                weights["novelty"] * novelty
                + weights["feasibility"] * feasibility
                + weights["impact"] * impact
                - weights["risk"] * risk
            )
            scored = dict(candidate)
            scored["scores"] = {
                "novelty": round(clamp(novelty), 3),
                "feasibility": round(clamp(feasibility), 3),
                "impact": round(clamp(impact), 3),
                "risk": round(clamp(risk), 3),
            }
            scored["overall_score"] = round(clamp(overall), 3)
            scored["evaluation_detail"] = {
                "novelty": novelty_detail,
                "feasibility": feasibility_detail,
                "impact_and_risk": impact_detail,
                "weights": weights,
            }
            evaluated.append(scored)

        evaluated.sort(key=lambda item: item["overall_score"], reverse=True)
        for rank, item in enumerate(evaluated, 1):
            item["rank"] = rank
        state.evaluated_innovations = evaluated

        top = evaluated[: state.request.top_k]
        method_diversity = len({item.get("method_type") for item in top})
        min_score = float(state.constraints.get("min_score", 0.58)) if isinstance(state.constraints, dict) else 0.58
        if top and (top[0]["overall_score"] < min_score or method_diversity < min(3, len(top))):
            state.feedback.append("Top-K 质量或方法多样性不足，建议增加文献源或扩大 seed ideas。")
        self.trace(
            state,
            "完成四维评分与排序。",
            evaluated=len(evaluated),
            top_score=top[0]["overall_score"] if top else None,
            method_diversity=method_diversity,
        )
        return state
