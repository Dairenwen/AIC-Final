from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..models import Document
from ..utils import (
    clamp,
    document_text,
    expand_keywords,
    extract_keyphrases,
    jaccard_similarity,
    parse_time_range,
    score_document,
    stable_id,
    tokenize,
    unique_keep_order,
    year_in_range,
)


class LiteratureSearchTool:
    """Local corpus retrieval over Papers with Code JSON artifacts."""

    def __init__(self, corpus_dir: str | Path, max_documents: int = 80) -> None:
        self.corpus_dir = Path(corpus_dir)
        self.max_documents = max_documents

    def load_documents(self) -> list[Document]:
        if not self.corpus_dir.exists():
            return []
        docs: list[Document] = []
        for path in sorted(self.corpus_dir.glob("*.json")):
            if path.name == "_index.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict) and isinstance(payload.get("papers"), list):
                records = payload["papers"]
            else:
                continue
            for record in records:
                if isinstance(record, dict) and record.get("title"):
                    docs.append(Document.from_record(record, str(path)))
        return docs

    def search(
        self,
        research_domain: str,
        keywords: list[str] | None = None,
        time_range: str | None = None,
    ) -> tuple[list[Document], dict[str, Any]]:
        terms = expand_keywords(research_domain, keywords)
        start_year, end_year = parse_time_range(time_range)
        scored: list[tuple[float, Document, list[str]]] = []
        all_docs = self.load_documents()
        for doc in all_docs:
            if not year_in_range(doc.publish_year, start_year, end_year):
                continue
            score, hits = score_document(doc, terms)
            if score > 0:
                scored.append((score, doc, hits))

        if not scored:
            fallback = [doc for doc in all_docs if year_in_range(doc.publish_year, start_year, end_year)]
            fallback.sort(key=lambda doc: (doc.publish_year or 0, doc.title), reverse=True)
            docs = fallback[: self.max_documents]
            return docs, {
                "query_terms": terms,
                "total_documents": len(all_docs),
                "matched_documents": 0,
                "fallback": "latest_documents",
            }

        scored.sort(key=lambda item: (item[0], item[1].publish_year or 0), reverse=True)
        docs = [doc for _, doc, _ in scored[: self.max_documents]]
        hit_counter: Counter[str] = Counter()
        for _, _, hits in scored[: self.max_documents]:
            hit_counter.update(hits)
        return docs, {
            "query_terms": terms,
            "total_documents": len(all_docs),
            "matched_documents": len(scored),
            "top_hit_terms": [term for term, _ in hit_counter.most_common(16)],
            "time_range": time_range,
        }


class ClusteringTool:
    def cluster(self, docs: list[Document], query_terms: list[str], limit: int = 8) -> list[dict[str, Any]]:
        phrases = extract_keyphrases(docs, query_terms, limit=limit * 2)
        clusters: list[dict[str, Any]] = []
        for index, phrase in enumerate(phrases[: limit], 1):
            members = [
                doc
                for doc in docs
                if phrase.lower() in document_text(doc).lower()
                or any(phrase.lower() in keyword.lower() for keyword in doc.key_words)
            ]
            if not members:
                continue
            clusters.append(
                {
                    "id": f"CLUSTER-{index:02d}",
                    "name": phrase,
                    "size": len(members),
                    "representative_docs": [doc.id for doc in members[:5]],
                    "summary": f"围绕 {phrase} 的文献簇，共 {len(members)} 篇候选文献。",
                }
            )
        return clusters


class KnowledgeGraphTool:
    def build(self, docs: list[Document], query_terms: list[str]) -> dict[str, Any]:
        concepts = extract_keyphrases(docs, query_terms, limit=28)
        pair_counter: Counter[tuple[str, str]] = Counter()
        for doc in docs:
            doc_terms = unique_keep_order(
                list(doc.key_words[:6]) + [term for term in tokenize(doc.title) if len(term) > 3][:8]
            )
            for i, left in enumerate(doc_terms):
                for right in doc_terms[i + 1 : i + 4]:
                    if left.lower() != right.lower():
                        pair_counter[tuple(sorted((left, right), key=str.lower))] += 1

        relations = [
            {"source": left, "target": right, "weight": weight}
            for (left, right), weight in pair_counter.most_common(40)
        ]
        summary = "、".join(concepts[:10]) if concepts else "暂无可用概念"
        return {
            "concepts": concepts,
            "relations": relations,
            "summary": f"知识图谱抽取了 {len(concepts)} 个核心概念，主要围绕：{summary}。",
        }


