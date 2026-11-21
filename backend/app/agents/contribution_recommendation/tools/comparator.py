"""对比矩阵构建工具 — 多候选多维度横向对比"""
from utils.logger import get_logger

logger = get_logger(__name__)


async def build_comparison_matrix(recommendations: list[dict]) -> dict:
    if not recommendations:
        return {"columns": [], "rows": [], "summary": "无推荐数据"}

    columns = [
        {"key": "venue", "label": "会议/期刊", "type": "text"},
        {"key": "ccf_level", "label": "CCF级别", "type": "category"},
        {"key": "tier", "label": "推荐档位", "type": "category"},
        {"key": "overall_match", "label": "综合匹配度", "type": "score"},
        {"key": "topic_similarity", "label": "主题相似度", "type": "score"},
        {"key": "acceptance_rate", "label": "接收率", "type": "percent"},
        {"key": "avg_review_weeks", "label": "审稿周期(周)", "type": "number"},
        {"key": "estimated_probability", "label": "估计录用概率", "type": "category"},
    ]

    rows = []
    for rec in recommendations:
        venue = rec.get("venue", {})
        ms = rec.get("match_score", {})
        rows.append({
            "venue": venue.get("abbreviation", ""), "ccf_level": venue.get("ccf_level", ""),
            "tier": rec.get("tier", ""), "overall_match": ms.get("overall", 0),
            "topic_similarity": ms.get("topic_similarity", 0),
            "acceptance_rate": venue.get("acceptance_rate", 0),
            "avg_review_weeks": venue.get("avg_review_weeks", 0),
            "estimated_probability": rec.get("estimated_acceptance_prob", ""),
        })

    logger.info(f"对比矩阵构建完成: {len(rows)} 行")
    return {
        "columns": columns, "rows": rows, "total_candidates": len(rows),
        "tier_distribution": {
            "sprint": sum(1 for r in rows if r["tier"] == "sprint"),
            "match": sum(1 for r in rows if r["tier"] == "match"),
            "safety": sum(1 for r in rows if r["tier"] == "safety"),
        },
    }
