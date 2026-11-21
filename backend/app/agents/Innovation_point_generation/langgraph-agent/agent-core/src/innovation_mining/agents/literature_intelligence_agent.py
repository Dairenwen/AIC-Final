from __future__ import annotations

from ..models import InnovationState
from ..tools import CitationNetworkTool, ClusteringTool, KnowledgeGraphTool, LiteratureSearchTool
from .base_agent import BaseAgent


class LiteratureIntelligenceAgent(BaseAgent):
    name = "LiteratureIntelligenceAgent"

    def run(self, state: InnovationState) -> InnovationState:
        state.current_step = "literature_intelligence"
        search_tool = LiteratureSearchTool(state.request.corpus_dir, max_documents=state.request.max_documents)
        docs, stats = search_tool.search(
            state.research_domain,
            state.request.keywords,
            state.request.time_range,
        )
        state.literature_corpus = docs
        state.metadata["literature_search"] = stats

        query_terms = stats.get("top_hit_terms") or stats.get("query_terms") or state.request.keywords
        state.metadata["clusters"] = ClusteringTool().cluster(docs, query_terms)
        state.knowledge_graph = KnowledgeGraphTool().build(docs, query_terms)
        state.citation_network = CitationNetworkTool().build(docs)
        self.trace(
            state,
            "完成本地文献检索、主题聚类、知识图谱和引用网络构建。",
            documents=len(docs),
            matched_documents=stats.get("matched_documents", 0),
        )
        return state
