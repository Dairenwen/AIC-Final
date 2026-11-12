"""趋势分析工具 — 分析近2年发文主题趋势、新兴方向"""
import asyncio
from typing import Optional
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

PROMPT = """分析以下会议/期刊的近年发文趋势。

## 目标会议/期刊
{venue_info}

## 论文主题特征
{paper_features}

输出 JSON：
```json
{{"hot_topics":["热门方向"],"emerging_topics":["新兴方向"],"trend_fit_score":0.0-1.0,"trend_analysis":"趋势分析摘要"}}
```"""


async def analyze_trends(venue: dict, paper_features: dict, model: Optional[str] = None) -> dict:
    ms = get_model_service()
    prompt = PROMPT.format(venue_info=to_json(venue), paper_features=to_json(paper_features))
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: ms.chat(messages=[
                {"role": "system", "content": "请严格按 JSON 格式输出。"},
                {"role": "user", "content": prompt},
            ], model=model, temperature=0.3, json_mode=True))
        result = safe_json_parse(response) or {}
    except Exception:
        result = {}
    return {"venue": venue.get("abbreviation", ""), "hot_topics": result.get("hot_topics", []),
            "emerging_topics": result.get("emerging_topics", []),
            "trend_fit_score": result.get("trend_fit_score", 0.5),
            "trend_analysis": result.get("trend_analysis", "")}


async def batch_analyze_trends(venues: list[dict], paper_features: dict,
                                model: Optional[str] = None) -> list[dict]:
    import asyncio
    tasks = [analyze_trends(venue, paper_features, model) for venue in venues]
    results = await asyncio.gather(*tasks)
    logger.info(f"趋势分析并行完成: {len(results)} 个 venues")
    return results
