"""引用耦合分析工具"""
from collections import Counter
from utils.logger import get_logger

logger = get_logger(__name__)


async def analyze_citation_coupling(parsed_paper: dict, candidate_venues: list[dict]) -> dict:
    references = parsed_paper.get("references", [])
    if not references:
        logger.warning("论文无参考文献数据")
        return {"coupling_scores": {}, "reference_distribution": {}, "total_references": 0}

    venue_counter = Counter()
    for ref in references:
        venue = ref.get("venue", "") or ref.get("journal", "") or ref.get("conference", "")
        if venue:
            venue_counter[venue] += 1

    total_refs = len(references)
    candidate_abbrs = {v["abbreviation"] for v in candidate_venues}
    coupling_scores = {}
    for abbr in candidate_abbrs:
        count = venue_counter.get(abbr, 0)
        if count == 0:
            for vn, cnt in venue_counter.items():
                if abbr.lower() in vn.lower() or vn.lower() in abbr.lower():
                    count += cnt
        coupling_scores[abbr] = round(count / max(total_refs, 1), 4)

    top_venues = venue_counter.most_common(10)
    logger.info(f"引用耦合分析完成: {total_refs} 篇参考文献, Top3: {top_venues[:3]}")
    return {
        "coupling_scores": coupling_scores,
        "reference_distribution": {v: c for v, c in top_venues},
        "total_references": total_refs,
        "top_cited_venues": [{"venue": v, "count": c} for v, c in top_venues[:5]],
    }
