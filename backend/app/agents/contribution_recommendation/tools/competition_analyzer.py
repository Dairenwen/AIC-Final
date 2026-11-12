"""竞争度分析工具 — 检索同子领域论文，评估差异化空间"""
import asyncio
from typing import Optional
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

PROMPT = """评估以下论文在目标会议/期刊中的竞争态势。

## 论文特征
{paper_features}

## 目标会议/期刊
{venue_info}

## 质量估计
{quality_estimate}

输出 JSON：
```json
{{"differentiation_score":0.0-1.0,"experiment_benchmark":0.0-1.0,"novelty_uniqueness":0.0-1.0,"overall_competitiveness":0.0-1.0,"strengths_vs_peers":["优势"],"weaknesses_vs_peers":["劣势"],"analysis_summary":"分析摘要"}}
```"""


async def analyze_competition(venue: dict, paper_features: dict, quality_estimate: dict,
                               model: Optional[str] = None) -> dict:
    ms = get_model_service()
    prompt = PROMPT.format(paper_features=to_json(paper_features),
                           venue_info=to_json(venue), quality_estimate=to_json(quality_estimate))
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
    return {"venue": venue.get("abbreviation", ""),
            "differentiation_score": result.get("differentiation_score", 0.5),
            "experiment_benchmark": result.get("experiment_benchmark", 0.5),
            "novelty_uniqueness": result.get("novelty_uniqueness", 0.5),
            "overall_competitiveness": result.get("overall_competitiveness", 0.5),
            "strengths_vs_peers": result.get("strengths_vs_peers", []),
            "weaknesses_vs_peers": result.get("weaknesses_vs_peers", []),
            "analysis_summary": result.get("analysis_summary", "")}


async def batch_analyze_competition(venues: list[dict], paper_features: dict,
                                     quality_estimate: dict,
                                     model: Optional[str] = None) -> list[dict]:
    import asyncio
    tasks = [analyze_competition(venue, paper_features, quality_estimate, model) for venue in venues]
    results = await asyncio.gather(*tasks)
    logger.info(f"竞争分析并行完成: {len(results)} 个 venues")
    return results
