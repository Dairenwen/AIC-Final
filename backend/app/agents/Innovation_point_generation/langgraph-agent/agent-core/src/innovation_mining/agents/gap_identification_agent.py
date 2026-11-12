from __future__ import annotations

from collections import Counter

from ..models import InnovationState
from ..utils import document_text, extract_keyphrases, stable_id, unique_keep_order
from .base_agent import BaseAgent


class GapIdentificationAgent(BaseAgent):
    name = "GapIdentificationAgent"

    GAP_PATTERNS = [
        ("under-explored", "研究不足"),
        ("under explored", "研究不足"),
        ("limited", "能力受限"),
        ("challenge", "关键挑战"),
        ("remain", "仍待解决"),
        ("future work", "未来工作"),
        ("lack", "缺少资源"),
        ("fail", "失败模式"),
        ("robust", "鲁棒性不足"),
        ("safety", "安全性不足"),
        ("risk", "风险暴露"),
        ("uncertain", "不确定性"),
    ]

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "gap_identification"
        gaps: list[dict] = []

        for doc in state.literature_corpus[:60]:
            text = document_text(doc).lower()
            matched_labels = [label for pattern, label in self.GAP_PATTERNS if pattern in text]
            if not matched_labels:
                continue
            keywords = extract_keyphrases([doc], state.request.keywords, limit=8)
            title = f"{matched_labels[0]}：{keywords[0] if keywords else doc.title[:32]}"
            gaps.append(
                {
                    "id": stable_id("GAP", doc.id + title, len(gaps) + 1),
                    "title": title,
                    "description": (
                        f"文献《{doc.title}》暴露出 {matched_labels[0]} 信号，"
                        f"可作为 {state.research_domain} 的候选研究空白。"
                    ),
                    "gap_type": "literature_signal",
                    "confidence": 0.68,
                    "evidence_refs": [doc.id],
                    "related_keywords": keywords,
                }
            )
            if len(gaps) >= 8:
                break

        concept_counter = Counter()
        for trend in state.research_trends[:6]:
            for keyword in trend.get("related_keywords", [])[:4]:
                concept_counter[keyword] += 1
        sparse_clusters = [cluster for cluster in state.metadata.get("clusters", []) if cluster.get("size", 0) <= 3]
        for cluster in sparse_clusters[:4]:
            title = f"稀疏主题桥接：{cluster['name']}"
            gaps.append(
                {
                    "id": stable_id("GAP", cluster["name"], len(gaps) + 1),
                    "title": title,
                    "description": (
                        f"知识图谱中 {cluster['name']} 相关文献较少，适合与高频趋势组合形成跨概念创新。"
                    ),
                    "gap_type": "graph_sparse_bridge",
                    "confidence": 0.61,
                    "evidence_refs": cluster.get("representative_docs", []),
                    "related_keywords": unique_keep_order([cluster["name"]] + [term for term, _ in concept_counter.most_common(4)]),
                }
            )

        if not gaps:
            title = f"{state.research_domain}的证据链与验证闭环不足"
            gaps.append(
                {
                    "id": stable_id("GAP", title, 1),
                    "title": title,
                    "description": "本地语料未发现明确空白信号，优先从证据链、评测协议和可复现性角度构造研究空白。",
                    "gap_type": "fallback_gap",
                    "confidence": 0.52,
                    "evidence_refs": [doc.id for doc in state.literature_corpus[:3]],
                    "related_keywords": extract_keyphrases(state.literature_corpus[:10], state.request.keywords, limit=8),
                }
            )

        state.research_gaps = gaps[:12]
        self.trace(state, "完成研究空白识别。", gaps=len(state.research_gaps))
        return state
