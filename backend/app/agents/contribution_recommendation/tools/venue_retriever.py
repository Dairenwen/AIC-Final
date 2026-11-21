"""知识库检索工具 — Elasticsearch BM25 粗筛候选会议/期刊"""
from typing import Optional
from knowledge import get_knowledge_base, WARNING_VENUES
from config import UserPreferences
from utils.logger import get_logger

logger = get_logger(__name__)


async def retrieve_candidate_venues(paper_features: dict,
                                     user_prefs: Optional[dict] = None,
                                     top_k: int = 150) -> list[dict]:
    kb = get_knowledge_base()
    prefs = UserPreferences(**(user_prefs or {}))
    sub_fields = paper_features.get("sub_fields", [])
    key_techniques = paper_features.get("key_techniques", [])
    all_keywords = list(set(sub_fields + key_techniques))

    area_results = kb.search_by_area(
        research_areas=sub_fields,
        ccf_levels=prefs.target_ccf_levels if prefs.target_ccf_levels else None, top_k=top_k)
    kw_results = kb.search_by_keyword(
        keywords=all_keywords,
        ccf_levels=prefs.target_ccf_levels if prefs.target_ccf_levels else None, top_k=top_k)

    seen = set()
    merged = []
    for v in area_results + kw_results:
        if v["abbreviation"] not in seen:
            seen.add(v["abbreviation"])
            merged.append(v)

    # 剔除预警期刊 + 用户排除列表
    excluded = set(v.upper() for v in prefs.excluded_venues) | WARNING_VENUES
    if excluded:
        before = len(merged)
        merged = [v for v in merged if v["abbreviation"].upper() not in excluded]
        if before > len(merged):
            logger.info(f"已剔除 {before - len(merged)} 个预警/排除期刊")

    merged = kb.filter_by_deadline(merged)

    level_rank = {"CCF-A": 1.0, "CCF-B": 0.8, "CCF-C": 0.5, "": 0.3}
    for v in merged:
        bm25 = v.pop("_bm25_score", 0.5)
        lr = level_rank.get(v.get("ccf_level", ""), 0.3)
        v["_rank_score"] = bm25 * 0.6 + lr * 0.4
    merged.sort(key=lambda x: x.get("_rank_score", 0), reverse=True)
    result = merged[:top_k]
    for v in result:
        v.pop("_rank_score", None)

    logger.info(f"候选检索完成: {len(result)} 个候选 venues")
    return result
