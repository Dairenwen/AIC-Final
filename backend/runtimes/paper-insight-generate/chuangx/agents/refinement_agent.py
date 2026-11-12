from __future__ import annotations

from ..models import InnovationState
from ..tools import EvidenceBindingTool
from ..utils import extract_keyphrases, unique_keep_order
from .base_agent import BaseAgent


class RefinementAgent(BaseAgent):
    name = "RefinementAgent"

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "refinement"
        top_items = state.evaluated_innovations[: state.request.top_k]
        evidence_map, evidence_payload = EvidenceBindingTool().bind(top_items, state.literature_corpus)
        state.evidence_map = evidence_map

        proposals = []
        fallback_keywords = extract_keyphrases(state.literature_corpus[:12], state.request.keywords, limit=10)
        for item in top_items:
            evidence = evidence_payload.get(item["id"], [])
            keywords = unique_keep_order(item.get("keywords", []) + fallback_keywords)[:10]
            proposal = {
                "innovation_id": item["id"],
                "rank": item["rank"],
                "title": item["title"],
                "summary": item["description"],
                "method_type": item["method_type"],
                "research_question": item["research_question"],
                "hypothesis": item["hypothesis"],
                "problem": item["rationale"],
                "method_route": item["proposed_approach"],
                "expected_contribution": item["expected_contribution"],
                "validation_plan": item["validation_plan"],
                "scores": item["scores"],
                "overall_score": item["overall_score"],
                "evidence": evidence,
                "evidence_refs": evidence_map.get(item["id"], []),
                "anti_hallucination_note": "该创新点已绑定本地文献证据；后续写作时应优先引用 evidence 中的 source_url/pdf_url。",
                "downstream_wengao_inputs": {
                    "topic": item["title"],
                    "contributions": [item["expected_contribution"]],
                    "keywords": keywords,
                    "additional_context": (
                        f"{item['proposed_approach']}\n\n研究问题：{item['research_question']}\n"
                        f"科学假设：{item['hypothesis']}\n验证方案：{item['validation_plan']}"
                    ),
                },
            }
            proposals.append(proposal)

        state.refined_proposals = proposals
        self.trace(state, "完成 Top-K 创新点精炼和 wengao 对接字段生成。", proposals=len(proposals))
        return state