class CitationNetworkTool:
    def build(self, docs: list[Document]) -> dict[str, Any]:
        nodes = []
        edges = []
        for doc in docs[:80]:
            nodes.append({"id": doc.id, "title": doc.title, "year": doc.publish_year, "venue": doc.publish_venue})
            related = doc.raw.get("related_papers") if isinstance(doc.raw, dict) else []
            if isinstance(related, list):
                for item in related[:5]:
                    target = item.get("id") if isinstance(item, dict) else str(item)
                    if target:
                        edges.append({"source": doc.id, "target": target})
        central = sorted(nodes, key=lambda node: (node.get("year") or 0), reverse=True)[:8]
        return {
            "nodes": nodes,
            "edges": edges,
            "central_references": central,
            "summary": f"引用网络包含 {len(nodes)} 个本地节点和 {len(edges)} 条显式关联。",
        }


class TrendAnalysisTool:
    def analyze(self, docs: list[Document], query_terms: list[str], limit: int = 8) -> list[dict[str, Any]]:
        if not docs:
            return [
                {
                    "id": "TREND-001",
                    "name": "领域文献不足",
                    "signal": "本地语料命中不足，需要扩展文献源。",
                    "evidence_refs": [],
                    "years": {},
                }
            ]

        year_counter: Counter[int] = Counter(doc.publish_year for doc in docs if doc.publish_year)
        max_year = max(year_counter) if year_counter else None
        recent_cut = (max_year - 1) if max_year else None
        term_years: dict[str, Counter[int]] = defaultdict(Counter)
        for doc in docs:
            terms = set(extract_keyphrases([doc], query_terms, limit=12))
            for term in terms:
                if doc.publish_year:
                    term_years[term][doc.publish_year] += 1

        scored_terms: list[tuple[float, str, int, int]] = []
        for term, years in term_years.items():
            recent = sum(count for year, count in years.items() if recent_cut is None or year >= recent_cut)
            older = sum(count for year, count in years.items() if recent_cut is not None and year < recent_cut)
            score = recent * 1.5 + recent / (older + 1)
            scored_terms.append((score, term, recent, older))
        scored_terms.sort(reverse=True)

        trends: list[dict[str, Any]] = []
        for index, (_, term, recent, older) in enumerate(scored_terms[:limit], 1):
            evidence_docs = [
                doc
                for doc in docs
                if term.lower() in document_text(doc).lower()
                or any(term.lower() in keyword.lower() for keyword in doc.key_words)
            ][:5]
            trend_years = Counter(doc.publish_year for doc in evidence_docs if doc.publish_year)
            trends.append(
                {
                    "id": f"TREND-{index:03d}",
                    "name": term,
                    "signal": f"近年命中 {recent} 次，历史命中 {older} 次，说明该方向仍在升温或保持活跃。",
                    "evidence_refs": [doc.id for doc in evidence_docs],
                    "years": dict(sorted(trend_years.items())),
                    "related_keywords": extract_keyphrases(evidence_docs, [term], limit=8),
                }
            )
        return trends


class RAGRetrievalTool:
    def retrieve(self, docs: list[Document], query: str, limit: int = 5) -> list[Document]:
        terms = expand_keywords(query, tokenize(query))
        scored = []
        for doc in docs:
            score, _ = score_document(doc, terms)
            scored.append((score, doc))
        scored.sort(key=lambda item: (item[0], item[1].publish_year or 0), reverse=True)
        return [doc for score, doc in scored[:limit] if score > 0] or docs[:limit]


class CrossDomainSearchTool:
    CROSS_DOMAIN_PATTERNS = [
        ("软件工程", "把持续集成、回归测试和故障定位思想迁移到科研假设验证。"),
        ("网络安全", "用红队攻击面建模和风险闭环来发现鲁棒性空白。"),
        ("医学循证", "借鉴证据等级、对照实验和可重复审计机制。"),
        ("知识工程", "用概念图谱、稀疏桥接和本体约束减少伪创新。"),
        ("人机交互", "把用户反馈、可解释性和任务流摩擦纳入创新评估。"),
    ]

    def search(self, domain: str, trends: list[dict[str, Any]], limit: int = 3) -> list[dict[str, str]]:
        anchors = ", ".join(item.get("name", "") for item in trends[:3]) or domain
        return [
            {"domain": name, "transfer_hint": f"{hint} 可与 {anchors} 组合。"}
            for name, hint in self.CROSS_DOMAIN_PATTERNS[:limit]
        ]


