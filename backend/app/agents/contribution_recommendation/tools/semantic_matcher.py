"""语义匹配工具 — BGE-M3 嵌入 + bge-reranker 精排"""
from typing import Optional
import numpy as np
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

MATCH_PROMPT = """评估以下论文与目标会议/期刊的匹配度。

## 论文特征
{paper_features}

## 目标会议/期刊
{venue_info}

## 评估维度
1. topic_similarity: 论文主题与发文主题的重叠程度
2. methodology_alignment: 方法范式与偏好契合程度
3. experiment_completeness_fit: 实验完整度是否达发表标准
4. novelty_level_fit: 创新层次匹配

```json
{{"topic_similarity":0.0-1.0,"methodology_alignment":0.0-1.0,"experiment_completeness_fit":0.0-1.0,"novelty_level_fit":0.0-1.0,"reasoning":"分析摘要"}}
```"""


async def compute_semantic_match(paper_features: dict, candidate_venues: list[dict],
                                  model: Optional[str] = None,
                                  similarity_threshold: float = 0.65,
                                  top_k: int = 30) -> list[dict]:
    ms = get_model_service()
    paper_text = _build_paper_text(paper_features)

    # 尝试向量嵌入粗筛，不可用时跳过
    top_indices = list(range(len(candidate_venues)))  # 默认全量
    try:
        paper_emb = ms.embed([paper_text])[0]
        venue_texts = [_build_venue_text(v) for v in candidate_venues]
        venue_embs = ms.embed(venue_texts)
        paper_vec = np.array(paper_emb)
        rough_scores = []
        for i, v_emb in enumerate(venue_embs):
            v_vec = np.array(v_emb)
            cosine = float(np.dot(paper_vec, v_vec) / (np.linalg.norm(paper_vec) * np.linalg.norm(v_vec) + 1e-8))
            rough_scores.append((i, cosine))
        rough_scores.sort(key=lambda x: x[1], reverse=True)
        top_indices = [i for i, s in rough_scores[:top_k * 2] if s > similarity_threshold]
        logger.info(f"向量粗筛: {len(top_indices)}/{len(candidate_venues)} 通过阈值")
    except Exception as e:
        logger.warning(f"嵌入模型不可用，跳过向量粗筛，直接使用 LLM 匹配: {e}")

    # LLM 并行评估匹配度（所有 venue 同时发出请求）
    import asyncio

    async def _eval_one(venue, idx):
        prompt = MATCH_PROMPT.format(paper_features=to_json(paper_features), venue_info=to_json(venue))
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: ms.chat(messages=[
                    {"role": "system", "content": "请严格按 JSON 格式输出。"},
                    {"role": "user", "content": prompt},
                ], model=model, temperature=0.2, json_mode=True))
            match = safe_json_parse(response) or {}
        except Exception:
            match = {}
        overall = (match.get("topic_similarity", 0.5) * 0.35 + match.get("methodology_alignment", 0.5) * 0.25 +
                   match.get("experiment_completeness_fit", 0.5) * 0.15 + match.get("novelty_level_fit", 0.5) * 0.25)
        return {**venue, "match_score": {
            "overall": round(overall, 4), "topic_similarity": round(match.get("topic_similarity", 0.5), 4),
            "methodology_alignment": round(match.get("methodology_alignment", 0.5), 4),
            "experiment_completeness_fit": round(match.get("experiment_completeness_fit", 0.5), 4),
            "novelty_level_fit": round(match.get("novelty_level_fit", 0.5), 4),
            "citation_coupling": 0.0,
        }}

    tasks = [_eval_one(candidate_venues[idx], idx) for idx in top_indices[:top_k]]
    results = await asyncio.gather(*tasks)
    logger.info(f"语义匹配并行完成: {len(results)} 个候选")

    # bge-reranker 精排
    rerank_docs = [f"[{v.get('ccf_level', '')}] {v.get('abbreviation', '')}: {v.get('full_name', '')}"
                   for v in results]
    try:
        reranked = ms.rerank(paper_text, rerank_docs, top_k=top_k)
        rerank_map = {r["index"]: r["score"] for r in reranked}
        for i, r in enumerate(results):
            if i in rerank_map:
                r["match_score"]["overall"] = round(r["match_score"]["overall"] * 0.6 + rerank_map[i] * 0.4, 4)
        results.sort(key=lambda x: x["match_score"]["overall"], reverse=True)
    except Exception as e:
        logger.warning(f"重排失败: {e}")
        results.sort(key=lambda x: x["match_score"]["overall"], reverse=True)

    logger.info(f"语义匹配完成: {len(results)} 个候选已精排")
    return results[:top_k]


def _build_paper_text(features: dict) -> str:
    parts = []
    if features.get("sub_fields"): parts.append("研究领域: " + ", ".join(features["sub_fields"]))
    if features.get("methodology_paradigm"): parts.append("方法: " + features["methodology_paradigm"])
    if features.get("key_techniques"): parts.append("技术: " + ", ".join(features["key_techniques"]))
    if features.get("innovation_summary"): parts.append("创新: " + features["innovation_summary"])
    return "; ".join(parts)


def _build_venue_text(venue: dict) -> str:
    parts = [venue.get("full_name", "")]
    parts.append("领域: " + ", ".join(venue.get("research_areas", [])))
    if venue.get("aims_scope"): parts.append("范围: " + venue["aims_scope"])
    return "; ".join(parts)
