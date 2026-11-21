"""接收率预估工具 — 综合估计录用概率区间"""
import asyncio
from typing import Optional
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

PROMPT = """估计以下论文在目标会议/期刊中的录用概率。

## 论文
- 创新层次: {novelty_level}
- 实验完整度: {experiment_completeness}
- 方法匹配度: {methodology_alignment}
- 主题匹配度: {topic_similarity}

## 目标
{venue_info}

## 竞争分析
{competition_analysis}

输出 JSON：
```json
{{"estimated_probability_range":"低/中等偏低/中等/中等偏高/高","probability_low":0.0,"probability_high":0.0,"key_factors":["因素"],"confidence":0.0-1.0,"advice":"投稿建议"}}
```"""


async def estimate_acceptance(venue: dict, match_score: dict, paper_features: dict,
                               competition_analysis: dict, model: Optional[str] = None) -> dict:
    ms = get_model_service()
    prompt = PROMPT.format(
        novelty_level=paper_features.get("novelty_level", "unknown"),
        experiment_completeness=paper_features.get("experiment_completeness", 0.5),
        methodology_alignment=match_score.get("methodology_alignment", 0.5),
        topic_similarity=match_score.get("topic_similarity", 0.5),
        venue_info=to_json(venue), competition_analysis=to_json(competition_analysis))
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: ms.chat(messages=[
                {"role": "system", "content": "请严格按 JSON 格式输出。"},
                {"role": "user", "content": prompt},
            ], model=model, temperature=0.3, json_mode=True))
        result = safe_json_parse(response) or {}
    except Exception:
        hist_rate = venue.get("acceptance_rate", 0.25)
        adj = match_score.get("overall", 0.5) - 0.5
        prob = max(0.05, min(0.6, hist_rate + adj * 0.3))
        result = {"estimated_probability_range": "中等", "probability_low": max(0.05, prob - 0.1),
                  "probability_high": min(0.6, prob + 0.1), "key_factors": ["基于历史数据估算"], "confidence": 0.5}
    return {"venue": venue.get("abbreviation", ""),
            "estimated_probability_range": result.get("estimated_probability_range", "中等"),
            "probability_low": result.get("probability_low", 0.1),
            "probability_high": result.get("probability_high", 0.3),
            "key_factors": result.get("key_factors", []),
            "confidence": result.get("confidence", 0.6), "advice": result.get("advice", "")}