class InnovationMethodTool:
    METHOD_TYPES = ["组合式创新", "迁移式创新", "矛盾消解(TRIZ)", "空白填补", "假设驱动", "问题重构"]

    def generate(
        self,
        research_domain: str,
        trends: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
        seed_ideas: list[str],
        cross_domain_hints: list[dict[str, str]],
        top_k: int,
        mode: str,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        target_count = max(top_k * 3, 8)

        for seed in seed_ideas:
            candidates.append(
                self._candidate(
                    research_domain,
                    method_type="假设驱动",
                    title=f"围绕“{seed}”的可验证创新路线",
                    problem=f"现有 {research_domain} 研究中，{seed} 仍缺少清晰的假设拆解、证据边界和验证闭环。",
                    evidence_refs=[],
                    anchor=seed,
                )
            )

        if mode != "evaluate":
            for gap in gaps[: max(3, top_k)]:
                candidates.append(
                    self._candidate(
                        research_domain,
                        method_type="空白填补",
                        title=f"{gap['title']}的证据驱动方案",
                        problem=gap.get("description", ""),
                        evidence_refs=gap.get("evidence_refs", []),
                        anchor=", ".join(gap.get("related_keywords", [])[:3]) or gap.get("title", ""),
                        source_gap_id=gap.get("id"),
                    )
                )

            if len(trends) >= 2:
                left, right = trends[0], trends[1]
                candidates.append(
                    self._candidate(
                        research_domain,
                        method_type="组合式创新",
                        title=f"{left['name']} × {right['name']} 的融合研究框架",
                        problem=f"{research_domain} 中的 {left['name']} 与 {right['name']} 往往被分开研究，缺少统一建模和共同评测。",
                        evidence_refs=unique_keep_order(left.get("evidence_refs", []) + right.get("evidence_refs", [])),
                        anchor=f"{left['name']} + {right['name']}",
                        source_trend_ids=[left.get("id"), right.get("id")],
                    )
                )

            for hint in cross_domain_hints:
                candidates.append(
                    self._candidate(
                        research_domain,
                        method_type="迁移式创新",
                        title=f"从{hint['domain']}迁移到{research_domain}的创新范式",
                        problem=hint["transfer_hint"],
                        evidence_refs=trends[0].get("evidence_refs", []) if trends else [],
                        anchor=hint["domain"],
                    )
                )

            candidates.append(
                self._candidate(
                    research_domain,
                    method_type="矛盾消解(TRIZ)",
                    title=f"{research_domain}中性能、成本与可信度的矛盾消解机制",
                    problem=f"该方向通常同时追求更强能力、更低数据/算力成本和更高可信度，三者之间存在明显张力。",
                    evidence_refs=unique_keep_order(
                        [ref for trend in trends[:3] for ref in trend.get("evidence_refs", [])]
                    ),
                    anchor="性能-成本-可信度",
                )
            )

            candidates.append(
                self._candidate(
                    research_domain,
                    method_type="问题重构",
                    title=f"将{research_domain}重构为证据链闭环优化问题",
                    problem="把单点模型效果提升转化为“证据检索-假设生成-风险评估-验证反馈”的闭环优化。",
                    evidence_refs=unique_keep_order(
                        [ref for gap in gaps[:3] for ref in gap.get("evidence_refs", [])]
                    ),
                    anchor="闭环优化",
                )
            )

        unique_candidates: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for index, candidate in enumerate(candidates, 1):
            if candidate["title"] in seen_titles:
                continue
            seen_titles.add(candidate["title"])
            candidate["id"] = stable_id("INNO", candidate["title"], index)
            unique_candidates.append(candidate)
            if len(unique_candidates) >= target_count:
                break
        return unique_candidates

    def _candidate(
        self,
        research_domain: str,
        method_type: str,
        title: str,
        problem: str,
        evidence_refs: list[str],
        anchor: str,
        source_gap_id: str | None = None,
        source_trend_ids: list[str | None] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": "",
            "title": title,
            "description": f"以 {anchor} 为切入点，在 {research_domain} 中形成可验证、可追溯的创新点。",
            "method_type": method_type,
            "research_question": f"如何在 {research_domain} 中针对 {anchor} 构建更可靠且可验证的研究方案？",
            "hypothesis": f"如果将 {anchor} 与证据检索、结构化评估和反馈迭代结合，可以获得比单点方法更稳健的研究贡献。",
            "proposed_approach": (
                "1) 建立领域文献与概念图谱；2) 识别高频趋势和低连接空白；"
                "3) 生成候选技术路线；4) 用新颖性、可行性、影响力和风险四维评分筛选。"
            ),
            "expected_contribution": f"形成面向 {research_domain} 的新问题定义、方法框架和可复现实验协议。",
            "validation_plan": "采用公开语料/基准复现实验、消融分析、跨场景泛化测试和人工审阅相结合的方式验证。",
            "source_evidence": unique_keep_order(evidence_refs)[:8],
            "source_gap_id": source_gap_id,
            "source_trend_ids": [item for item in (source_trend_ids or []) if item],
            "keywords": unique_keep_order(tokenize(research_domain) + tokenize(anchor))[:12],
            "rationale": problem,
        }


class NoveltyDetectionTool:
    def score(self, candidate: dict[str, Any], docs: list[Document]) -> tuple[float, dict[str, Any]]:
        if not docs:
            return 0.62, {"max_overlap": 0.0, "nearest_reference": None}
        text = " ".join(
            [
                candidate.get("title", ""),
                candidate.get("description", ""),
                candidate.get("research_question", ""),
                candidate.get("hypothesis", ""),
            ]
        )
        similarities = [(jaccard_similarity(text, f"{doc.title} {doc.abstract[:400]}"), doc) for doc in docs[:80]]
        max_similarity, nearest = max(similarities, key=lambda item: item[0])
        method_bonus = 0.05 if candidate.get("method_type") in {"组合式创新", "迁移式创新", "矛盾消解(TRIZ)"} else 0.02
        novelty = clamp(0.55 + (1 - max_similarity) * 0.35 + method_bonus)
        return novelty, {
            "max_overlap": round(max_similarity, 3),
            "nearest_reference": nearest.id if nearest else None,
            "interpretation": "分数由候选点与已有文献的词汇重叠反向估计，并按创新方法类型加权。",
        }


class FeasibilityAssessTool:
    def score(self, candidate: dict[str, Any], docs: list[Document]) -> tuple[float, dict[str, Any]]:
        evidence_ids = set(candidate.get("source_evidence", []))
        evidence_docs = [doc for doc in docs if doc.id in evidence_ids]
        github_count = sum(1 for doc in evidence_docs if doc.github_url)
        score = 0.58 + min(len(evidence_docs), 5) * 0.045 + min(github_count, 3) * 0.035
        if "基准" in candidate.get("validation_plan", "") or "benchmark" in candidate.get("validation_plan", "").lower():
            score += 0.04
        if candidate.get("method_type") == "迁移式创新":
            score -= 0.04
        return clamp(score), {
            "evidence_count": len(evidence_docs),
            "open_code_references": github_count,
            "interpretation": "证据越充分、可复现实验资源越多，可行性越高。",
        }


class ImpactEstimationTool:
    def score(self, candidate: dict[str, Any], trends: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> tuple[float, float, dict[str, Any]]:
        trend_refs = set(ref for trend in trends[:4] for ref in trend.get("evidence_refs", []))
        evidence_refs = set(candidate.get("source_evidence", []))
        trend_overlap = len(trend_refs & evidence_refs)
        gap_bonus = 0.06 if candidate.get("source_gap_id") else 0.0
        impact = clamp(0.56 + min(trend_overlap, 5) * 0.045 + gap_bonus)
        risk = 0.22
        if len(evidence_refs) < 2:
            risk += 0.12
        if candidate.get("method_type") in {"迁移式创新", "矛盾消解(TRIZ)"}:
            risk += 0.06
        if "人工审阅" in candidate.get("validation_plan", ""):
            risk -= 0.03
        return impact, clamp(risk), {
            "trend_overlap": trend_overlap,
            "gap_bonus": gap_bonus,
            "risk_drivers": ["证据不足"] if len(evidence_refs) < 2 else ["跨域落地难度", "验证成本"],
        }


class EvidenceBindingTool:
    def bind(self, candidates: list[dict[str, Any]], docs: list[Document]) -> tuple[dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
        doc_map = {doc.id: doc for doc in docs}
        evidence_map: dict[str, list[str]] = {}
        evidence_payload: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            refs = unique_keep_order(candidate.get("source_evidence", []))[:8]
            if not refs:
                refs = [doc.id for doc in docs[:3]]
            evidence_map[candidate["id"]] = refs
            evidence_payload[candidate["id"]] = [
                doc_map[ref].evidence_dict() for ref in refs if ref in doc_map
            ]
        return evidence_map, evidence_payload
